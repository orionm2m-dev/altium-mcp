"""Situs button policy and public lock tests; no native calls."""
import ast
import asyncio
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import situs_setup_control as control

TITLE = "ScratchQualification.PcbDoc - Altium Designer"


class SitusTests(unittest.TestCase):
    def setUp(self):
        self.main = {"hwnd": 100, "class": "TDocumentForm", "title": TITLE}
        self.target = Mock(hwnd=42, identity={"pid": 123, "creation_filetime": 456},
            row={"class": "TSitusSetup", "title": "Situs Routing Strategies", "enabled": True},
            window={"handle": 42, "title": "Situs Routing Strategies"})
        self.target.api.windows.side_effect = lambda pid: [copy.deepcopy(self.main)]
        self.target.api.matches.return_value = True
        self.target.api.title.side_effect = lambda hwnd: self.main["title"]
        self.rows = {
            11: {"pid": 123, "thread": 7, "class": "TXPBitBtn", "text": "&OK", "visible": True, "enabled": True},
            12: {"pid": 123, "thread": 7, "class": "TButton", "text": "Cancel", "visible": True, "enabled": True},
            13: {"pid": 999, "thread": 8, "class": "Chrome_WidgetWin_0", "text": "webview", "visible": True, "enabled": True}}
        self.gui = Mock()
        self.gui.EnumChildWindows.side_effect = lambda hwnd, callback, data: [callback(h, data) for h in self.rows]
        self.gui.IsChild.return_value = True
        self.gui.GetClassName.side_effect = lambda hwnd: self.rows[hwnd]["class"]
        self.gui.GetWindowText.side_effect = lambda hwnd: self.rows[hwnd]["text"]
        self.gui.IsWindowVisible.side_effect = lambda hwnd: self.rows[hwnd]["visible"]
        self.gui.IsWindowEnabled.side_effect = lambda hwnd: self.rows[hwnd]["enabled"]
        self.process = SimpleNamespace(GetWindowThreadProcessId=lambda hwnd: [self.rows[hwnd]["thread"], self.rows[hwnd]["pid"]])
        for p in (patch.object(control, "CaptureTarget", return_value=self.target),
                  patch.object(control, "win32gui", self.gui), patch.object(control, "win32process", self.process)):
            p.start()
            self.addCleanup(p.stop)

    def call(self, action="inspect", title=TITLE):
        return control.control("configured.exe", action, 42, title)

    def test_inspect_skips_foreign_descendants_without_input(self):
        result = self.call()
        self.assertTrue(result["success"], result)
        self.assertEqual([b["hwnd"] for b in result["buttons"]], [11, 12])
        self.assertEqual(result["foreign_descendants_skipped"], 1)
        self.assertFalse(result["completion_verified"])
        self.gui.PostMessage.assert_not_called()
        self.target.close.assert_called_once()

    def test_ok_and_cancel_post_exactly_one_message_without_completion_claim(self):
        for action, hwnd in [("ok", 11), ("cancel", 12)]:
            with self.subTest(action=action):
                self.gui.PostMessage.reset_mock()
                result = self.call(action)
                self.assertTrue(result["success"], result)
                self.assertTrue(result["button_message_posted"])
                self.assertFalse(result["completion_verified"])
                self.gui.PostMessage.assert_called_once_with(hwnd, 0xF5, 0, 0)
        self.gui.SetForegroundWindow.assert_not_called()
        self.gui.SendMessage.assert_not_called()

    def test_route_all_requires_observed_live_button(self):
        self.assertTrue(self.call("route_all")["blocked"])
        self.gui.PostMessage.assert_not_called()
        self.rows[14] = dict(self.rows[11], text="Route All")
        result = self.call("route_all")
        self.assertTrue(result["success"], result)
        self.gui.PostMessage.assert_called_once_with(14, 0xF5, 0, 0)

    def test_invalid_action_or_main_title_cannot_post(self):
        for action, title in [("save", TITLE), ("ok", "Home Page"), ("ok", "Other.PcbDoc - Altium Designer")]:
            result = self.call(action, title)
            self.assertFalse(result["success"], result)
        self.gui.PostMessage.assert_not_called()

    def test_wrong_dialog_or_process_identity_blocks(self):
        self.target.row["class"] = "TSaveDialog"
        self.assertTrue(self.call("ok")["blocked"])
        self.target.row["class"] = "TSitusSetup"
        self.target.verify.side_effect = ValueError("Pinned process changed")
        self.assertTrue(self.call("ok")["blocked"])
        self.gui.PostMessage.assert_not_called()

    def test_hidden_disabled_duplicate_or_foreign_button_blocks(self):
        original = copy.deepcopy(self.rows)
        for change in (lambda: self.rows[11].update(visible=False),
                       lambda: self.rows[11].update(enabled=False),
                       lambda: self.rows.update({15: dict(self.rows[11])}),
                       lambda: self.rows[11].update(pid=999)):
            self.rows = copy.deepcopy(original)
            change()
            self.assertTrue(self.call("ok")["blocked"])
        self.gui.PostMessage.assert_not_called()

    def test_changed_button_before_post_blocks(self):
        counts = {}
        def caption(hwnd):
            counts[hwnd] = counts.get(hwnd, 0) + 1
            return "Cancel" if hwnd == 11 and counts[hwnd] > 1 else self.rows[hwnd]["text"]
        self.gui.GetWindowText.side_effect = caption
        self.assertTrue(self.call("ok")["blocked"])
        self.gui.PostMessage.assert_not_called()

    def test_changed_main_before_post_blocks(self):
        self.target.api.title.side_effect = [TITLE, TITLE, "Other.PcbDoc"]
        self.assertTrue(self.call("ok")["blocked"])
        self.gui.PostMessage.assert_not_called()

    def test_post_failure_is_not_retried_and_completion_stays_unknown(self):
        self.gui.PostMessage.side_effect = OSError("Cannot queue")
        result = self.call("ok")
        self.assertFalse(result["success"])
        self.assertTrue(result["button_message_attempted"])
        self.assertTrue(result["outcome_unknown"])
        self.assertFalse(result["button_message_posted"])
        self.assertFalse(result["completion_verified"])
        self.gui.PostMessage.assert_called_once()


class PublicToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_locks_span_inspection_and_action(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text("utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "control_situs_setup")
        node.decorator_list = []
        self.assertEqual([arg.arg for arg in node.args.args], ["ctx", "action", "target_hwnd", "expected_main_title"])
        lock, flags = asyncio.Lock(), {"claimed": False}
        @contextmanager
        def claim(exchange):
            self.assertTrue(lock.locked())
            flags["claimed"] = True
            try:
                yield
            finally:
                flags["claimed"] = False
        def service(*args):
            self.assertTrue(lock.locked())
            self.assertTrue(flags["claimed"])
            return {"success": True, "completion_verified": False}
        env = dict(Context=object, json=json, EXCHANGE_DIR="exchange",
            emergency_stop=SimpleNamespace(emergency_claim=claim),
            altium_bridge=SimpleNamespace(_command_lock=lock, config=SimpleNamespace(altium_exe_path="configured.exe")))
        exec(compile(ast.Module(body=[node], type_ignores=[]), "situs_public", "exec"), env)
        with patch.object(control, "control", side_effect=service) as call:
            for action in ("inspect", "ok"):
                result = json.loads(await env["control_situs_setup"](None, action, 42, TITLE))
                self.assertTrue(result["success"])
        self.assertEqual(call.call_count, 2)
        self.assertFalse(lock.locked())
        self.assertFalse(flags["claimed"])


if __name__ == "__main__":
    unittest.main()
