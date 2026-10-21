"""Exact informational error acknowledgment tests; all native I/O is mocked."""
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
import script_error_control as control
import situs_setup_control

TITLE = "Sandbox.PrjScr - Altium Designer (26.9.1) - Trial"
TEXT = "Undeclared identifier: GetState_DesignRuleCheckerOptions"


class ErrorDialogTests(unittest.TestCase):
    def setUp(self):
        self.main = {"hwnd": 100, "class": "TDocumentForm", "title": TITLE}
        self.target = Mock(hwnd=42, identity={"pid": 123, "creation_filetime": 456},
            row={"class": "TMessageForm", "title": "Error", "enabled": True},
            window={"handle": 42, "title": "Error", "class_name": "TMessageForm"})
        self.windows = [self.main, {"hwnd": 42, "class": "TMessageForm", "title": "Error"},
                        {"hwnd": 40, "class": "TApplication", "title": ""}]
        self.target.api.windows.side_effect = lambda pid: copy.deepcopy(self.windows)
        self.target.api.matches.return_value = True
        self.target.api.title.side_effect = lambda hwnd: self.main["title"]
        self.rows = {
            11: {"pid": 123, "thread": 7, "class": "TEdit", "text": "", "content": TEXT,
                 "visible": True, "enabled": True},
            12: {"pid": 123, "thread": 7, "class": "TXPBitBtn", "text": "&OK", "content": "&OK",
                 "visible": True, "enabled": True}}
        self.gui = Mock()
        self.gui.EnumChildWindows.side_effect = lambda hwnd, callback, data: [callback(h, data) for h in self.rows]
        self.gui.IsChild.return_value = True
        self.gui.GetClientRect.return_value = (0, 0, 100, 20)
        self.gui.GetClassName.side_effect = lambda hwnd: self.rows[hwnd]["class"]
        self.gui.GetWindowText.side_effect = lambda hwnd: self.rows[hwnd]["text"]
        self.gui.IsWindowVisible.side_effect = lambda hwnd: self.rows[hwnd]["visible"]
        self.gui.IsWindowEnabled.side_effect = lambda hwnd: self.rows[hwnd]["enabled"]
        process = SimpleNamespace(GetWindowThreadProcessId=lambda hwnd: [self.rows[hwnd]["thread"], self.rows[hwnd]["pid"]])
        self.text = Mock(side_effect=lambda target, hwnd: self.rows[hwnd]["content"])
        self.send = Mock(return_value=0)
        for p in (patch.object(control, "CaptureTarget", return_value=self.target),
                  patch.object(control, "win32gui", self.gui),
                  patch.object(situs_setup_control, "win32gui", self.gui),
                  patch.object(situs_setup_control, "win32process", process),
                  patch.object(control, "_text", self.text), patch.object(control, "_send", self.send)):
            p.start()
            self.addCleanup(p.stop)

    def call(self, action="inspect", **kwargs):
        args = dict(exe="configured.exe", action=action, target_pid=123, target_hwnd=42,
                    expected_main_title=TITLE)
        if action == "acknowledge":
            args.update(expected_error_text=TEXT, button_hwnd=12)
        args.update(kwargs)
        return control.control(**args)

    def test_inspection_reads_edit_contents_without_input(self):
        result = self.call()
        self.assertTrue(result["success"], result)
        self.assertTrue(result["acknowledgment_allowed"])
        self.assertEqual(result["error_text"], TEXT)
        self.assertEqual(result["button"]["hwnd"], 12)
        self.assertTrue(result["no_input"])
        self.send.assert_not_called()
        self.target.close.assert_called_once()

    def test_one_custom_ok_pair_or_standard_click_never_claims_recovery(self):
        for cls in ("TXPBitBtn", "TButton"):
            self.rows[12]["class"] = cls
            self.send.reset_mock()
            result = self.call("acknowledge")
            self.assertTrue(result["success"], result)
            if cls == "TXPBitBtn":
                self.assertEqual([c.args[1:] for c in self.send.call_args_list],
                    [(12, 0x201, 1, 50 | (10 << 16)), (12, 0x202, 0, 50 | (10 << 16))])
            else:
                self.send.assert_called_once_with(self.target, 12, 0xF5, 0, 0)
            for flag in ("dialog_closed_verified", "executor_recovered", "stop_dispatched",
                         "script_dispatched", "process_terminated", "document_save_requested", "retry_performed"):
                self.assertFalse(result[flag], flag)
        self.gui.PostMessage.assert_not_called()
        self.gui.SetForegroundWindow.assert_not_called()

    def test_wrong_pid_dialog_title_main_or_process_identity_refuses(self):
        self.assertTrue(self.call("acknowledge", target_pid=999)["blocked"])
        self.assertTrue(self.call("acknowledge", expected_main_title="Other.PrjScr")["blocked"])
        self.target.row["class"] = "TShowException"
        self.assertTrue(self.call("acknowledge")["blocked"])
        self.target.row["class"] = "TMessageForm"
        self.target.row["title"] = "Save"
        self.assertTrue(self.call("acknowledge")["blocked"])
        self.target.row["title"] = "Error"
        self.target.verify.side_effect = ValueError("PID creation changed")
        self.assertTrue(self.call("acknowledge")["blocked"])
        self.send.assert_not_called()

    def test_only_readable_undeclared_identifier_and_unique_supported_ok_qualify(self):
        original = copy.deepcopy(self.rows)
        for change in (lambda: self.rows[11].update(content=""),
                       lambda: self.rows[11].update(content="Syntax error"),
                       lambda: self.rows[11].update(content="Runtime error in script"),
                       lambda: self.rows[11].update(visible=False),
                       lambda: self.rows[12].update(content="Cancel", text="Cancel"),
                       lambda: self.rows[12].update({"class": "UnknownButton"}),
                       lambda: self.rows[12].update(enabled=False),
                       lambda: self.rows.update({13: dict(self.rows[12])})):
            self.rows = copy.deepcopy(original)
            change()
            inspected = self.call()
            self.assertTrue(inspected["success"], inspected)
            self.assertFalse(inspected["acknowledgment_allowed"], inspected)
            self.assertTrue(self.call("acknowledge")["blocked"])
        self.send.assert_not_called()

    def test_invalid_arguments_and_stale_text_or_button_refuse(self):
        for args in ({"action": "cancel"}, {"target_pid": True}, {"expected_error_text": "Changed"},
                     {"expected_error_text": ""}, {"button_hwnd": 99}, {"button_hwnd": True}):
            self.assertTrue(self.call(args.pop("action", "acknowledge"), **args)["blocked"])
        self.send.assert_not_called()

    def test_foreign_child_other_form_and_unreadable_text_refuse(self):
        self.rows[11]["pid"] = 999
        self.assertTrue(self.call("acknowledge")["blocked"])
        self.rows[11]["pid"] = 123
        self.windows.append({"hwnd": 50, "class": "TSaveDialog", "title": "Save"})
        self.assertTrue(self.call("acknowledge")["blocked"])
        self.windows.pop()
        self.text.side_effect = OSError("WM_GETTEXT timeout")
        self.assertTrue(self.call("acknowledge")["blocked"])
        self.send.assert_not_called()

    def test_changed_content_before_click_refuses(self):
        self.text.side_effect = [TEXT, "OK", "Undeclared identifier: Other", "OK"]
        self.assertTrue(self.call("acknowledge")["blocked"])
        self.send.assert_not_called()

    def test_second_message_failure_reports_partial_unknown_without_retry(self):
        self.send.side_effect = [0, OSError("up timeout")]
        result = self.call("acknowledge")
        self.assertFalse(result["success"])
        self.assertTrue(result["outcome_unknown"])
        self.assertTrue(result["partial_dispatch"])
        self.assertEqual((result["messages_attempted"], result["messages_completed"]), (2, 1))
        self.assertEqual(self.send.call_count, 2)

    def test_changed_identity_after_down_refuses_up(self):
        self.send.side_effect = lambda *args: self.rows[12].update(pid=999)
        result = self.call("acknowledge")
        self.assertTrue(result["partial_dispatch"])
        self.send.assert_called_once()


