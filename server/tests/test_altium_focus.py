"""Offline focus tests: process/window adapters are mocked, no Altium calls."""
import ast
import asyncio
from contextlib import ExitStack
import ctypes
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import altium_focus as focus
import emergency_stop


class FocusTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.exe = self.root / "X2.EXE"
        self.exe.write_bytes(b"never executed")
        self.latch = self.root / "script_recovery_state.json"
        self.latch.write_bytes(b'{"phase":"probe_pending","token":"preserve"}')
        self.original = self.latch.read_bytes()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.inventory = self.stack.enter_context(patch.object(focus.altium_lifecycle,
            "configured_instances", return_value={"configured": [{"pid": 123}],
                "uncertain": [], "other_paths": []}))
        self.identity = {"pid": 123, "exe": emergency_stop._canonical(self.exe),
            "creation_filetime": 133777777777777777, "user_sid": "test-user", "running": True}
        self.process = Mock()
        self.process.identity.side_effect = lambda: dict(self.identity)
        self.process.__enter__ = Mock(return_value=self.process)
        self.process.__exit__ = Mock(return_value=False)
        self.factory = self.stack.enter_context(patch.object(emergency_stop, "WinProcess", return_value=self.process))
        self.stack.enter_context(patch.object(emergency_stop, "_current_sid", return_value="test-user"))
        self.main = {"hwnd": 42, "pid": 123, "thread_id": 7, "class": "TDocumentForm", "enabled": True,
            "minimized": True, "tool_window": False, "title": "Home Page",
            "owner_hwnd": 0, "rect": [0, 0, 160, 28]}
        self.rows = [self.main]
        self.foreground = 99
        self.api = Mock()
        self.api.windows.side_effect = lambda pid: [dict(row) for row in self.rows]
        self.api.responsive.return_value = True
        self.api.matches.return_value = True
        self.api.foreground.side_effect = lambda: self.foreground
        self.api.restore.side_effect = self.restore
        self.api.activate.side_effect = self.activate
        self.stack.enter_context(patch.object(focus, "WindowAPI", return_value=self.api))

    def restore(self, hwnd):
        self.main.update(minimized=False, rect=[0, 0, 1280, 900])
        return True

    def activate(self, hwnd):
        self.foreground = hwnd
        return True

    def call(self, pid=123):
        result = focus.focus(str(self.exe), self.root, pid)
        self.assertEqual(self.latch.read_bytes(), self.original)
        self.process.terminate.assert_not_called()
        self.process.wait.assert_not_called()
        return result

    def assert_no_action(self):
        self.api.restore.assert_not_called()
        self.api.activate.assert_not_called()

    def test_minimized_exact_process_restores_and_activates_once(self):
        result = self.call()
        self.assertTrue(result["success"])
        self.assertTrue(result["restored"])
        self.assertTrue(result["foreground_confirmed"])
        self.assertFalse(result["native_idle_verified"])
        self.assertFalse(result["script_dispatched"])
        self.assertFalse(result["document_saved"])
        self.assertEqual(result["window_after"]["rect"], [0, 0, 1280, 900])
        self.assertEqual(result["creation_filetime_decimal"], "133777777777777777")
        self.factory.assert_called_once_with(123)
        self.api.restore.assert_called_once_with(42)
        self.api.activate.assert_called_once_with(42)
        self.process.__exit__.assert_called_once()

    def test_already_foreground_does_not_restore_or_reactivate(self):
        self.main["minimized"] = False
        self.foreground = 42
        self.assertTrue(self.call()["success"])
        self.assert_no_action()

    def test_invalid_pid_or_missing_executable_has_no_native_query(self):
        for pid in (0, -3, True, "123", None):
            self.assertFalse(self.call(pid)["success"])
        self.exe.unlink()
        self.assertFalse(self.call()["success"])
        self.factory.assert_not_called()
        self.assert_no_action()

    def test_other_process_or_ambiguous_inventory_is_refused(self):
        for inventory in ({"configured": [{"pid": 456}], "uncertain": []},
                {"configured": [{"pid": 123}, {"pid": 456}], "uncertain": []},
                {"configured": [{"pid": 123}], "uncertain": [{"pid": 789}]}):
            self.inventory.return_value = inventory
            self.assertFalse(self.call()["success"])
        self.factory.assert_not_called()
        self.assert_no_action()

    def test_foreign_executable_user_or_ended_process_is_refused(self):
        for field, value in (("exe", "other.exe"), ("user_sid", "another-user"),
                              ("running", False), ("pid", 456)):
            with self.subTest(field=field), patch.dict(self.identity, {field: value}):
                self.assertFalse(self.call()["success"])
        self.assert_no_action()

    def test_missing_ambiguous_modal_and_unknown_popup_are_refused(self):
        for cls in ("TDocumentForm", "#32770", "TPCBImportWizard", "UnknownToolPopup"):
            self.rows = [self.main, dict(self.main, hwnd=88, **{"class": cls, "tool_window": True})]
            self.assertFalse(self.call()["success"])
        self.rows = []
        self.assertFalse(self.call()["success"])
        self.assert_no_action()

    def test_empty_framework_owner_is_allowed_but_never_activated(self):
        owner = dict(self.main, hwnd=88, owner_hwnd=0, title="", **{"class": "TApplication"})
        self.main["owner_hwnd"] = 88
        self.rows.append(owner)
        result = self.call()
        self.assertTrue(result["success"])
        self.assertEqual(result["window_after"]["owner_hwnd"], 88)
        self.api.restore.assert_called_once_with(42)
        self.api.activate.assert_called_once_with(42)

    def test_unrelated_named_foreign_thread_or_cyclic_framework_is_refused(self):
        self.main["owner_hwnd"] = 88
        owner = dict(self.main, hwnd=88, owner_hwnd=0, title="", **{"class": "TApplication"})
        for changes in ({"title": "Dialog"}, {"hwnd": 89}, {"thread_id": 8},
                {"pid": 456}, {"enabled": False}, {"owner_hwnd": 42}, {"owner_hwnd": 999},
                {"class": "OtherFramework"}):
            with self.subTest(changes=changes):
                self.rows = [self.main, dict(owner, **changes)]
                self.assertFalse(self.call()["success"])
        self.assert_no_action()

    def test_framework_owner_title_or_identity_change_before_action_is_refused(self):
        self.main["owner_hwnd"] = 88
        owner = dict(self.main, hwnd=88, owner_hwnd=0, title="", **{"class": "TApplication"})
        self.rows.append(owner)
        self.api.matches.side_effect = lambda row: row["hwnd"] != 88
        self.assertFalse(self.call()["success"])
        self.api.matches.side_effect = None
        self.api.matches.return_value = True
        self.api.windows.side_effect = [[dict(self.main), dict(owner)],
            [dict(self.main), dict(owner, title="New dialog")]]
        self.assertFalse(self.call()["success"])
        self.assert_no_action()

    def test_disabled_or_unresponsive_main_is_refused(self):
        self.main["enabled"] = False
        self.assertFalse(self.call()["success"])
        self.main["enabled"] = True
        self.api.responsive.return_value = False
        self.assertFalse(self.call()["success"])
        self.assert_no_action()

    def test_identity_or_window_changed_before_restore_is_refused(self):
        self.process.identity.side_effect = [dict(self.identity), dict(self.identity, creation_filetime=7)]
        self.assertFalse(self.call()["success"])
        self.process.identity.side_effect = lambda: dict(self.identity)
        self.api.windows.side_effect = [[dict(self.main)], [dict(self.main, hwnd=88)]]
        self.assertFalse(self.call()["success"])
        self.assert_no_action()

    def test_window_thread_or_final_identity_changed_is_refused(self):
        self.api.windows.side_effect = [[dict(self.main)], [dict(self.main, thread_id=8)]]
        self.assertFalse(self.call()["success"])
        self.api.windows.side_effect = lambda pid: [dict(row) for row in self.rows]
        self.api.matches.return_value = False
        self.assertFalse(self.call()["success"])
        self.assert_no_action()

    def test_new_instance_before_restore_is_refused(self):
        self.inventory.side_effect = [self.inventory.return_value,
            {"configured": [{"pid": 123}, {"pid": 456}], "uncertain": []}]
        self.assertFalse(self.call()["success"])
        self.assert_no_action()

    def test_modal_appearing_after_restore_prevents_activation(self):
        def restore(hwnd):
            self.restore(hwnd)
            self.rows.append(dict(self.main, hwnd=88, **{"class": "#32770"}))
            return True
        self.api.restore.side_effect = restore
        result = self.call()
        self.assertFalse(result["success"])
        self.assertTrue(result["restore_requested"])
        self.api.activate.assert_not_called()

    def test_restore_never_completes_is_bounded(self):
        self.api.restore.side_effect = lambda hwnd: True
        with patch.object(focus.time, "monotonic", side_effect=[0, 3]):
            result = self.call()
        self.assertFalse(result["success"])
        self.assertTrue(result["restore_requested"])
        self.assertFalse(result["restored"])
        self.api.activate.assert_not_called()

    def test_windows_denial_reports_restore_separately_without_retry(self):
        self.api.activate.side_effect = lambda hwnd: False
        with patch.object(focus.time, "monotonic", side_effect=[0, 0, 2]):
            result = self.call()
        self.assertFalse(result["success"])
        self.assertTrue(result["restored"])
        self.assertFalse(result["foreground_confirmed"])
        self.api.activate.assert_called_once_with(42)

    def test_foreground_same_pid_different_window_is_not_success(self):
        self.api.activate.side_effect = lambda hwnd: setattr(self, "foreground", 88)
        with patch.object(focus.time, "monotonic", side_effect=[0, 0, 2]):
            self.assertFalse(self.call()["success"])

    def test_claim_collision_is_refused_without_actions(self):
        with patch.object(emergency_stop, "emergency_claim", side_effect=OSError("busy")):
            self.assertFalse(self.call()["success"])
        self.factory.assert_not_called()
        self.assert_no_action()


