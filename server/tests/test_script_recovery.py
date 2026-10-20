"""Offline recovery protocol tests. No Altium, GUI or real process launches."""
import ast
import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import script_recovery as recovery

ROOT = Path(__file__).resolve().parents[1]


class RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()

    @classmethod
    def tearDownClass(cls):
        cls.loop.close()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.clock = 0
        self.processes = []
        self.phase_results = {"stop": 0, "probe": 0}
        self.result_token = "current_token"
        self.write_result = True
        self.environment = {"ready": True, "target_pid": 10,
                            "foreground_confirmed": True, "ui_responsive": True}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(recovery, "_launches", {}))
        self.stack.enter_context(patch.object(recovery, "inspect_environment", return_value=self.environment))
        self.stack.enter_context(patch.object(recovery.time, "monotonic", side_effect=lambda: self.clock))
        self.stack.enter_context(patch.object(recovery.uuid, "uuid4", return_value=SimpleNamespace(hex="current_token")))
        self.stack.enter_context(patch.object(recovery.asyncio, "sleep", side_effect=self.sleep))
        self.launcher = self.stack.enter_context(patch.object(recovery.subprocess, "Popen", side_effect=self.launch))

    async def sleep(self, seconds):
        self.clock += seconds

    def launch(self, command, **kwargs):
        phase = "stop" if "EditScript:Stop" in command else "probe"
        process = Mock(pid=100 + len(self.processes))
        process.poll.return_value = self.phase_results[phase]
        self.processes.append(process)
        if phase == "probe" and self.write_result:
            (self.root / "recovery" / "current_token" / "result.json").write_text(
                json.dumps({"probe": "executor ok", "token": self.result_token}), encoding="utf-8")
        return process

    def run_recovery(self, **kwargs):
        return self.loop.run_until_complete(recovery.recover("C:/Altium/X2.EXE", self.root,
                                           timeout_seconds=1, dispatch_timeout_seconds=1, **kwargs))

    def assert_no_termination(self):
        for process in self.processes:
            process.kill.assert_not_called()
            process.terminate.assert_not_called()

    def test_stop_and_matching_probe_are_required_for_success(self):
        result = self.run_recovery()
        self.assertTrue(result["recovered"])
        self.assertEqual(len(self.processes), 2)
        self.assertEqual([item["exit_code"] for item in result["state"]["launches"]], [0, 0])
        self.assertFalse(result["native_transaction_idle_verified"])
        self.assertIsNone(recovery.blocked_state(self.root))
        for call in self.launcher.call_args_list:
            self.assertFalse(call.kwargs["shell"])
            self.assertEqual(call.kwargs["executable"], "C:/Altium/X2.EXE")
            for name in ("stdin", "stdout", "stderr"):
                self.assertEqual(call.kwargs[name], recovery.subprocess.DEVNULL)
            self.assertIn("creationflags", call.kwargs)
        self.assertNotIn("^|", self.launcher.call_args_list[1].args[0])
        self.assert_no_termination()

    def test_pending_stop_does_not_launch_probe_or_accumulate_on_retry(self):
        self.phase_results["stop"] = None
        first = self.run_recovery()
        self.assertTrue(first["blocked"])
        self.assertEqual(first["state"]["phase"], "stop_pending")
        for manual in (False, True):
            again = self.run_recovery(manual_stop_confirmed=manual)
            self.assertTrue(again["blocked"])
        self.assertEqual(self.launcher.call_count, 1)
        self.assert_no_termination()

    def test_nonzero_stop_never_launches_probe(self):
        self.phase_results["stop"] = 1
        result = self.run_recovery()
        self.assertFalse(result["recovered"])
        self.assertEqual(self.launcher.call_count, 1)
        self.assertIn("delivery unproven", result["error"])

    def test_pending_probe_retains_handle_and_blocks_new_launches(self):
        self.phase_results["probe"] = None
        result = self.run_recovery()
        self.assertFalse(result["recovered"])  # Even an early token cannot hide a live launcher.
        self.assertEqual(result["state"]["phase"], "probe_pending")
        self.run_recovery(manual_stop_confirmed=True)
        self.assertEqual(self.launcher.call_count, 2)
        self.assert_no_termination()

    def test_checkpoint_survives_server_restart_and_manual_mode_skips_stop(self):
        recovery._write_state(self.root, {"phase": "stop_pending", "launches": [{"pid": 99}]})
        result = self.run_recovery()
        self.assertTrue(result["blocked"])
        self.launcher.assert_not_called()
        # The process/window preflight (mocked here) must establish exactly one
        # configured process before an explicit manual confirmation can proceed.
        result = self.run_recovery(manual_stop_confirmed=True)
        self.assertTrue(result["recovered"])
        self.assertEqual(self.launcher.call_count, 1)
        self.assertNotIn("EditScript:Stop", self.launcher.call_args.args[0])

    def test_wrong_token_and_stale_generic_result_cannot_pass(self):
        self.result_token = "previous_call"
        (self.root / "sandbox_result.json").write_text('{"probe":"executor ok"}', encoding="utf-8")
        result = self.run_recovery(manual_stop_confirmed=True)
        self.assertFalse(result["recovered"])
        self.assertEqual(result["state"]["phase"], "probe_token_mismatch")
        self.assertEqual(recovery.blocked_state(self.root)["phase"], "probe_token_mismatch")

    def test_missing_probe_requires_manual_stop_without_retry(self):
        self.write_result = False
        first = self.run_recovery(manual_stop_confirmed=True)
        self.assertEqual(first["state"]["phase"], "probe_timeout")
        self.run_recovery()
        self.assertEqual(self.launcher.call_count, 1)

    def test_background_modal_ambiguous_or_unresponsive_preflight_never_launches(self):
        for reason in ("background", "save confirmation", "multiple X2 clients", "not responsive"):
            self.environment.update(ready=False, reason=reason)
            result = self.run_recovery(manual_stop_confirmed=True)
            self.assertTrue(result["blocked"])
            self.assertEqual(result["error"], reason)
        self.launcher.assert_not_called()

    def test_launch_error_leaves_checkpoint_and_no_retry(self):
        self.launcher.side_effect = OSError("launch failed")
        result = self.run_recovery()
        self.assertEqual(result["state"]["phase"], "failed_unresolved")
        self.run_recovery()
        self.assertEqual(self.launcher.call_count, 1)

    def test_cancelled_wait_retains_pending_handle_and_checkpoint(self):
        self.phase_results["stop"] = None
        async def cancelled(_):
            raise asyncio.CancelledError()
        with patch.object(recovery.asyncio, "sleep", side_effect=cancelled):
            with self.assertRaises(asyncio.CancelledError):
                self.run_recovery()
        self.assertEqual(recovery.blocked_state(self.root)["phase"], "cancelled_unresolved")
        self.assertTrue(self.run_recovery(manual_stop_confirmed=True)["blocked"])
        self.assertEqual(self.launcher.call_count, 1)
        self.assert_no_termination()

    def test_probe_source_only_writes_its_unique_result(self):
        self.run_recovery(manual_stop_confirmed=True)
        source = (self.root / "recovery/current_token/RecoveryProbe.pas").read_text(encoding="utf-8")
        self.assertIn("current_token", source)
        for forbidden in ("PCBServer", "SchServer", "Client.", "SendMessage", "RunProcess"):
            self.assertNotIn(forbidden, source)


class LockContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_waits_for_shared_lock_and_releases_it(self):
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        method = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                      and n.name == "recover_script_executor")
        method.decorator_list = []
        lock = asyncio.Lock()
        await lock.acquire()
        called = []
        async def recover(*args):
            self.assertTrue(lock.locked())
            called.append(args)
            return {"recovered": True}
        environment = dict(Context=object, json=json, EXCHANGE_DIR=Path("unused"),
            altium_bridge=SimpleNamespace(_command_lock=lock,
                config=SimpleNamespace(altium_exe_path="X2.EXE")),
            script_recovery=SimpleNamespace(recover=recover))
        exec(compile(ast.Module(body=[method], type_ignores=[]), "recovery_test", "exec"), environment)
        task = asyncio.create_task(environment["recover_script_executor"](None))
        await asyncio.sleep(0)
        self.assertFalse(called)
        lock.release()
        result = json.loads(await task)
        self.assertTrue(result["recovered"])
        self.assertFalse(lock.locked())

    async def test_bridge_and_generic_sandbox_reject_unresolved_recovery_before_dispatch(self):
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        bridge = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AltiumBridge")
        method = next(n for n in bridge.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "execute_command")
        sandbox = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_altium_script")
        sandbox.decorator_list = []
        invoked = []
        async def native(*args): invoked.append(args)
        obj = SimpleNamespace(_command_lock=asyncio.Lock(), _execute_command_locked=native)
        environment = dict(Dict=dict, Any=object, Context=object, json=json,
            EXCHANGE_DIR=Path("unused"), altium_bridge=obj, _run_altium_script_locked=native,
            script_recovery=SimpleNamespace(blocked_state=lambda _: {"phase": "stop_pending"}))
        exec(compile(ast.Module(body=[method, sandbox], type_ignores=[]), "lock_test", "exec"), environment)
        bridge_result = await environment["execute_command"](obj, "mutation", {})
        sandbox_result = json.loads(await environment["run_altium_script"](None, "anything"))
        self.assertEqual(bridge_result["mutation_state"], "not_dispatched")
        self.assertFalse(sandbox_result["success"])
        self.assertFalse(invoked)


