"""Offline protocol tests; native process and window operations are all mocked."""
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
import emergency_stop as stop
import script_recovery as recovery


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.doc = self.root / "Working.PcbDoc"
        self.doc.write_bytes(b"disk document, unsaved memory unknown")
        self.exe = stop._canonical("C:/Altium/X2.EXE")
        self.identity = {"pid": 123, "creation_filetime": 987654321000000001,
                         "exe": self.exe, "user_sid": "S-1-owner", "running": True}
        self.processes = []
        self.clock = 1000.0
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(stop, "_plans", {}))
        self.stack.enter_context(patch.object(stop, "_current_sid", return_value="S-1-owner"))
        self.stack.enter_context(patch.object(stop.time, "time", side_effect=lambda: self.clock))
        self.factory = self.stack.enter_context(patch.object(stop, "WinProcess", side_effect=self.open_process))
        self.failure = self.stack.enter_context(patch.object(stop, "inspect_failure", return_value={
            "windows": [], "failure_evidence": [{"pid": 123, "kind": "breakpoint_stop_blocker"}]}))

    def open_process(self, pid, terminate_access=False):
        process = Mock()
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)
        process.identity.side_effect = lambda: dict(self.identity, pid=pid)
        process.wait.return_value = {"terminated_confirmed": True, "wait_status": 0,
                                     "exit_code": stop.EXIT_CODE}
        self.processes.append(process)
        return process

    def prepare(self, **overrides):
        args = dict(exe=self.exe, exchange=self.root, target_pid=123,
                    backup_paths=[str(self.doc)], acknowledge_managed_session=True)
        args.update(overrides)
        return stop.prepare(**args)

    def terminate(self, token, **overrides):
        args = dict(exe=self.exe, exchange=self.root, target_pid=123,
                    plan_token=token, acknowledge_unsaved_loss=True)
        args.update(overrides)
        return stop.terminate(**args)

    def assert_not_stopped(self):
        for process in self.processes:
            process.terminate.assert_not_called()

    def test_prepare_disk_only_and_single_same_handle_stop(self):
        prepared = self.prepare()
        self.assertTrue(prepared["success"])
        plan = prepared["plan"]
        self.assertEqual(plan["unsaved_state"], "unknown")
        self.assertEqual(Path(plan["backups"][0]["backup"]).read_bytes(), self.doc.read_bytes())
        self.assert_not_stopped()
        result = self.terminate(prepared["plan_token"])
        self.assertTrue(result["terminated_confirmed"])
        self.assertEqual(len(self.processes), 2)  # one preparation, one termination handle
        self.processes[0].terminate.assert_not_called()
        self.processes[1].identity.assert_called_once()
        self.processes[1].terminate.assert_called_once_with(stop.EXIT_CODE)
        self.processes[1].wait.assert_called_once_with(5000)
        self.assertTrue(recovery.blocked_state(self.root)["require_fresh_instance"])
        self.assertFalse(self.terminate(prepared["plan_token"])["success"])

    def attestation(self):
        path = self.root / "prior_failure.json"
        path.write_text('{"historical_error": "debugger stopped at breakpoint"}', encoding="utf-8")
        return {"reason": "The managed debugging session remains unresolved after the recorded failure.",
                "prior_receipt_path": str(path), "prior_receipt_sha256": stop._hash(path)}

    def test_explicit_attestation_is_operator_authorization_not_live_detection(self):
        self.failure.return_value["failure_evidence"] = []
        prepared = self.prepare(failure_attestation=self.attestation())
        self.assertTrue(prepared["success"])
        qualification = prepared["qualification"]
        self.assertEqual(qualification["kind"], "caller_attested_abandoned_debugger")
        self.assertIs(qualification["live_failure_verified"], False)
        self.assertEqual(prepared["plan"]["observed"]["failure_evidence"], [])
        self.assertIsNone(prepared["plan"]["native_incident_path"])
        self.assertFalse(self.terminate(prepared["plan_token"], acknowledge_unsaved_loss=False)["success"])
        result = self.terminate(prepared["plan_token"])
        self.assertTrue(result["success"])
        self.assertEqual(result["qualification"], qualification)
        self.assertIs(result["qualification"]["live_failure_verified"], False)
        self.assertEqual(result["observed_before_stop"]["failure_evidence"], [])
        self.assertFalse(self.terminate(prepared["plan_token"])["success"])

    def test_attestation_does_not_replace_managed_acknowledgement(self):
        self.failure.return_value["failure_evidence"] = []
        self.assertFalse(self.prepare(failure_attestation=self.attestation(),
                                     acknowledge_managed_session=False)["success"])
        self.factory.assert_not_called()

    def test_attestation_missing_unknown_empty_or_invalid_fields_fail_closed(self):
        self.failure.return_value["failure_evidence"] = []
        valid = self.attestation()
        candidates = [{}, {"reason": "only reason"}, dict(valid, unsupported=True),
                      dict(valid, reason="   "), dict(valid, reason=42),
                      dict(valid, prior_receipt_sha256="0" * 64),
                      dict(valid, prior_receipt_sha256="not a hash"),
                      dict(valid, prior_receipt_path="relative.json"),
                      dict(valid, prior_receipt_path=str(self.root / "absent.json")),
                      dict(valid, prior_receipt_path=str(self.root))]
        for value in candidates:
            with self.subTest(value=value):
                self.assertFalse(self.prepare(failure_attestation=value)["success"])
        self.assert_not_stopped()

    def test_attested_receipt_change_or_removal_before_stop_refuses(self):
        self.failure.return_value["failure_evidence"] = []
        for remove in (False, True):
            with self.subTest(remove=remove):
                attestation = self.attestation()
                prepared = self.prepare(failure_attestation=attestation)
                path = Path(attestation["prior_receipt_path"])
                if remove:
                    path.unlink()
                else:
                    path.write_text("new evidence", encoding="utf-8")
                result = self.terminate(prepared["plan_token"])
                self.assertFalse(result["success"])
                self.assertFalse(result["termination_requested"])
        self.assert_not_stopped()

    def test_attestation_keeps_pid_generation_expiry_and_exact_pid_guards(self):
        self.failure.return_value["failure_evidence"] = []
        for mode in ("pid", "generation", "expiry"):
            with self.subTest(mode=mode):
                self.clock += 301
                prepared = self.prepare(failure_attestation=self.attestation())
                if mode == "generation":
                    self.identity["creation_filetime"] += 1
                if mode == "expiry":
                    self.clock += 301
                result = self.terminate(prepared["plan_token"], target_pid=999 if mode == "pid" else 123)
                self.assertFalse(result["success"])
        self.assert_not_stopped()

    def test_native_incident_persists_identity_time_and_observation_if_backup_fails(self):
        result = self.prepare(backup_paths=[])
        self.assertFalse(result["success"])
        records = list((self.root / "emergency_stop" / "incidents").glob("*.json"))
        self.assertEqual(len(records), 1)
        incident = json.loads(records[0].read_text(encoding="utf-8"))
        self.assertEqual(incident["identity"], self.identity)
        self.assertEqual(incident["capture_time_unix"], self.clock)
        self.assertTrue(incident["live_failure_verified"])
        self.assertEqual(incident["observed"], self.failure.return_value)
        self.assert_not_stopped()

    def test_attestation_never_weakens_executable_or_owner_gate(self):
        self.failure.return_value["failure_evidence"] = []
        attestation = self.attestation()
        self.identity["exe"] = stop._canonical("C:/other.exe")
        self.assertFalse(self.prepare(failure_attestation=attestation)["success"])
        self.identity["exe"] = self.exe
        self.identity["user_sid"] = "other"
        self.assertFalse(self.prepare(failure_attestation=attestation)["success"])
        self.assert_not_stopped()

    def test_managed_and_unsaved_acknowledgements_required(self):
        self.assertFalse(self.prepare(acknowledge_managed_session=False)["success"])
        self.factory.assert_not_called()
        prepared = self.prepare()
        self.assertFalse(self.terminate(prepared["plan_token"], acknowledge_unsaved_loss=False)["success"])
        self.assert_not_stopped()
        self.assertTrue(self.terminate(prepared["plan_token"])["success"])

    def test_no_failure_even_with_unrelated_old_latch_refuses(self):
        recovery._write_state(self.root, {"phase": "probe_timeout", "target_pid": 999})
        self.failure.return_value["failure_evidence"] = []
        result = self.prepare()
        self.assertFalse(result["success"])
        self.assert_not_stopped()
        self.assertFalse((self.root / "emergency_stop").exists())

    def test_unexpected_executable_or_user_refuses(self):
        for field, value in (("exe", stop._canonical("C:/Other/X2.EXE")), ("user_sid", "S-1-other")):
            with self.subTest(field=field):
                old = self.identity[field]
                self.identity[field] = value
                self.assertFalse(self.prepare()["success"])
                self.identity[field] = old
        self.assert_not_stopped()

    def test_pretermination_failure_allows_immediate_explicit_new_preparation(self):
        prepared = self.prepare()
        self.doc.write_bytes(b"intentional saved update")
        self.assertFalse(self.terminate(prepared["plan_token"])["success"])
        state = recovery.blocked_state(self.root)
        self.assertIs(state["termination_requested"], False)
        fresh = self.prepare()
        self.assertTrue(fresh["success"])
        self.assertNotEqual(fresh["plan_token"], prepared["plan_token"])
        self.assert_not_stopped()

    def test_pid_reuse_is_not_the_prepared_process(self):
        prepared = self.prepare()
        self.identity["creation_filetime"] += 1
        result = self.terminate(prepared["plan_token"])
        self.assertFalse(result["success"])
        self.assertIn("identity differs", result["error"])
        self.assert_not_stopped()

    def test_pid_or_expiry_mismatch_refuses_and_consumes_token(self):
        prepared = self.prepare()
        result = self.terminate(prepared["plan_token"], target_pid=999)
        self.assertFalse(result["success"])
        self.assertFalse(self.terminate(prepared["plan_token"])["success"])
        self.clock += 301
        prepared = self.prepare()
        self.clock += 301
        self.assertFalse(self.terminate(prepared["plan_token"])["success"])
        self.assert_not_stopped()

    def test_source_or_backup_or_checkpoint_modification_refuses(self):
        for changed in ("source", "backup", "checkpoint"):
            with self.subTest(changed=changed):
                self.clock += 301
                prepared = self.prepare()
                plan = prepared["plan"]
                path = Path(plan["checkpoint"] if changed == "checkpoint" else plan["backups"][0][changed])
                original = path.read_bytes()
                path.write_bytes(original + b"changed")
                result = self.terminate(prepared["plan_token"])
                self.assertFalse(result["success"])
                path.write_bytes(original)
        self.assert_not_stopped()

    def test_failure_disappeared_invalidates_plan(self):
        prepared = self.prepare()
        self.failure.return_value["failure_evidence"] = []
        result = self.terminate(prepared["plan_token"])
        self.assertIn("Failure disappeared", result["error"])
        self.assert_not_stopped()

    def test_latch_changed_refuses(self):
        prepared = self.prepare()
        recovery._write_state(self.root, {"phase": "emergency_prepared", "emergency_token": "other"})
        self.assertFalse(self.terminate(prepared["plan_token"])["success"])
        self.assertEqual(recovery.blocked_state(self.root)["emergency_token"], "other")
        self.assert_not_stopped()

    def test_second_hash_validation_catches_change_during_evidence_read(self):
        prepared = self.prepare()
        def change(*unused):
            self.doc.write_bytes(b"modified while collecting evidence")
            return {"failure_evidence": [{"pid": 123, "kind": "native_crash"}]}
        self.failure.side_effect = change
        self.assertFalse(self.terminate(prepared["plan_token"])["success"])
        self.assert_not_stopped()

    def test_timeout_is_unconfirmed_and_token_cannot_repeat(self):
        prepared = self.prepare()
        original = self.open_process
        def timeout(*args, **kwargs):
            process = original(*args, **kwargs)
            process.wait.return_value = {"terminated_confirmed": False, "wait_status": 258, "exit_code": None}
            return process
        self.factory.side_effect = timeout
        result = self.terminate(prepared["plan_token"])
        self.assertTrue(result["termination_requested"])
        self.assertFalse(result["terminated_confirmed"])
        self.assertEqual(recovery.blocked_state(self.root)["phase"], "emergency_exit_unconfirmed")
        self.assertFalse(self.prepare()["success"])
        self.assertFalse(self.terminate(prepared["plan_token"])["success"])
        self.assertEqual(sum(p.terminate.call_count for p in self.processes), 1)

    def test_os_failure_or_cancellation_keeps_latch(self):
        original = self.open_process
        for error in (OSError("denied"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                self.clock += 301
                self.factory.side_effect = original
                prepared = self.prepare()
                def failure(*args, **kwargs):
                    process = original(*args, **kwargs)
                    def stop_failure(*unused):
                        self.assertEqual(recovery.blocked_state(self.root)["phase"], "emergency_termination_requested")
                        raise error
                    process.terminate.side_effect = stop_failure
                    return process
                self.factory.side_effect = failure
                if isinstance(error, asyncio.CancelledError):
                    with self.assertRaises(asyncio.CancelledError):
                        self.terminate(prepared["plan_token"])
                else:
                    self.assertFalse(self.terminate(prepared["plan_token"])["success"])
                self.assertEqual(recovery.blocked_state(self.root)["phase"], "emergency_failed_unresolved")
                self.assertNotIn(prepared["plan_token"], stop._plans)

    def test_active_plan_and_held_os_claim_refuse(self):
        prepared = self.prepare()
        self.assertTrue(prepared["success"])
        self.assertFalse(self.prepare()["success"])
        with stop.emergency_claim(self.root):
            self.assertFalse(self.terminate(prepared["plan_token"])["success"])
            self.assertFalse(self.prepare()["success"])
        self.assert_not_stopped()
        self.assertTrue(self.terminate(prepared["plan_token"])["success"])

    def test_backup_arguments_are_explicit_files(self):
        for paths in ([], [str(self.root)], ["relative.PcbDoc"], [str(self.root / "missing.PcbDoc")], [str(self.root / "secret.txt")]):
            with self.subTest(paths=paths):
                self.assertFalse(self.prepare(backup_paths=paths)["success"])
        self.assert_not_stopped()

    def test_same_instance_recovery_never_dispatches_even_manual_mode(self):
        self.prepare()
        with patch.object(recovery, "_launch") as launch, patch.object(recovery, "inspect_environment") as inspect:
            result = asyncio.run(recovery.recover(self.exe, self.root, manual_stop_confirmed=True))
            self.assertTrue(result["blocked"])
            self.assertIn("still running", result["error"])
            launch.assert_not_called()
            inspect.assert_not_called()

    def test_fresh_creation_identity_permits_existing_recovery_preflight(self):
        prepared = self.prepare()
        state = recovery.blocked_state(self.root)
        self.identity["creation_filetime"] += 1  # Same PID, new process is distinct.
        self.assertIsNone(stop.recovery_block(self.exe, state))
        self.factory.side_effect = PermissionError("identity unavailable")
        self.assertIn("Cannot prove", stop.recovery_block(self.exe, state))

    def test_absent_target_permits_probe_but_unknown_error_refuses(self):
        self.prepare()
        state = recovery.blocked_state(self.root)
        missing = OSError("invalid PID")
        missing.winerror = 87
        self.factory.side_effect = missing
        self.assertIsNone(stop.recovery_block(self.exe, state))


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.dialogs = SimpleNamespace(top_windows=Mock(return_value=[10]), is_visible=Mock(return_value=True),
            window_class=Mock(return_value="TShowException"), window_text=Mock(return_value="Error"),
            children=Mock(return_value=[(1, "TLabel", "Access violation in ScriptingSystem.DLL")]))
        self.stack.enter_context(patch.dict(sys.modules, {"dialogs": self.dialogs}))
        self.stack.enter_context(patch.object(recovery, "_launches", {}))
        self.exe = stop._canonical("C:/Altium/X2.EXE")
        self.identity = {"pid": 123, "creation_filetime": 9, "exe": self.exe, "user_sid": "user"}
        self.window_process = self.stack.enter_context(patch.object(stop.script_guard, "_window_process", return_value=(123, self.exe)))
        self.hung = self.stack.enter_context(patch.object(stop, "_hung_window", return_value=False))

    def test_crash_and_exact_breakpoint_error_are_read_only_evidence(self):
        self.assertEqual(stop.inspect_failure(self.exe, self.identity)["failure_evidence"][0]["kind"], "native_crash")
        self.dialogs.window_class.return_value = "TMessageForm"
        self.dialogs.children.return_value = [(1, "TLabel", "Altium Designer cannot close the current document when a script is stopped on a breakpoint.")]
        self.assertEqual(stop.inspect_failure(self.exe, self.identity)["failure_evidence"][0]["kind"], "breakpoint_stop_blocker")
        self.assertFalse(hasattr(self.dialogs, "click"))

    def test_other_pid_hidden_crash_and_information_do_not_authorize(self):
        self.window_process.return_value = (999, self.exe)
        self.assertFalse(stop.inspect_failure(self.exe, self.identity)["failure_evidence"])
        self.window_process.return_value = (123, self.exe)
        self.dialogs.is_visible.return_value = False
        self.assertFalse(stop.inspect_failure(self.exe, self.identity)["failure_evidence"])
        self.dialogs.is_visible.return_value = True
        self.dialogs.window_class.return_value = "TMessageForm"
        self.dialogs.window_text.return_value = "Information"
        self.dialogs.children.return_value = [(1, "TLabel", "There is another instance busy")]
        self.assertFalse(stop.inspect_failure(self.exe, self.identity)["failure_evidence"])

    def test_hung_main_window_qualifies_but_save_modal_does_not(self):
        self.hung.return_value = True
        self.dialogs.window_class.return_value = "TMainForm"
        self.assertEqual(stop.inspect_failure(self.exe, self.identity)["failure_evidence"][0]["kind"], "hung_ui")
        self.dialogs.window_class.return_value = "#32770"
        self.dialogs.window_text.return_value = "Save"
        self.assertFalse(stop.inspect_failure(self.exe, self.identity)["failure_evidence"])

    def test_hidden_during_child_read_is_ignored(self):
        self.dialogs.is_visible.side_effect = [True, False]
        self.assertFalse(stop.inspect_failure(self.exe, self.identity)["failure_evidence"])


class SourceContractTests(unittest.TestCase):
    def test_public_tool_uses_common_lock_and_only_known_actions(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "emergency_stop_altium")
        body = ast.unparse(function)
        self.assertIn("async with altium_bridge._command_lock", body)
        self.assertIn("emergency_stop.prepare", body)
        self.assertIn("emergency_stop.terminate", body)
        self.assertNotIn("subprocess", body)

    def test_backend_terminate_and_wait_use_existing_handle(self):
        # Native adapter bypassed completely; assert exact handles passed to APIs.
        process = object.__new__(stop.WinProcess)
        process.handle = 0x123456789
        process.k = Mock()
        process._check = Mock()
        process.terminate()
        process.k.TerminateProcess.assert_called_once_with(0x123456789, stop.EXIT_CODE)
        process.k.OpenProcess.assert_not_called()


if __name__ == "__main__":
    unittest.main()
