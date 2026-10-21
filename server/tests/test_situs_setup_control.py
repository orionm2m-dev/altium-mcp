"""Situs button policy and public lock tests; no native calls."""
import ast
import asyncio
from contextlib import contextmanager
import copy
import ctypes
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
        self.gui.GetClientRect.return_value = (0, 0, 100, 20)
        self.gui.GetClassName.side_effect = lambda hwnd: self.rows[hwnd]["class"]
        self.gui.GetWindowText.side_effect = lambda hwnd: self.rows[hwnd]["text"]
        self.gui.IsWindowVisible.side_effect = lambda hwnd: self.rows[hwnd]["visible"]
        self.gui.IsWindowEnabled.side_effect = lambda hwnd: self.rows[hwnd]["enabled"]
        self.process = SimpleNamespace(GetWindowThreadProcessId=lambda hwnd: [self.rows[hwnd]["thread"], self.rows[hwnd]["pid"]])
        for p in (patch.object(control, "CaptureTarget", return_value=self.target),
                  patch.object(control, "win32gui", self.gui), patch.object(control, "win32process", self.process)):
            p.start()
            self.addCleanup(p.stop)

    def call(self, action="inspect", title=TITLE, **kwargs):
        return control.control("configured.exe", action, 42, title, **kwargs)

    def add_checkbox(self, checked=False):
        self.rows[14] = {"pid": 123, "thread": 7, "class": "TCheckBox",
            "text": "Rip-up Violations After Routing", "visible": True, "enabled": True}
        self.checked = checked
        self.gui.GetWindowLong.return_value = 3
        def message(target, hwnd, msg):
            if msg == 0xF0:
                return int(self.checked)
            self.assertEqual(msg, 0xF5)
            self.checked = not self.checked
            return 0
        p = patch.object(control, "_message", side_effect=message)
        self.message = p.start()
        self.addCleanup(p.stop)

    def enable(self, expected=False, hwnd=14):
        return self.call("enable_rip_up", checkbox_hwnd=hwnd, expected_checked=expected)

    def test_inspect_skips_foreign_descendants_without_input(self):
        result = self.call()
        self.assertTrue(result["success"], result)
        self.assertEqual([b["hwnd"] for b in result["buttons"]], [11, 12])
        self.assertEqual(result["foreign_descendants_skipped"], 1)
        self.assertFalse(result["completion_verified"])
        self.gui.PostMessage.assert_not_called()
        self.target.close.assert_called_once()

    def test_inspect_reads_checkbox_state_without_click(self):
        self.add_checkbox(True)
        result = self.call()
        self.assertTrue(result["success"], result)
        self.assertEqual(result["checkboxes"][0]["hwnd"], 14)
        self.assertTrue(result["checkboxes"][0]["checked"])
        self.assertEqual([c.args[2] for c in self.message.call_args_list], [0xF0])
        self.gui.PostMessage.assert_not_called()

    def test_enable_checks_expected_state_clicks_once_and_reads_back(self):
        self.add_checkbox()
        result = self.enable()
        self.assertTrue(result["success"], result)
        self.assertTrue(result["checkbox_state_verified"])
        self.assertTrue(result["checked_after"])
        self.assertTrue(result["checkbox_message_completed"])
        self.assertFalse(result["completion_verified"])
        self.assertEqual([c.args[2] for c in self.message.call_args_list], [0xF0, 0xF0, 0xF5, 0xF0])
        self.gui.PostMessage.assert_not_called()

    def test_already_enabled_is_no_input_and_stale_expected_state_refuses(self):
        self.add_checkbox(True)
        result = self.enable(True)
        self.assertTrue(result["success"], result)
        self.assertTrue(result["no_input"])
        self.assertTrue(result["already_enabled"])
        self.assertTrue(self.enable(False)["blocked"])
        self.assertTrue(all(c.args[2] == 0xF0 for c in self.message.call_args_list))

    def test_manual_three_state_checkbox_requires_binary_state_and_checked_readback(self):
        self.add_checkbox()
        self.gui.GetWindowLong.return_value = 5
        self.assertTrue(self.enable()["success"])

    def test_checkbox_messages_are_bounded_and_use_zero_parameters(self):
        self.target.api.c = ctypes
        def reply(hwnd, message, wp, lp, flags, timeout, address):
            self.assertEqual((hwnd, message, wp, lp, flags, timeout), (14, 0xF0, 0, 0, 0x22, 500))
            ctypes.cast(address, ctypes.POINTER(ctypes.c_size_t)).contents.value = 1
            return 1
        self.target.api.api.SendMessageTimeoutW.side_effect = reply
        self.assertEqual(control._message(self.target, 14, 0xF0), 1)
        self.target.api.api.SendMessageTimeoutW.side_effect = None
        self.target.api.api.SendMessageTimeoutW.return_value = 0
        with self.assertRaisesRegex(OSError, "no retry"):
            control._message(self.target, 14, 0xF0)
        self.assertEqual(self.target.api.api.SendMessageTimeoutW.call_count, 2)

    def test_checkbox_arguments_class_style_identity_and_duplicates_refuse(self):
        self.add_checkbox()
        self.assertTrue(self.call("enable_rip_up")["blocked"])
        self.assertTrue(self.enable(expected=0)["blocked"])
        self.assertTrue(self.enable(hwnd=99)["blocked"])
        self.assertTrue(self.call("ok", checkbox_hwnd=14)["blocked"])
        original = copy.deepcopy(self.rows)
        for change in (lambda: self.rows[14].update(visible=False),
                       lambda: self.rows[14].update(enabled=False),
                       lambda: self.rows[14].update(pid=999),
                       lambda: self.rows[14].update(text="Lock All Pre-routes"),
                       lambda: self.rows[14].update({"class": "UnknownCheckbox"}),
                       lambda: self.rows.update({15: dict(self.rows[14])})):
            self.rows = copy.deepcopy(original)
            change()
            self.assertTrue(self.enable()["blocked"])
        self.rows = original
        self.gui.GetWindowLong.return_value = 11  # Owner-draw is not a readable two-state checkbox.
        result = self.call()
        self.assertTrue(result["success"], result)
        self.assertIn("state_error", result["checkboxes"][0])
        self.assertTrue(self.enable()["blocked"])
        self.assertTrue(all(c.args[2] == 0xF0 for c in self.message.call_args_list))
        self.gui.PostMessage.assert_not_called()

    def test_checkbox_state_change_or_failed_read_blocks_before_input(self):
        self.add_checkbox()
        for results in ([0, 1], [2], [OSError("read timeout")]):
            self.message.reset_mock(side_effect=True)
            self.message.side_effect = results
            self.assertTrue(self.enable()["blocked"])
            self.assertTrue(all(c.args[2] == 0xF0 for c in self.message.call_args_list))

    def test_checkbox_style_change_during_read_blocks_before_input(self):
        self.add_checkbox()
        self.gui.GetWindowLong.side_effect = [3, 11]
        result = self.enable()
        self.assertTrue(result["blocked"], result)
        self.assertEqual([c.args[2] for c in self.message.call_args_list], [0xF0])

    def test_failed_checkbox_click_or_readback_is_unknown_and_never_retried(self):
        self.add_checkbox()
        for results in ([0, 0, OSError("click timeout")], [0, 0, 0, 0], [0, 0, 0, OSError("read timeout")]):
            self.message.reset_mock(side_effect=True)
            self.message.side_effect = results
            result = self.enable()
            self.assertFalse(result["success"], result)
            self.assertTrue(result["outcome_unknown"])
            self.assertFalse(result["checkbox_state_verified"])
            self.assertEqual([c.args[2] for c in self.message.call_args_list].count(0xF5), 1)

    def custom_checkbox(self):
        self.add_checkbox()
        self.rows[14]["class"]="TXPCheckBox"
        self.gui.GetWindowLong.return_value=0
        self.message.side_effect=lambda *args:0

    def test_custom_xp_zero_reply_is_unknown_not_false(self):
        self.custom_checkbox()
        result=self.call();self.assertTrue(result["success"],result)
        checkbox=result["checkboxes"][0]
        self.assertEqual(checkbox["raw_bm_getcheck"],0)
        self.assertFalse(checkbox["state_known"]);self.assertNotIn("checked",checkbox)
        self.assertIn("state_error",checkbox)
        self.assertTrue(all(c.args[2]==0xF0 for c in self.message.call_args_list))

    def test_custom_xp_nonzero_reply_still_unqualified(self):
        self.custom_checkbox();self.message.side_effect=lambda *args:1
        checkbox=self.call()["checkboxes"][0]
        self.assertEqual(checkbox["raw_bm_getcheck"],1);self.assertNotIn("checked",checkbox)
        self.assertFalse(checkbox["state_known"])

    def test_custom_enable_and_repeat_are_blocked_before_any_gesture(self):
        self.custom_checkbox()
        for expected in (False,True,False):
            result=self.enable(expected)
            self.assertFalse(result["success"]);self.assertTrue(result["blocked"])
            self.assertFalse(result["checkbox_message_attempted"])
            self.assertFalse(result["checkbox_state_verified"])
        self.assertTrue(all(c.args[2]==0xF0 for c in self.message.call_args_list))
        self.gui.PostMessage.assert_not_called()

    def test_zero_style_on_other_checkbox_stays_refused(self):
        self.add_checkbox();self.gui.GetWindowLong.return_value=0
        self.assertTrue(self.enable()["blocked"])
        self.message.assert_not_called()

    def test_custom_ok_pair_and_standard_cancel_message_without_completion_claim(self):
        for action, hwnd in [("ok", 11), ("cancel", 12)]:
            with self.subTest(action=action):
                self.gui.PostMessage.reset_mock()
                result = self.call(action)
                self.assertTrue(result["success"], result)
                self.assertTrue(result["button_message_posted"])
                self.assertFalse(result["completion_verified"])
                if action == "ok":
                    self.assertEqual([c.args for c in self.gui.PostMessage.call_args_list],
                        [(hwnd, 0x201, 1, 50 | (10 << 16)), (hwnd, 0x202, 0, 50 | (10 << 16))])
                    self.assertTrue(result["message_pair_posted"])
                    self.assertEqual(result["messages_posted"], 2)
                else:
                    self.gui.PostMessage.assert_called_once_with(hwnd, 0xF5, 0, 0)
                    self.assertFalse(result["message_pair_attempted"])
        self.gui.SetForegroundWindow.assert_not_called()
        self.gui.SendMessage.assert_not_called()

    def test_route_all_requires_observed_live_button(self):
        self.assertTrue(self.call("route_all")["blocked"])
        self.gui.PostMessage.assert_not_called()
        self.rows[14] = dict(self.rows[11], text="Route All", **{"class": "TButton"})
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

    def test_invalid_client_rect_blocks_without_any_message(self):
        for rect in [(0, 0, 0, 20), (0, 0, 100, 0), (0, 0, 100000, 20)]:
            self.gui.GetClientRect.return_value = rect
            self.assertTrue(self.call("ok")["blocked"])
        self.gui.PostMessage.assert_not_called()

    def test_second_message_failure_reports_partial_pair_without_retry(self):
        self.gui.PostMessage.side_effect = [None, OSError("up failed")]
        result = self.call("ok")
        self.assertFalse(result["success"])
        self.assertTrue(result["message_pair_attempted"])
        self.assertFalse(result["message_pair_posted"])
        self.assertTrue(result["partial_dispatch"])
        self.assertEqual((result["messages_attempted"], result["messages_posted"]), (2, 1))
        self.assertEqual(self.gui.PostMessage.call_count, 2)

    def test_identity_change_after_down_refuses_up(self):
        def down(*args):
            self.rows[11]["text"] = "Changed"
        self.gui.PostMessage.side_effect = down
        result = self.call("ok")
        self.assertFalse(result["success"])
        self.assertTrue(result["partial_dispatch"])
        self.assertEqual((result["messages_attempted"], result["messages_posted"]), (1, 1))
        self.gui.PostMessage.assert_called_once()

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
        self.assertEqual([arg.arg for arg in node.args.args], ["ctx", "action", "target_hwnd", "expected_main_title", "checkbox_hwnd", "expected_checked"])
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
            for action in ("inspect", "ok", "enable_rip_up"):
                extra = {"checkbox_hwnd": 14, "expected_checked": False} if action == "enable_rip_up" else {}
                result = json.loads(await env["control_situs_setup"](None, action, 42, TITLE, **extra))
                self.assertTrue(result["success"])
            self.assertEqual(call.call_args.args, ("configured.exe", "enable_rip_up", 42, TITLE, 14, False))
        self.assertEqual(call.call_count, 3)
        self.assertFalse(lock.locked())
        self.assertFalse(flags["claimed"])


if __name__ == "__main__":
    unittest.main()