class MessageTests(unittest.TestCase):
    def test_wm_gettext_is_bounded_with_owned_buffer_and_timeout_failure(self):
        native = Mock()
        target = SimpleNamespace(api=SimpleNamespace(c=ctypes, api=native))
        def send(hwnd, message, wp, lp, flags, timeout, address):
            self.assertEqual((hwnd, message, wp, flags, timeout), (14, 0xD, 4097, 0x22, 500))
            buffer = ctypes.cast(lp, ctypes.POINTER(ctypes.c_wchar * wp)).contents
            buffer.value = TEXT
            ctypes.cast(address, ctypes.POINTER(ctypes.c_size_t)).contents.value = len(TEXT)
            return 1
        native.SendMessageTimeoutW.side_effect = send
        self.assertEqual(control._text(target, 14), TEXT)
        native.SendMessageTimeoutW.side_effect = None
        native.SendMessageTimeoutW.return_value = 0
        with self.assertRaisesRegex(OSError, "no retry"):
            control._text(target, 14)


class PublicToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_wrapper_holds_both_locks_and_forwards_exact_observation(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text("utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "acknowledge_script_error")
        node.decorator_list = []
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
            return {"success": True, "executor_recovered": False}
        env = dict(Context=object, json=json, EXCHANGE_DIR="exchange",
            emergency_stop=SimpleNamespace(emergency_claim=claim),
            altium_bridge=SimpleNamespace(_command_lock=lock, config=SimpleNamespace(altium_exe_path="configured.exe")))
        exec(compile(ast.Module(body=[node], type_ignores=[]), "error_public", "exec"), env)
        with patch.object(control, "control", side_effect=service) as call:
            result = json.loads(await env["acknowledge_script_error"](None, "acknowledge", 123, 42, TITLE, TEXT, 12))
            self.assertTrue(result["success"])
            call.assert_called_once_with("configured.exe", "acknowledge", 123, 42, TITLE, TEXT, 12)
        self.assertFalse(lock.locked())
        self.assertFalse(flags["claimed"])


if __name__ == "__main__":
    unittest.main()
