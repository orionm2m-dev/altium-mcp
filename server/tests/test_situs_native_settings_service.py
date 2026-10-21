"""Offline service protocol tests. Every native/process observation is mocked."""
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
import situs_native_settings_service as service

PID = 123
CREATED = "134370000000000123"
PCB = r"C:\boards\sample.PcbDoc"
EXE = r"C:\Altium\X2.EXE"
OP = "a" * 32


class PublicFiletimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_installed_fastmcp_preserves_prefixed_token_through_validation(self):
        from mcp.server.fastmcp.utilities.func_metadata import func_metadata

        def public_boundary(expected_creation_filetime: str) -> str:
            return service.normalize_public_filetime(expected_creation_filetime)

        metadata = func_metadata(public_boundary)
        for decimal in (CREATED, str(2 ** 64 - 1)):
            arguments = {"expected_creation_filetime": "ft:" + decimal}
            self.assertEqual(metadata.pre_parse_json(arguments), arguments)
            result = await metadata.call_fn_with_arg_validation(
                public_boundary, False, arguments, None)
            self.assertEqual(result, decimal)
            self.assertEqual(int(result), int(decimal))

    def test_installed_fastmcp_demonstrates_unprefixed_decimal_coercion(self):
        from mcp.server.fastmcp.utilities.func_metadata import func_metadata
        from pydantic import ValidationError

        def public_boundary(expected_creation_filetime: str) -> str:
            return expected_creation_filetime

        metadata = func_metadata(public_boundary)
        parsed = metadata.pre_parse_json({"expected_creation_filetime": CREATED})
        self.assertIs(type(parsed["expected_creation_filetime"]), int)
        with self.assertRaises(ValidationError):
            metadata.arg_model.model_validate(parsed)

    def test_public_normalizer_rejects_numeric_unprefixed_or_malformed_values(self):
        for value in (True, False, int(CREATED), float(CREATED), CREATED, "0", "ft:0",
                      "ft:01", "FT:" + CREATED, " ft:" + CREATED, "ft:" + CREATED + " ",
                      "ft:1.0", "ft:1e17", "ft:-1", "ft:+1", "ft:" + str(2 ** 64), None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    service.normalize_public_filetime(value)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lock = asyncio.Lock()
        self.clock = 0
        self.mode = "inspect"
        self.launches = []
        self.native_extra = {}
        self.identity = dict(pid=PID, creation_filetime=int(CREATED),
                             exe=service.emergency_stop._canonical(EXE), user_sid="test", running=True)
        self.process = Mock()
        self.process.identity.side_effect = lambda: dict(self.identity)
        self.process.__enter__ = Mock(return_value=self.process)
        self.process.__exit__ = Mock(return_value=False)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(service.emergency_stop, "WinProcess", return_value=self.process))
        self.validator = self.stack.enter_context(patch.object(service.emergency_stop, "_valid_identity"))
        self.stack.enter_context(patch.object(service.script_recovery, "_launches", {}))
        self.environment = self.stack.enter_context(patch.object(service.script_recovery, "inspect_environment",
            return_value={"ready": True, "target_pid": PID, "configured_process_ids": [PID]}))
        self.stack.enter_context(patch.object(service.script_guard, "blocking_crash_report", return_value=None))
        self.launch = self.stack.enter_context(patch.object(service.script_recovery, "_launch", side_effect=self.launch_native))
        self.stack.enter_context(patch.object(service, "time", SimpleNamespace(monotonic=lambda: self.clock)))
        self.stack.enter_context(patch.object(service.asyncio, "sleep", side_effect=self.sleep))

    async def sleep(self, seconds):
        self.clock += seconds
        if self.mode == "cancel":
            raise asyncio.CancelledError()

    def launch_native(self, exe, argument):
        self.assertTrue(self.lock.locked())
        self.assertEqual(service.script_recovery.blocked_state(self.root)["phase"], "native_settings_pending")
        self.assertEqual(exe, EXE)
        self.assertNotIn("^|", argument)
        self.assertIn('ProcName="SitusSettings>RunSitusSettings"', argument)
        folder = self.root / "situs_native_settings" / self.operation
        request = json.loads((folder / "request.json").read_text())["request"]
        value = dict(request, stage="completed", success=True, before=False, after=False,
                     setter_attempted=False, changed=False, already_satisfied=False, error="")
        if self.mode == "set":
            value.update(after=True, setter_attempted=True, changed=True)
        elif self.mode == "noop":
            value.update(before=True, after=True, already_satisfied=True)
        elif self.mode == "stale":
            value.update(stage="expected_state_mismatch", success=False, after=None, error="stale_expected_state")
        elif self.mode == "guard":
            value.update(stage="guard_failed", success=False, before=None, after=None, error="document mismatch")
        elif self.mode == "partial":
            value.update(stage="before_setter", success=False, after=None, setter_attempted=True)
        value.update(self.native_extra)
        if self.mode not in {"missing", "cancel"}:
            (folder / "native.json").write_text("{" if self.mode == "malformed" else json.dumps(value), encoding="utf-8")
        process = Mock(pid=456)
        process.poll.return_value = None if self.mode == "pending_launcher" else (2 if self.mode == "bad_exit" else 0)
        self.launches.append(process)
        return process

    async def run_control(self, **changes):
        args = dict(action="inspect", pcb_path=PCB, target_pid=PID, expected_creation_filetime=CREATED,
                    operation_id=OP, timeout_seconds=1)
        args.update(changes)
        self.operation = args["operation_id"]
        return await service.control(EXE, self.root, self.lock, **args)

    async def test_inspect_uses_fixed_project_serialization_and_native_readback(self):
        result = await self.run_control()
        self.assertTrue(result["success"])
        self.assertTrue(result["process_identity_verified"])
        self.assertTrue(result["setting_readback_verified"])
        self.assertFalse(result["pid_verified_by_native"])
        self.assertFalse(result["persistence_verified"])
        self.assertIsNone(service.script_recovery.blocked_state(self.root))
        self.launch.assert_called_once()
        source = (self.root / "situs_native_settings" / OP / "SitusSettings.pas").read_text()
        self.assertIn("procedure RunSitusSettings;", source)
        self.assertNotIn("Options.LockPreRoutes :=", source)
        self.assertNotIn("Sandbox", source)
        self.assertEqual(self.process.identity.call_count, 3)
        self.assertEqual(self.environment.call_count, 3)

    async def test_set_compare_readback_and_exact_request(self):
        self.mode = "set"
        result = await self.run_control(action="set", expected_checked=False, checked=True)
        self.assertTrue(result["success"])
        self.assertTrue(result["native_receipt"]["changed"])
        source = (self.root / "situs_native_settings" / OP / "SitusSettings.pas").read_text()
        self.assertEqual(source.count("Options.LockPreRoutes := True;"), 1)

    async def test_native_idempotent_set_succeeds_without_assignment(self):
        self.mode = "noop"
        result = await self.run_control(action="set", expected_checked=False, checked=True)
        self.assertTrue(result["success"])
        self.assertTrue(result["native_receipt"]["already_satisfied"])
        self.assertFalse(result["native_receipt"]["setter_attempted"])

    async def test_stale_expected_no_input_does_not_wedge_recovery(self):
        self.mode = "stale"
        result = await self.run_control(action="set", expected_checked=True, checked=True)
        self.assertFalse(result["success"])
        self.assertFalse(result["outcome_unknown"])
        self.assertEqual(result["error"], "stale_expected_state")
        self.assertIsNone(service.script_recovery.blocked_state(self.root))

    async def test_native_document_guard_refusal_does_not_count_as_readback(self):
        self.mode = "guard"
        result = await self.run_control()
        self.assertFalse(result["setting_readback_verified"])
        self.assertFalse(result["current_state_rechecked"])
        self.assertFalse(result["outcome_unknown"])
        self.assertIsNone(service.script_recovery.blocked_state(self.root))

    async def test_repeated_id_returns_historical_receipt_without_native_calls(self):
        first = await self.run_control()
        self.assertTrue(first["success"])
        self.process.identity.reset_mock()
        second = await self.run_control()
        self.assertTrue(second["replayed"])
        self.assertFalse(second["current_state_rechecked"])
        self.launch.assert_called_once()
        self.process.identity.assert_not_called()

    async def test_same_id_cannot_change_request(self):
        await self.run_control()
        second = await self.run_control(pcb_path=r"C:\boards\other.PcbDoc")
        self.assertFalse(second["success"])
        self.assertIn("different request", second["error"])
        self.launch.assert_called_once()

    async def test_pending_or_invalid_native_receipts_are_unknown_and_latched(self):
        for mode in ("missing", "malformed", "pending_launcher", "bad_exit"):
            self.mode = mode
            result = await self.run_control(operation_id=hex(len(self.launches) + 1)[2:].zfill(32))
            self.assertFalse(result["success"], mode)
            self.assertTrue(result["outcome_unknown"], mode)
            self.assertIsNotNone(service.script_recovery.blocked_state(self.root))
            (self.root / "script_recovery_state.json").unlink()  # Test fixture only.
        for process in self.launches:
            process.terminate.assert_not_called()
            process.kill.assert_not_called()

    async def test_partial_setter_is_unknown_and_never_replayed(self):
        self.mode = "partial"
        first = await self.run_control(action="set", expected_checked=False, checked=True)
        self.assertTrue(first["outcome_unknown"])
        second = await self.run_control(action="set", expected_checked=False, checked=True)
        self.assertTrue(second["replayed"])
        self.launch.assert_called_once()

    async def test_wrong_native_operation_id_is_not_success(self):
        self.native_extra = {"operation_id": "b" * 32}
        result = await self.run_control()
        self.assertTrue(result["outcome_unknown"])
        self.assertFalse(result["success"])

    async def test_existing_recovery_block_prevents_new_dispatch(self):
        service.script_recovery._write_state(self.root, {"phase": "probe_pending"})
        result = await self.run_control()
        self.assertTrue(result["blocked"])
        self.launch.assert_not_called()

    async def test_ambiguous_or_wrong_pid_environment_blocks_before_dispatch(self):
        for env in ({"ready": True, "target_pid": PID, "configured_process_ids": [PID, 999]},
                    {"ready": True, "target_pid": 999, "configured_process_ids": [999]}):
            self.environment.return_value = env
            result = await self.run_control()
            self.assertFalse(result["dispatch_attempted"])
        self.launch.assert_not_called()

    async def test_identity_change_immediately_before_launch_refuses(self):
        self.process.identity.side_effect = [self.identity, dict(self.identity, creation_filetime=int(CREATED)+1)]
        result = await self.run_control()
        self.assertFalse(result["dispatch_attempted"])
        self.launch.assert_not_called()

    async def test_identity_change_after_launch_remains_unknown_and_latched(self):
        self.process.identity.side_effect = [self.identity, self.identity, dict(self.identity, creation_filetime=int(CREATED)+1)]
        result = await self.run_control()
        self.assertTrue(result["outcome_unknown"])
        self.assertFalse(result["success"])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))

    async def test_invalid_filetime_types_and_unsafe_requests_never_launch(self):
        for changes in ({"expected_creation_filetime": int(CREATED)}, {"target_pid": True},
                        {"expected_creation_filetime": "01"}, {"expected_creation_filetime": str(2**64)},
                        {"timeout_seconds": 31}, {"operation_id": "../x"},
                        {"action": "set", "expected_checked": False, "checked": 1}):
            result = await self.run_control(**changes)
            self.assertFalse(result["success"])
            self.assertFalse(result["dispatch_attempted"])
        self.launch.assert_not_called()

    async def test_preflight_failure_never_dispatches_or_sets_recovery_latch(self):
        with patch.object(service.script_lint, "lint", return_value={"success": False}):
            result = await self.run_control()
        self.assertFalse(result["dispatch_attempted"])
        self.assertIsNone(service.script_recovery.blocked_state(self.root))
        self.launch.assert_not_called()

    async def test_launch_exception_is_uncertain_and_never_retried(self):
        self.launch.side_effect = OSError("launch failed")
        result = await self.run_control()
        self.assertTrue(result["dispatch_attempted"])
        self.assertTrue(result["outcome_unknown"])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))
        self.launch.assert_called_once()

    async def test_cancel_preserves_recovery_latch_and_receipt(self):
        self.mode = "cancel"
        with self.assertRaises(asyncio.CancelledError):
            await self.run_control()
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))
        saved = json.loads((self.root / "situs_native_settings" / OP / "result.json").read_text())
        self.assertTrue(saved["outcome_unknown"])
        self.launch.assert_called_once()

    async def test_command_lock_serializes_two_identical_requests(self):
        results = await asyncio.gather(self.run_control(), self.run_control())
        self.assertTrue(all(item["success"] for item in results))
        self.assertEqual(sum(item["replayed"] for item in results), 1)
        self.launch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
