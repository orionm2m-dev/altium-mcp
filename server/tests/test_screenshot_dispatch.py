"""Check diagnostic screenshots do not depend on the script executor."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import ctypes
import os


class ScreenshotDispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
        node = next(n for n in ast.parse(source).body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == "_prepare_screenshot_view")
        self.dispatch = AsyncMock(return_value={"success": True, "result": {"focused_kind": "PCB"}})
        env = {"altium_bridge": SimpleNamespace(execute_command=self.dispatch)}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "screenshot_helper", "exec"), env)
        self.prepare = env["_prepare_screenshot_view"]

    async def test_current_never_dispatches_even_if_executor_is_broken(self):
        self.dispatch.side_effect = RuntimeError("executor unavailable")
        self.assertEqual(await self.prepare("CURRENT", None), {"success": True, "result": {}})
        self.dispatch.assert_not_awaited()

    async def test_current_rejects_zoom_before_dispatch(self):
        result = await self.prepare("current", ["U1"])
        self.assertFalse(result["success"])
        self.dispatch.assert_not_awaited()

    async def test_editor_preserves_exact_zoom_request(self):
        result = await self.prepare("PCB", ["U1"])
        self.assertTrue(result["success"])
        self.dispatch.assert_awaited_once_with("take_view_screenshot", {"view_type": "pcb", "designators": ["U1"]})

    async def test_editor_failure_is_not_retried_or_relabeled(self):
        self.dispatch.return_value = {"success": False, "error": "compile failure"}
        self.assertEqual(await self.prepare("sch", None), self.dispatch.return_value)
        self.assertEqual(self.dispatch.await_count, 1)

    async def test_unknown_mode_cannot_silently_bypass_dispatch(self):
        self.assertFalse((await self.prepare("anything", None))["success"])
        self.dispatch.assert_not_awaited()


class DiagnosticFallbackTests(unittest.TestCase):
    def setUp(self):
        source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
        self.tree = ast.parse(source)
        nodes = [node for node in self.tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {"_current_diagnostic_window", "_print_diagnostic_window"}]
        self.rows = {
            1: {"pid": 10, "path": "C:/Altium/X2.EXE", "class": "TShowException", "title": "Error", "visible": True},
            2: {"pid": 11, "path": "C:/Other/app.exe", "class": "TShowException", "title": "Error", "visible": True},
            3: {"pid": 10, "path": "C:/Altium/X2.EXE", "class": "TMessageForm", "title": "Information", "visible": True}}
        self.dialogs = SimpleNamespace(top_windows=lambda: list(self.rows),
            is_visible=lambda h: self.rows[h]["visible"],
            window_class=lambda h: self.rows[h]["class"], window_text=lambda h: self.rows[h]["title"])
        self.gui = Mock()
        self.gui.GetWindowRect.return_value = (0, 0, 400, 200)
        self.owned = {}
        env = {"dialogs": self.dialogs, "win32gui": self.gui,
               "emergency_stop": SimpleNamespace(_canonical=lambda p: os.path.normcase(os.path.abspath(p))),
               "script_guard": SimpleNamespace(_window_process=lambda h: (self.rows[h]["pid"], self.rows[h]["path"])),
               "altium_lifecycle": SimpleNamespace(_started=self.owned)}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "diagnostic_helpers", "exec"), env)
        self.select = env["_current_diagnostic_window"]
        self.capture = env["_print_diagnostic_window"]

    def test_error_without_altium_title_is_selected_only_by_verified_exe(self):
        selected = self.select("C:/Altium/X2.EXE")
        self.assertEqual((selected["handle"], selected["pid"], selected["title"]), (1, 10, "Error"))
        self.gui.SetForegroundWindow.assert_not_called()
        self.rows[1]["visible"] = False
        self.rows[3]["visible"] = False
        self.assertIsNone(self.select("C:/Altium/X2.EXE"))  # Foreign Error remains visible.

    def test_owned_exception_preferred_and_hidden_during_read_is_ignored(self):
        self.rows[4] = dict(self.rows[1], pid=20)
        self.owned[20] = Mock()
        self.owned[20].poll.return_value = None
        self.assertEqual(self.select("C:/Altium/X2.EXE")["handle"], 4)
        def hides(h):
            if h == 4:
                self.rows[4]["visible"] = False
            return self.rows[h]["title"]
        self.dialogs.window_text = hides
        self.assertEqual(self.select("C:/Altium/X2.EXE")["handle"], 1)

    def test_printwindow_only_no_keyboard_focus_or_dialog_answers(self):
        api = Mock()
        api.PrintWindow.return_value = 1
        with patch.object(ctypes, "WinDLL", return_value=api, create=True):
            self.assertTrue(self.capture(342548, 77))
        api.PrintWindow.assert_called_once_with(342548, 77, 2)
        api.SetForegroundWindow.assert_not_called()
        api.keybd_event.assert_not_called()
        api.PostMessageW.assert_not_called()

    def test_printwindow_failure_is_reported(self):
        api = Mock()
        api.PrintWindow.return_value = 0
        with patch.object(ctypes, "WinDLL", return_value=api, create=True):
            self.assertFalse(self.capture(1, 2))

    def test_activation_is_only_in_non_diagnostic_branch(self):
        screenshot = next(n for n in self.tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "get_screenshot")
        branch = next(n for n in ast.walk(screenshot) if isinstance(n, ast.If)
                      and ast.unparse(n.test) == "diagnostic_fallback" and "activated" in ast.unparse(n))
        diagnostic = ast.unparse(ast.Module(body=branch.body, type_ignores=[]))
        normal = ast.unparse(ast.Module(body=branch.orelse, type_ignores=[]))
        self.assertNotIn("SetForegroundWindow", diagnostic)
        self.assertNotIn("keybd_event", diagnostic)
        self.assertIn("SetForegroundWindow", normal)
        selection = next(n for n in ast.walk(screenshot) if isinstance(n, ast.If)
                         and "not altium_windows" in ast.unparse(n.test)
                         and "current" in ast.unparse(n.test))
        self.assertIn("_current_diagnostic_window", ast.unparse(selection))


if __name__ == "__main__":
    unittest.main()
