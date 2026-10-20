"""Offline cold-start tests; no Altium process, GUI or native API is invoked."""
import ast
import asyncio
from contextlib import ExitStack
import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest import TestCase, main
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import altium_lifecycle as lifecycle
import emergency_stop


class StartTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exe = self.root / "X2.EXE"
        self.exe.write_bytes(b"never execute this test fixture")
        self.state_path = self.root / "script_recovery_state.json"
        self.state_bytes = b'{"phase":"emergency_terminated","token":"preserve exactly"}'
        self.state_path.write_bytes(self.state_bytes)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(lifecycle, "_started", {}))
        self.inventory = self.stack.enter_context(patch.object(lifecycle, "configured_instances",
            return_value={"configured": [], "uncertain": [], "other_paths": []}))
        self.process = Mock(pid=456)
        self.process.poll.return_value = None
        self.launch = self.stack.enter_context(patch.object(lifecycle.subprocess, "Popen", return_value=self.process))
        self.environment = self.stack.enter_context(patch.object(lifecycle, "_child_environment",
            return_value=({"PRESERVED_TEST_VALUE": "secret-test-value"},
                          {"windir_present": True, "windir_repaired": False})))
        self.identity = {"pid": 456, "exe": emergency_stop._canonical(self.exe),
                         "creation_filetime": 133777777777777777, "user_sid": "owned", "running": True}
        self.read_identity = self.stack.enter_context(patch.object(lifecycle, "_owned_identity", return_value=self.identity))

    def start(self):
        return lifecycle.start(str(self.exe), self.root)

    def assert_untouched(self):
        self.assertEqual(self.state_path.read_bytes(), self.state_bytes)
        self.process.terminate.assert_not_called()
        self.process.kill.assert_not_called()

    def test_normal_gui_launch_has_no_script_arguments_and_preserves_latch(self):
        result = self.start()
        self.assertTrue(result["success"])
        self.assertTrue(result["launched"])
        self.assertFalse(result["ui_ready_verified"])
        self.assertFalse(result["script_dispatched"])
        self.assertEqual(result["creation_filetime_decimal"], "133777777777777777")
        self.assertEqual(result["identity"], self.identity)
        args, kwargs = self.launch.call_args
        self.assertEqual(args, ([str(self.exe.resolve())],))
        self.assertEqual(kwargs["executable"], str(self.exe.resolve()))
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["env"], {"PRESERVED_TEST_VALUE": "secret-test-value"})
        self.assertNotIn("secret-test-value", json.dumps(result))
        self.assertNotIn("secret-test-value", (self.root / "altium_startup.json").read_text())
        self.assertNotIn("startupinfo", kwargs)  # Do not hide the normal GUI application.
        self.assertEqual(kwargs["creationflags"], getattr(lifecycle.subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(json.loads((self.root / "altium_startup.json").read_text())["target_pid"], 456)
        self.assert_untouched()

    def test_existing_or_uncertain_instance_blocks_without_launch(self):
        for field in ("configured", "uncertain"):
            with self.subTest(field=field):
                self.inventory.return_value = {"configured": [], "uncertain": [], "other_paths": []}
                self.inventory.return_value[field] = [{"pid": 22, "exe": None}]
                self.assertFalse(self.start()["launched"])
        self.launch.assert_not_called()
        self.assert_untouched()

    def test_previous_owned_start_blocks_even_if_snapshot_missed_it(self):
        self.assertTrue(self.start()["launched"])
        self.assertFalse(self.start()["launched"])
        self.launch.assert_called_once()
        self.assert_untouched()

    def test_exited_start_is_reported_without_retry_or_probe(self):
        self.process.poll.return_value = 3
        result = self.start()
        self.assertTrue(result["launched"])
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "startup_process_exited_or_unverified")
        self.assertEqual(result["exit_code"], 3)
        self.launch.assert_called_once()
        self.assert_untouched()

    def test_missing_configured_exe_never_launches(self):
        self.exe.unlink()
        self.assertFalse(self.start()["launched"])
        self.launch.assert_not_called()
        self.assert_untouched()

    def test_launch_error_is_truthful_and_does_not_retry(self):
        self.launch.side_effect = OSError("launch denied")
        result = self.start()
        self.assertFalse(result["launched"])
        self.launch.assert_called_once()
        self.assert_untouched()

    def test_identity_failure_after_launch_retains_owned_process_without_retry(self):
        self.read_identity.side_effect = OSError("identity unavailable")
        result = self.start()
        self.assertTrue(result["launched"])
        self.assertFalse(result["success"])
        self.assertIs(lifecycle._started[456], self.process)
        self.launch.assert_called_once()
        self.assert_untouched()

    def test_unexpected_owned_identity_is_not_success(self):
        for field, value in (("pid", 999), ("exe", "C:/Other.exe")):
            with self.subTest(field=field):
                lifecycle._started.clear()
                self.read_identity.return_value = dict(self.identity, **{field: value})
                self.assertFalse(self.start()["success"])
        self.assert_untouched()

    def test_snapshot_error_or_held_crossprocess_claim_never_launches(self):
        self.inventory.side_effect = OSError("snapshot failed")
        self.assertFalse(self.start()["launched"])
        self.inventory.side_effect = None
        with emergency_stop.emergency_claim(self.root):
            self.assertFalse(self.start()["launched"])
        self.launch.assert_not_called()
        self.assert_untouched()

    def test_busy_claim_never_writes_over_another_launch_receipt(self):
        with emergency_stop.emergency_claim(self.root), patch.object(lifecycle, "_write_record") as record:
            self.assertFalse(self.start()["launched"])
            record.assert_not_called()
        self.launch.assert_not_called()

    def test_error_receipt_is_written_while_claim_remains_held(self):
        self.launch.side_effect = OSError("launch failed")
        writes = []
        def record(*args):
            with self.assertRaises(emergency_stop.EmergencyClaimBusy):
                with emergency_stop.emergency_claim(self.root):
                    pass
            writes.append(dict(args[1]))
        with patch.object(lifecycle, "_write_record", side_effect=record):
            result = self.start()
        self.assertFalse(result["launched"])
        self.assertEqual(writes[-1]["error"], "launch failed")

    def test_invalid_child_environment_never_launches(self):
        self.environment.side_effect = ValueError("Child WINDIR invalid")
        result = self.start()
        self.assertFalse(result["launched"])
        self.launch.assert_not_called()
        self.assert_untouched()

    def test_owned_handle_identity_does_not_query_reused_launch_pid(self):
        reader = Mock(handle=11)
        reader.__enter__ = Mock(return_value=reader)
        reader.__exit__ = Mock(return_value=False)
        def read():
            self.assertEqual(reader.handle, 9999)
            return self.identity
        reader.identity.side_effect = read
        process = Mock(pid=456, _handle=9999)
        # Restore the function hidden by setUp's patch; no native backend is used.
        implementation = self.read_identity
        implementation.side_effect = None
        with patch.object(emergency_stop, "WinProcess", return_value=reader) as backend:
            original = lifecycle._owned_identity
            # Read source function separately so the normal protocol mock stays isolated.
            tree = ast.parse(Path(lifecycle.__file__).read_text())
            function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_owned_identity")
            namespace = dict(lifecycle.__dict__)
            exec(compile(ast.Module(body=[function], type_ignores=[]), "owned_identity_test", "exec"), namespace)
            result = namespace["_owned_identity"](process)
            self.assertEqual(result, self.identity)
            self.assertEqual(reader.handle, 11)
            backend.assert_called_once_with(os.getpid())


class ChildEnvironmentTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = self.temp.name

    def test_missing_windir_repaired_only_in_child_copy(self):
        original = {"TOKEN": "do-not-log", "SystemRoot": "existing-root", "PATH": "existing-path"}
        with patch.object(lifecycle.os, "environ", original), \
             patch.object(lifecycle, "_get_windows_directory", return_value=self.directory) as query:
            child, flags = lifecycle._child_environment()
        self.assertEqual(original, {"TOKEN": "do-not-log", "SystemRoot": "existing-root", "PATH": "existing-path"})
        self.assertEqual(child, dict(original, WINDIR=self.directory))
        self.assertEqual(flags, {"windir_present": False, "windir_repaired": True})
        self.assertNotIn("do-not-log", json.dumps(flags))
        query.assert_called_once_with()

    def test_existing_case_insensitive_windir_preserved_without_native_query(self):
        for key in ("windir", "WINDIR", "WinDir"):
            with self.subTest(key=key), patch.object(lifecycle.os, "environ", {key: self.directory, "TOKEN": "private"}), \
                 patch.object(lifecycle, "_get_windows_directory") as query:
                child, flags = lifecycle._child_environment()
                self.assertEqual(child, {key: self.directory, "TOKEN": "private"})
                self.assertEqual(flags, {"windir_present": True, "windir_repaired": False})
                query.assert_not_called()

    def test_invalid_existing_or_derived_directory_is_not_guessed(self):
        for original, derived in (({"WINDIR": ""}, self.directory),
                                  ({"WINDIR": "missing-relative"}, self.directory),
                                  ({}, str(Path(self.directory) / "absent"))):
            with self.subTest(original=original), patch.object(lifecycle.os, "environ", original), \
                 patch.object(lifecycle, "_get_windows_directory", return_value=derived):
                with self.assertRaises(ValueError):
                    lifecycle._child_environment()

    def test_native_directory_query_is_mocked_and_uses_windows_api(self):
        kernel = Mock()
        def directory_query(buffer, capacity):
            buffer.value = self.directory
            return len(self.directory)
        kernel.GetWindowsDirectoryW.side_effect = directory_query
        with patch.object(ctypes, "WinDLL", return_value=kernel, create=True):
            self.assertEqual(lifecycle._get_windows_directory(), self.directory)
        kernel.GetWindowsDirectoryW.assert_called_once()



class InventoryTests(TestCase):
    def test_exact_paths_and_unknown_candidates_are_distinguished(self):
        kernel = Mock()
        kernel.CreateToolhelp32Snapshot.return_value = 123
        entries = iter([(1, "other.exe"), (2, "X2.EXE"), (3, "X2.EXE"), (4, "X2.EXE")])
        def next_entry(snapshot, pointer):
            try:
                pid, name = next(entries)
            except StopIteration:
                return False
            pointer._obj.th32ProcessID = pid
            pointer._obj.szExeFile = name
            return True
        kernel.Process32FirstW.side_effect = next_entry
        kernel.Process32NextW.side_effect = next_entry
        with patch.object(ctypes, "WinDLL", return_value=kernel, create=True), \
             patch.object(ctypes, "get_last_error", return_value=18, create=True), \
             patch.object(lifecycle.script_recovery, "_process_path", side_effect=lambda pid: {
                 2: "C:/Altium/X2.EXE", 3: "", 4: "C:/Other/X2.EXE"}[pid]), \
             patch.object(lifecycle.os, "name", "nt"):
            result = lifecycle.configured_instances("C:/Altium/X2.EXE")
        self.assertEqual([p["pid"] for p in result["configured"]], [2])
        self.assertEqual([p["pid"] for p in result["uncertain"]], [3])
        self.assertEqual([p["pid"] for p in result["other_paths"]], [4])
        kernel.CloseHandle.assert_called_once_with(123)

    def test_public_registration_holds_shared_bridge_lock(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "start_altium")
        body = ast.unparse(function)
        self.assertIn("async with altium_bridge._command_lock", body)
        self.assertIn("altium_lifecycle.start", body)
        self.assertNotIn("recover(", body)
        self.assertNotIn("execute_command", body)


if __name__ == "__main__":
    main()
