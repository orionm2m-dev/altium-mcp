"""Offline completed-Stop continuation tests; no native process/window calls."""
import ast
import asyncio
from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import completed_stop_resume as resume
import emergency_stop
import script_recovery as recovery


class ResumeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.exe = self.root / "X2.EXE"
        self.exe.write_bytes(b"never execute")
        created = int((time.time() - 2) * 10000000) + 116444736000000000
        self.identity = {"pid": 123, "exe": emergency_stop._canonical(self.exe),
            "creation_filetime": created, "user_sid": "user", "running": True}
        self.environment = {"ready": True, "target_pid": 123, "configured_process_ids": [123],
            "foreground_confirmed": True, "ui_responsive": True, "dialog_handles": [],
            "process_windows": {123: [{"hwnd": 42, "class": "TDocumentForm"}]}}
        self.token = "a" * 32
        self.state = {"token": self.token, "phase": "blocked_after_stop",
            "manual_stop_confirmed": False, "fresh_instance_confirmed": False,
            "environment": self.environment,
            "launches": [{"phase": "stop", "pid": 456, "exit_code": 0}]}
        self.startup = {"success": True, "launched": True, "script_dispatched": False,
            "mutation_replayed": False, "exit_code": None, "status": "process_started_ui_unverified",
            "configured_exe": str(self.exe), "target_pid": 123, "identity": self.identity,
            "creation_filetime_decimal": str(created), "started_at_unix": time.time() - 3}
        self.latch = self.root / "script_recovery_state.json"
        self.start_path = self.root / "altium_startup.json"
        self.write_receipts()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.main = Mock()
        self.main.identity.side_effect = lambda: dict(self.identity)
        self.main.__enter__ = Mock(return_value=self.main)
        self.main.__exit__ = Mock(return_value=False)
        def factory(pid, **kwargs):
            if pid == 123:
                return self.main
            error = OSError("PID absent")
            error.winerror = 87
            raise error
        self.factory = self.stack.enter_context(patch.object(emergency_stop, "WinProcess", side_effect=factory))
        self.stack.enter_context(patch.object(emergency_stop, "_current_sid", return_value="user"))
        self.stack.enter_context(patch.object(recovery, "_launches", {}))
        self.window = {"hwnd": 42, "pid": 123, "thread_id": 7, "class": "TDocumentForm",
            "enabled": True, "minimized": False, "owner_hwnd": 0, "title": "Home", "tool_window": False}
        self.window_api = Mock()
        self.window_api.windows.side_effect = lambda pid: [dict(self.window)]
        self.window_api.responsive.return_value = True
        self.window_api.matches.return_value = True
        self.window_api.foreground.return_value = 42
        self.stack.enter_context(patch.object(resume.altium_focus, "WindowAPI", return_value=self.window_api))
        self.focus = self.stack.enter_context(patch.object(resume.altium_focus, "_focus_locked",
            return_value={"success": True, "window_after": self.window}))
        self.inventory = self.stack.enter_context(patch.object(resume.altium_focus.altium_lifecycle,
            "configured_instances", return_value={"configured": [{"pid": 123}], "uncertain": []}))
        self.environment_reader = self.stack.enter_context(patch.object(recovery, "inspect_environment", return_value=self.environment))
        self.launch = self.stack.enter_context(patch.object(recovery, "_launch", return_value=Mock(pid=789)))
        self.stack.enter_context(patch.object(recovery, "_wait_exit", AsyncMock(return_value=0)))
        actual_probe_files = recovery._probe_files
        def files(exchange, token):
            project, result = actual_probe_files(exchange, token)
            result.write_text(json.dumps({"probe": "executor ok", "token": token}), encoding="utf-8")
            return project, result
        self.stack.enter_context(patch.object(recovery, "_probe_files", side_effect=files))

    def write_receipts(self):
        self.start_path.write_text(json.dumps(self.startup), encoding="utf-8")
        self.latch.write_text(json.dumps(self.state), encoding="utf-8")
        # Explicit ordering avoids filesystem timestamp granularity differences.
        now = time.time()
        os.utime(self.start_path, (now - 1, now - 1))
        os.utime(self.latch, (now, now))

    async def call(self, **kwargs):
        return await resume.resume_locked(str(self.exe), self.root,
            kwargs.get("target_pid", 123), kwargs.get("recovery_token", self.token), 1, 1)

    async def test_completed_stop_focuses_then_one_unique_probe_without_stop(self):
        previous = self.latch.read_bytes()
        result = await self.call()
        self.assertTrue(result["recovered"], result)
        self.focus.assert_called_once_with(str(self.exe), 123)
        self.assertEqual(self.launch.call_count, 1)
        self.assertIn("ScriptingSystem:RunScript", self.launch.call_args.args[1])
        self.assertNotIn("EditScript:Stop", self.launch.call_args.args[1])
        state = json.loads(self.latch.read_text())
        self.assertEqual(state["phase"], "recovered")
        self.assertFalse(state["manual_stop_confirmed"])
        self.assertNotEqual(state["token"], self.token)
        evidence = state["verified_prior_stop_exit"]
        self.assertFalse(evidence["stop_redispatched"])
        self.assertFalse(evidence["stop_delivery_verified"])
        self.assertFalse(evidence["historical_stop_creation_verified"])
        self.assertFalse(evidence["historical_main_creation_verified_in_stop_receipt"])
        self.assertEqual(evidence["prior_latch_sha256"], resume._hash(previous))
        self.main.terminate.assert_not_called()

    async def test_wrong_token_pending_or_incomplete_stop_cannot_focus_or_probe(self):
        original = copy.deepcopy(self.state)
        changes = [{"phase": "stop_pending"}, {"manual_stop_confirmed": True},
            {"fresh_instance_confirmed": True}, {"probe_result_path": "old"},
            {"launches": [{"phase": "stop", "pid": 456, "exit_code": None}]},
            {"launches": [{"phase": "stop", "pid": 456, "exit_code": False}]},
            {"launches": [{"phase": "stop", "pid": 123, "exit_code": 0}]},
            {"launches": [{"phase": "probe", "pid": 456, "exit_code": 0}]}]
        for change in changes:
            self.state = dict(original, **change)
            self.write_receipts()
            before = self.latch.read_bytes()
            self.assertFalse((await self.call())["recovered"])
            self.assertEqual(self.latch.read_bytes(), before)
        self.state = original
        self.write_receipts()
        self.assertFalse((await self.call(recovery_token="b" * 32))["recovered"])
        self.focus.assert_not_called()
        self.launch.assert_not_called()

    async def test_current_main_must_match_owned_start_identity(self):
        self.main.identity.side_effect = lambda: dict(self.identity, creation_filetime=1)
        self.assertFalse((await self.call())["recovered"])
        self.focus.assert_not_called()
        self.launch.assert_not_called()

    async def test_newer_start_receipt_or_foreign_start_identity_is_refused(self):
        for changed in ({"started_at_unix": time.time() + 5}, {"success": False},
                {"configured_exe": str(self.root / "other.exe")}, {"creation_filetime_decimal": "wrong"}):
            original = dict(self.startup)
            self.startup.update(changed)
            self.write_receipts()
            self.assertFalse((await self.call())["recovered"])
            self.startup = original
        self.focus.assert_not_called()
        self.launch.assert_not_called()

    async def test_live_reused_stop_pid_or_unknown_pid_access_is_refused(self):
        stop = Mock()
        stop.__enter__ = Mock(return_value=stop)
        stop.__exit__ = Mock(return_value=False)
        stop.exit_observation.return_value = {"pid": 456, "exit_code": 259}
        self.factory.side_effect = lambda pid, **kw: self.main if pid == 123 else stop
        self.assertFalse((await self.call())["recovered"])
        def unknown(pid, **kwargs):
            if pid == 123:
                return self.main
            raise OSError("Access unknown")
        self.factory.side_effect = unknown
        self.assertFalse((await self.call())["recovered"])
        self.focus.assert_not_called()
        self.launch.assert_not_called()

    async def test_pending_tracked_launcher_is_refused(self):
        pending = Mock()
        pending.poll.return_value = None
        recovery._launches[999] = pending
        self.assertFalse((await self.call())["recovered"])
        self.focus.assert_not_called()
        self.launch.assert_not_called()

    async def test_focus_failure_keeps_exact_latch_and_no_dispatch(self):
        before = self.latch.read_bytes()
        self.focus.return_value = {"success": False, "restored": True}
        self.assertFalse((await self.call())["recovered"])
        self.assertEqual(self.latch.read_bytes(), before)
        self.launch.assert_not_called()

    async def test_receipt_or_main_change_during_focus_prevents_probe(self):
        def change(*args):
            self.start_path.write_text("{}")
            return {"success": True, "window_after": self.window}
        self.focus.side_effect = change
        self.assertFalse((await self.call())["recovered"])
        self.launch.assert_not_called()
        self.write_receipts()
        def change_main(*args):
            self.main.identity.side_effect = lambda: dict(self.identity, creation_filetime=1)
            return {"success": True, "window_after": self.window}
        self.focus.side_effect = change_main
        self.assertFalse((await self.call())["recovered"])
        self.launch.assert_not_called()

    async def test_uncertain_inventory_or_dialog_after_focus_prevents_probe(self):
        before = self.latch.read_bytes()
        self.inventory.return_value["uncertain"] = [{"pid": 999}]
        self.assertFalse((await self.call())["recovered"])
        self.assertEqual(self.latch.read_bytes(), before)
        self.inventory.return_value["uncertain"] = []
        self.environment_reader.return_value = dict(self.environment, ready=False, dialog_handles=[88])
        self.assertFalse((await self.call())["recovered"])
        self.assertEqual(self.latch.read_bytes(), before)
        self.launch.assert_not_called()

    async def test_unknown_modal_after_focus_is_refused_before_dispatch(self):
        self.window_api.windows.side_effect = lambda pid: [dict(self.window),
            dict(self.window, hwnd=88, **{"class": "UnknownModal"})]
        before = self.latch.read_bytes()
        self.assertFalse((await self.call())["recovered"])
        self.assertEqual(self.latch.read_bytes(), before)
        self.launch.assert_not_called()

    async def test_main_change_during_probe_keeps_latch_blocked(self):
        async def exited(*args):
            self.main.identity.side_effect = lambda: dict(self.identity, creation_filetime=1)
            return 0
        with patch.object(recovery, "_wait_exit", side_effect=exited):
            result = await self.call()
        self.assertFalse(result["recovered"])
        self.assertEqual(json.loads(self.latch.read_text())["phase"], "failed_unresolved")
        self.assertEqual(self.launch.call_count, 1)

    async def test_capability_is_single_use_and_does_not_dispatch(self):
        raw, startup_raw, state, identity, stop_pid = resume._receipts(
            str(self.exe), self.root, 123, self.token)
        proof = resume._VerifiedCompletedStop(str(self.exe), self.root, raw,
            startup_raw, self.main, identity, stop_pid, self.window)
        self.assertEqual(proof.consume(str(self.exe), self.root, state, self.environment)["exit_code"], 0)
        with self.assertRaises(ValueError):
            proof.consume(str(self.exe), self.root, state, self.environment)
        self.launch.assert_not_called()

    async def test_probe_cancellation_retains_unresolved_latch(self):
        with patch.object(recovery, "_wait_exit", AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await self.call()
        self.assertEqual(json.loads(self.latch.read_text())["phase"], "cancelled_unresolved")
        self.assertEqual(self.launch.call_count, 1)
        self.assertNotIn("EditScript:Stop", self.launch.call_args.args[1])

    async def test_failed_probe_keeps_blocked_latch_and_cannot_resume_again(self):
        with patch.object(recovery, "_wait_exit", AsyncMock(return_value=3)):
            self.assertFalse((await self.call())["recovered"])
        self.assertNotEqual(json.loads(self.latch.read_text())["phase"], "recovered")
        self.assertFalse((await self.call())["recovered"])
        self.assertEqual(self.launch.call_count, 1)


class PublicToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_schema_and_locking_do_not_claim_manual_stop(self):
        from mcp.server.fastmcp import FastMCP
        source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == "resume_completed_stop")
        lock = asyncio.Lock()
        bridge = Mock(_command_lock=lock)
        bridge.config.altium_exe_path = "configured.exe"
        mcp = FastMCP("resume-test")
        env = {"mcp": mcp, "Context": object, "altium_bridge": bridge,
               "emergency_stop": emergency_stop, "EXCHANGE_DIR": "unused", "json": json}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "public_resume", "exec"), env)
        schema = next(tool.inputSchema for tool in await mcp.list_tools()
                      if tool.name == "resume_completed_stop")
        self.assertEqual(schema["properties"]["target_pid"]["type"], "integer")
        self.assertEqual(schema["properties"]["recovery_token"]["type"], "string")
        self.assertNotIn("manual_stop_confirmed", schema["properties"])
        async def call(*args):
            self.assertTrue(lock.locked())
            return {"recovered": False, "stop_redispatched": False}
        claim = Mock()
        claim.__enter__ = Mock()
        claim.__exit__ = Mock(return_value=False)
        with patch.object(emergency_stop, "emergency_claim", return_value=claim), \
                patch.object(resume, "resume_locked", side_effect=call) as dispatch:
            await env["resume_completed_stop"](None, 123, "a" * 32)
        dispatch.assert_awaited_once_with("configured.exe", "unused", 123, "a" * 32, 30, 8)
        bridge.execute_command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