class WindowAPITests(unittest.TestCase):
    def setUp(self):
        self.native = Mock()
        with patch.object(ctypes, "WinDLL", return_value=self.native, create=True):
            self.api = focus.WindowAPI()

    def test_responsiveness_refuses_either_hung_or_timeout(self):
        for hung, response, expected in ((1, 1, False), (0, 0, False), (0, 1, True)):
            self.native.IsHungAppWindow.return_value = hung
            self.native.SendMessageTimeoutW.return_value = response
            self.assertEqual(self.api.responsive(42), expected)

    def test_restore_is_asynchronous_and_foreground_has_no_input_fallback(self):
        self.native.ShowWindowAsync.return_value = 1
        self.native.SetForegroundWindow.return_value = 0
        self.assertTrue(self.api.restore(42))
        self.assertFalse(self.api.activate(42))
        self.native.ShowWindowAsync.assert_called_once_with(42, 9)
        self.native.SetForegroundWindow.assert_called_once_with(42)
        self.native.SendInput.assert_not_called()
        self.native.keybd_event.assert_not_called()
        self.native.AttachThreadInput.assert_not_called()


class PublicToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_schema_dispatches_under_bridge_lock_without_script(self):
        from mcp.server.fastmcp import FastMCP
        source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == "focus_altium")
        lock = asyncio.Lock()
        service = Mock()
        def call(exe, exchange, pid):
            self.assertTrue(lock.locked())
            return {"success": True, "target_pid": pid}
        service.focus.side_effect = call
        bridge = Mock(_command_lock=lock)
        bridge.config.altium_exe_path = "configured.exe"
        mcp = FastMCP("focus-test")
        env = {"mcp": mcp, "Context": object, "altium_bridge": bridge,
               "altium_focus": service, "EXCHANGE_DIR": "exchange", "json": json, "asyncio": asyncio}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "public_focus", "exec"), env)
        tools = await mcp.list_tools()
        schema = next(t.inputSchema for t in tools if t.name == "focus_altium")
        self.assertEqual(schema["properties"]["target_pid"]["type"], "integer")
        self.assertIn("target_pid", schema["required"])
        await env["focus_altium"](None, 123)
        service.focus.assert_called_once_with("configured.exe", "exchange", 123)
        bridge.execute_command.assert_not_called()
        entered, release = threading.Event(), threading.Event()
        def blocked_call(*args):
            entered.set()
            release.wait(5)
            return {"success": True}
        service.focus.side_effect = blocked_call
        task = asyncio.create_task(env["focus_altium"](None, 123))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            task.cancel()
            await asyncio.sleep(0.02)
            self.assertTrue(lock.locked())
            self.assertFalse(task.done())
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(lock.locked())


if __name__ == "__main__":
    unittest.main()
