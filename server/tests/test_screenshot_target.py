"""Exact-window capture tests; process, window and GDI calls are mocked."""
import ast
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from typing import Optional
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import screenshot_target as target


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.identity = dict(pid=123, creation_filetime=456, exe=target.emergency_stop._canonical("configured.exe"), user_sid="test", running=True)
        self.row = dict(hwnd=42, pid=123, thread_id=7, minimized=False, title="Situs Routing Strategies", **{"class": "TSitusSetup"}, rect=[0, 0, 2, 2])
        self.process = Mock()
        self.process.identity.side_effect = lambda: dict(self.identity)
        self.api = Mock()
        self.api.windows.return_value = [self.row]
        self.api.matches.return_value = True
        self.api.title.return_value = self.row["title"]
        for p in (patch.object(target.win32process, "GetWindowThreadProcessId", return_value=(7, 123)),
                  patch.object(target.emergency_stop, "WinProcess", return_value=self.process),
                  patch.object(target.emergency_stop, "_current_sid", return_value="test"),
                  patch.object(target.altium_focus, "WindowAPI", return_value=self.api)):
            p.start()
            self.addCleanup(p.stop)

    def test_exact_owned_dialog_is_pinned_without_activation(self):
        guard = target.CaptureTarget("configured.exe", 42)
        self.assertEqual(guard.window["handle"], 42)
        target.emergency_stop.WinProcess.assert_called_once_with(123)
        self.api.activate.assert_not_called()
        self.api.restore.assert_not_called()
        guard.close()
        self.process.close.assert_called_once()

    def test_foreign_hidden_minimized_or_wrong_thread_is_refused(self):
        cases = [lambda: self.identity.update(exe="foreign.exe"),
                 lambda: setattr(self.api.windows, "return_value", []),
                 lambda: self.row.update(minimized=True),
                 lambda: self.row.update(thread_id=8)]
        for change in cases:
            with self.subTest(change=change):
                self.identity["exe"] = target.emergency_stop._canonical("configured.exe")
                self.api.windows.return_value = [self.row]
                self.row.update(minimized=False, thread_id=7)
                change()
                with self.assertRaises(ValueError):
                    target.CaptureTarget("configured.exe", 42)
        self.assertEqual(self.process.close.call_count, 4)

    def test_changed_process_window_or_title_is_refused(self):
        guard = target.CaptureTarget("configured.exe", 42)
        self.identity["creation_filetime"] += 1
        with self.assertRaises(ValueError):
            guard.verify()
        self.identity["creation_filetime"] -= 1
        self.api.matches.return_value = False
        with self.assertRaises(ValueError):
            guard.verify()
        self.api.matches.return_value = True
        self.api.title.return_value = "Different"
        with self.assertRaises(ValueError):
            guard.verify()
        guard.close()


class CaptureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text("utf-8")
        nodes = [n for n in ast.parse(source).body if isinstance(n, ast.AsyncFunctionDef)
                 and n.name in {"_prepare_screenshot_view", "get_screenshot"}]
        for node in nodes:
            node.decorator_list = []
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bridge = SimpleNamespace(config=SimpleNamespace(altium_exe_path="configured.exe"), execute_command=AsyncMock())
        self.gui, self.ui, self.print_window = Mock(), Mock(), Mock(return_value=True)
        self.gui.GetWindowRect.return_value = (0, 0, 2, 2)
        self.gui.IsIconic.return_value = False
        self.gui.GetForegroundWindow.return_value = 42
        bitmap = self.ui.CreateBitmap.return_value
        bitmap.GetInfo.return_value = {"bmWidth": 2, "bmHeight": 2}
        bitmap.GetBitmapBits.return_value = bytes(16)
        self.guard = Mock(window={"handle": 42, "title": "Situs Routing Strategies", "class_name": "TSitusSetup", "rect": [0, 0, 2, 2]}, identity={"pid": 123, "creation_filetime": 456})
        self.env = dict(Context=object, Optional=Optional, json=json, logger=Mock(),
            altium_bridge=self.bridge, win32gui=self.gui, win32ui=self.ui,
            win32process=SimpleNamespace(GetWindowThreadProcessId=lambda _: (7, 123)),
            _print_diagnostic_window=self.print_window, io=io, base64=base64,
            MCP_DIR=Path(self.temp.name), MCPImage=lambda **kwargs: kwargs,
            _current_diagnostic_window=Mock(side_effect=AssertionError("No fallback allowed")))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "capture", "exec"), self.env)
        p = patch.object(target, "CaptureTarget", return_value=self.guard)
        self.factory = p.start()
        self.addCleanup(p.stop)

    async def test_exact_capture_uses_printwindow_without_input_or_dispatch(self):
        result = await self.env["get_screenshot"](None, "current", window_handle=42)
        metadata = json.loads(result[0])
        self.assertTrue(metadata["success"])
        self.assertEqual(metadata["window_handle"], 42)
        self.assertEqual(metadata["capture_method"], "PrintWindow")
        self.assertFalse(metadata["script_focus_requested"])
        self.guard.verify.assert_called()
        self.guard.close.assert_called_once()
        self.bridge.execute_command.assert_not_awaited()
        self.gui.EnumWindows.assert_not_called()
        self.gui.SetForegroundWindow.assert_not_called()
        self.env["_current_diagnostic_window"].assert_not_called()

    async def test_bad_mode_zoom_or_handle_never_reaches_worker(self):
        for mode, zoom, hwnd in [("pcb", None, 42), ("current", [], 42),
                                 ("current", ["U1"], 42), ("current", None, True),
                                 ("current", None, 0)]:
            result = json.loads(await self.env["get_screenshot"](None, mode, zoom, hwnd))
            self.assertFalse(result["success"])
        self.factory.assert_not_called()
        self.bridge.execute_command.assert_not_awaited()

    async def test_post_capture_identity_failure_returns_no_image_and_no_debug_file(self):
        self.guard.verify.side_effect = [None, ValueError("changed")]
        result = json.loads(await self.env["get_screenshot"](None, "current", window_handle=42))
        self.assertFalse(result["success"])
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])
        self.guard.close.assert_called_once()
        self.gui.ReleaseDC.assert_called_once()
        self.gui.SetForegroundWindow.assert_not_called()


if __name__ == "__main__":
    unittest.main()
