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
import emergency_stop

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
        self.environment = {"ready": True, "target_pid": 10, "configured_process_ids": [10],
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

    def test_native_crash_preflight_never_dispatches_stop_or_probe(self):
        self.environment.update(ready=False, reason="Native exception dialog blocks recovery",
            script_errors=[{"kind": "native_crash", "text": "Access violation in ScriptingSystem.DLL"}])
        for manual in (False, True):
            with self.subTest(manual_stop_confirmed=manual):
                result = self.run_recovery(manual_stop_confirmed=manual)
                self.assertTrue(result["blocked"])
                self.assertEqual(result["environment"]["script_errors"], self.environment["script_errors"])
                self.launcher.assert_not_called()
                self.assertFalse((self.root / "recovery").exists())
                self.assertFalse((self.root / "script_recovery_state.json").exists())

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

    def install_fresh_emergency(self):
        previous = {"pid": 5, "creation_filetime": 133777777777777771,
                    "exe": emergency_stop._canonical("C:/Altium/X2.EXE"),
                    "user_sid": "test-user", "running": True}
        old = {"phase": "emergency_terminated", "require_fresh_instance": True,
               "emergency_identity": previous, "launches": []}
        recovery._write_state(self.root, old)
        self.fresh = dict(previous, pid=10, creation_filetime=133777777777777777)
        self.environment["process_windows"] = {10: [{"class": "TDocumentForm", "hwnd": 101}]}
        self.reader = Mock()
        self.reader.__enter__ = Mock(return_value=self.reader)
        self.reader.__exit__ = Mock(return_value=False)
        self.reader.identity.side_effect = lambda: dict(self.fresh)
        self.factory = self.stack.enter_context(patch.object(emergency_stop, "WinProcess", return_value=self.reader))
        self.old_exit = self.stack.enter_context(patch.object(emergency_stop, "recovery_block", return_value=None))
        self.stack.enter_context(patch.object(emergency_stop, "_current_sid", return_value="test-user"))
        return old

    def test_verified_fresh_emergency_probes_without_stop_or_manual_claim(self):
        old = self.install_fresh_emergency()
        result = self.run_recovery()
        self.assertTrue(result["recovered"])
        self.assertFalse(result["state"]["manual_stop_confirmed"])
        self.assertTrue(result["state"]["fresh_instance_confirmed"])
        self.assertEqual(result["state"]["fresh_instance_creation_filetime_decimal"], "133777777777777777")
        self.assertEqual(result["state"]["previous_emergency_identity"], old["emergency_identity"])
        self.assertEqual(result["state"]["fresh_instance_identity"], self.fresh)
        self.assertEqual(self.launcher.call_count, 1)
        self.assertNotIn("EditScript:Stop", self.launcher.call_args.args[0])
        self.old_exit.assert_called_once_with("C:/Altium/X2.EXE", old)
        self.factory.assert_called_once_with(10)
        self.assert_no_termination()

    def test_same_pid_with_new_creation_is_a_fresh_emergency_instance(self):
        self.install_fresh_emergency()
        self.environment.update(target_pid=5, configured_process_ids=[5],
                                process_windows={5: [{"class": "TDocumentForm", "hwnd": 101}]})
        self.fresh["pid"] = 5
        self.assertTrue(self.run_recovery()["recovered"])
        self.assertEqual(self.launcher.call_count, 1)

    def test_fresh_emergency_requires_old_exact_exit_and_ready_single_inventory(self):
        self.install_fresh_emergency()
        self.old_exit.return_value = "Exact emergency target is still alive or unknown"
        self.assertTrue(self.run_recovery()["blocked"])
        self.factory.assert_not_called()
        self.old_exit.return_value = None
        self.environment.update(ready=False, reason="modal or ambiguous startup")
        self.assertTrue(self.run_recovery()["blocked"])
        self.factory.assert_not_called()
        self.environment.update(ready=True, configured_process_ids=[10, 11])
        self.assertTrue(self.run_recovery()["blocked"])
        self.factory.assert_not_called()
        self.launcher.assert_not_called()

    def test_fresh_emergency_refuses_responsive_startup_window_without_main(self):
        self.install_fresh_emergency()
        self.environment["process_windows"] = {10: [{"class": "TStartUpWindow", "hwnd": 102}]}
        result = self.run_recovery()
        self.assertTrue(result["blocked"])
        self.assertIn("native main window", result["error"])
        self.factory.assert_not_called()
        self.launcher.assert_not_called()

    def test_fresh_emergency_rejects_same_unknown_or_wrong_current_identity(self):
        old = self.install_fresh_emergency()
        baseline = dict(self.fresh)
        for change in ({"exe": "C:/Other/X2.EXE"}, {"user_sid": "other"},
                       {"running": False}, {"creation_filetime": None}, {"pid": 11}):
            with self.subTest(change=change):
                self.fresh = dict(baseline, **change)
                self.assertTrue(self.run_recovery()["blocked"])
        self.environment.update(target_pid=5, configured_process_ids=[5],
                                process_windows={5: [{"class": "TDocumentForm", "hwnd": 101}]})
        self.fresh = dict(old["emergency_identity"])
        self.assertTrue(self.run_recovery()["blocked"])
        self.factory.side_effect = PermissionError("identity query denied")
        self.assertTrue(self.run_recovery()["blocked"])
        self.launcher.assert_not_called()
        self.assertEqual(recovery.blocked_state(self.root), old)

    def test_fresh_emergency_still_blocks_pending_launchers_and_wrong_probe(self):
        self.install_fresh_emergency()
        pending = Mock()
        pending.poll.return_value = None
        recovery._launches[99] = pending
        self.assertTrue(self.run_recovery()["blocked"])
        self.factory.assert_not_called()
        self.launcher.assert_not_called()
        recovery._launches.clear()
        self.result_token = "not_this_probe"
        result = self.run_recovery()
        self.assertEqual(result["state"]["phase"], "probe_token_mismatch")
        self.assertFalse(result["state"]["manual_stop_confirmed"])
        self.run_recovery()
        self.assertEqual(self.launcher.call_count, 1)

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
    def environment(self, *, modal=False, processes=(10,), recorded=(), modal_class="#32770", hidden=False, hides_during_capture=False):
        windows = [101, 102] if modal else [101]
        user32 = SimpleNamespace(SendMessageTimeoutW=Mock(return_value=1))
        api = SimpleNamespace(OpenProcess=Mock(return_value=Mock()))
        gui = SimpleNamespace(GetForegroundWindow=Mock(return_value=101))
        process = SimpleNamespace(EnumProcesses=Mock(return_value=list(processes)),
            QueryFullProcessImageName=Mock(return_value="C:/Altium/X2.EXE"),
            GetWindowThreadProcessId=Mock(return_value=(1, 10)))
        visibility = {"hidden": hidden}
        def children(hwnd):
            if hides_during_capture:
                visibility["hidden"] = True
            return [(103, "TStaticText", "Access violation in ScriptingSystem.DLL"),
                    (104, "TButton", "Send Report"), (105, "TButton", "Close")]
        dialogs = SimpleNamespace(top_windows=lambda: windows,
            is_visible=lambda h: not (visibility["hidden"] and h == 102),
            window_text=lambda h: "Altium" if h == 101 else "Save confirmation",
            window_class=lambda h: modal_class if h == 102 else "TMainForm",
            children=children)
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

    def test_exception_reporter_blocks_before_responsiveness_even_with_other_clients(self):
        for processes in ((10,), (10, 11)):
            with self.subTest(processes=processes):
                result, user32, _ = self.environment(modal=True, modal_class="TShowException",
                                                     processes=processes)
                self.assertFalse(result["ready"])
                self.assertEqual(result["script_errors"][0]["kind"], "native_crash")
                self.assertEqual(result["script_errors"][0]["text"],
                                 "Access violation in ScriptingSystem.DLL")
                self.assertIn("Native exception dialog", result["reason"])
                self.assertEqual(result["visible_application_process_ids"], [10])
                user32.SendMessageTimeoutW.assert_not_called()

    def test_hidden_exception_from_stale_enumeration_is_not_blocking_modal(self):
        result, user32, _ = self.environment(modal=True, modal_class="TShowException", hidden=True)
        self.assertTrue(result["ready"])
        self.assertEqual(result["dialog_handles"], [])
        self.assertEqual([w["hwnd"] for w in result["process_windows"][10]], [101])
        self.assertNotIn("script_errors", result)

    def test_exception_hidden_while_reading_text_is_removed_from_blocking_inventory(self):
        result, user32, _ = self.environment(modal=True, modal_class="TShowException",
                                             hides_during_capture=True)
        self.assertTrue(result["ready"])
        self.assertEqual(result["dialog_handles"], [])
        self.assertEqual([w["hwnd"] for w in result["process_windows"][10]], [101])
        self.assertNotIn("script_errors", result)

    def test_hidden_exception_does_not_remove_separate_windowless_client_block(self):
        result, user32, _ = self.environment(modal=True, modal_class="TShowException", hidden=True,
                                             processes=(10, 11))
        self.assertFalse(result["ready"])
        self.assertEqual(result["windowless_process_ids"], [11])
        self.assertNotIn("script_errors", result)
        self.assertIn("possible pending dispatch clients", result["reason"])

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