class ReadOnlyPreflightTests(unittest.TestCase):
    def environment(self, *, modal=False, processes=(10,), recorded=()):
        windows = [101, 102] if modal else [101]
        user32 = SimpleNamespace(SendMessageTimeoutW=Mock(return_value=1))
        api = SimpleNamespace(OpenProcess=Mock(return_value=Mock()))
        gui = SimpleNamespace(GetForegroundWindow=Mock(return_value=101))
        process = SimpleNamespace(EnumProcesses=Mock(return_value=list(processes)),
            QueryFullProcessImageName=Mock(return_value="C:/Altium/X2.EXE"),
            GetWindowThreadProcessId=Mock(return_value=(1, 10)))
        dialogs = SimpleNamespace(top_windows=lambda: windows,
            window_text=lambda h: "Altium" if h == 101 else "Save confirmation",
            window_class=lambda h: "#32770" if h == 102 else "TMainForm")
        with patch.dict(sys.modules, {"win32gui": gui,
                                     "win32process": process, "dialogs": dialogs}), \
                patch.object(recovery, "_process_path", return_value="C:/Altium/X2.EXE") as read_path:
            with patch("ctypes.windll", SimpleNamespace(user32=user32), create=True):
                with patch.object(recovery.script_guard, "_window_process", return_value=(10, "C:/Altium/X2.EXE")):
                    result = recovery.inspect_environment("C:/Altium/X2.EXE", recorded)
        return result, user32, SimpleNamespace(OpenProcess=read_path)

    def test_any_modal_blocks_without_posting_or_answering_messages(self):
        result, user32, _ = self.environment(modal=True)
        self.assertFalse(result["ready"])
        self.assertEqual(result["dialog_handles"], [102])
        user32.SendMessageTimeoutW.assert_not_called()
        # No WM_CLOSE/SendInput/SetForegroundWindow functions exist in these
        # strict fakes; accidental dialog or focus mutation fails the test.

    def test_multiple_processes_block_before_window_interaction(self):
        result, user32, _ = self.environment(processes=(10, 11))
        self.assertFalse(result["ready"])
        self.assertEqual(result["configured_process_ids"], [10, 11])
        self.assertEqual(result["visible_application_process_ids"], [10])
        self.assertEqual(result["windowless_process_ids"], [11])
        self.assertIn("possible pending dispatch clients", result["reason"])
        user32.SendMessageTimeoutW.assert_not_called()

    def test_recorded_pending_pid_blocks_after_restart_even_if_path_unknown(self):
        result, user32, api = self.environment(processes=(10, 99), recorded=(99,))
        self.assertFalse(result["ready"])
        self.assertEqual(result["unresolved_launch_ids"], [99])
        api.OpenProcess.assert_not_called()
        user32.SendMessageTimeoutW.assert_not_called()

    def test_foreground_responsiveness_is_not_reported_as_native_idle(self):
        result, user32, _ = self.environment()
        self.assertTrue(result["ready"])
        self.assertFalse(result["native_idle_verified"])
        self.assertTrue(result["foreground_confirmed"])
        self.assertEqual(user32.SendMessageTimeoutW.call_args.args[1], 0)  # WM_NULL only.


class ProcessIdentityTests(unittest.TestCase):
    def test_process_identity_queries_read_only_and_closes_handle(self):
        def query(handle, flags, buffer, size):
            buffer.value = "C:/Altium/X2.EXE"
            return True
        kernel32 = SimpleNamespace(OpenProcess=Mock(return_value=123),
            QueryFullProcessImageNameW=Mock(side_effect=query), CloseHandle=Mock())
        with patch("ctypes.windll", SimpleNamespace(kernel32=kernel32), create=True):
            self.assertEqual(recovery._process_path(42), "C:/Altium/X2.EXE")
        kernel32.OpenProcess.assert_called_once_with(0x1000, False, 42)
        kernel32.CloseHandle.assert_called_once_with(123)
