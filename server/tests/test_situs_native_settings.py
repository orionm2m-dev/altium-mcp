"""Pure native settings program/receipt tests; never execute Altium."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import situs_native_settings as native

PCB = r"C:\boards\O'Brien.PcbDoc"
OP = "a" * 32
REPORT = r"C:\receipts\operation.json"


def request(action="inspect", expected=None, checked=None):
    return native.build_settings_request(PCB, OP, action, expected, checked)


def receipt(req, stage="completed", **changes):
    result = dict(req, stage=stage, success=stage == "completed", before=False, after=False,
                  setter_attempted=False, changed=False, already_satisfied=False, error="")
    result.update(changes)
    return result


class BuilderTests(unittest.TestCase):
    def test_inspection_has_no_setter_save_dispatch_or_helper_dependency(self):
        source = native.build_lock_preroutes_program(PCB, OP, REPORT)
        self.assertIn("Options := PCBServer.SystemOptions;", source)
        self.assertIn("BeforeValue := Options.LockPreRoutes;", source)
        self.assertIn("procedure Run;", source)
        for token in ("Options.LockPreRoutes :=", "RunProcess", "OpenDocument", "SaveDocument", "Export_ToIniFile", "PreProcess", "PostProcess", "SendMessage"):
            self.assertNotIn(token, source)

    def test_set_has_one_assignment_reacquisition_and_before_attempt_receipt(self):
        source = native.build_lock_preroutes_program(PCB, OP, REPORT, "set", False, True)
        self.assertEqual(source.count("Options.LockPreRoutes :="), 1)
        self.assertIn("Options.LockPreRoutes := True;", source)
        self.assertLess(source.index("SettingsReceipt('before_setter'"), source.index("Options.LockPreRoutes :="))
        self.assertIn("BeforeValue <> False", source)
        self.assertIn("AfterValue <> True", source)
        self.assertIn("identity_changed_after_write", source)
        self.assertIn("if FileExists('C:\\receipts\\operation.json') then Exit;", source)
        self.assertIn("if FileExists('C:\\receipts\\operation.entered.json') then Exit;", source)

    def test_receipts_are_immutable_and_a_failed_marker_blocks_the_setter(self):
        source = native.build_lock_preroutes_program(PCB, OP, REPORT, "set", False, True)
        self.assertIn("ReceiptPath := 'C:\\receipts\\operation' + '.' + Stage + '.json'", source)
        self.assertIn("if FileExists(ReceiptPath) then Exit;", source)
        self.assertEqual(source.count("Lines.SaveToFile(ReceiptPath);"), 1)
        self.assertIn("if not SettingsReceipt('before_setter'", source)
        marker = source[source.index("if not SettingsReceipt('before_setter'"):source.index("Options.LockPreRoutes :=")]
        self.assertIn("then Exit;", marker)
        self.assertNotIn("__RECEIPT", source)
        # No hardcoded project or exchange paths belong to the fixed source.
        self.assertNotIn("C:\\projects", source)
        self.assertNotIn("Users\\Public", source)

    def test_immutable_program_passes_existing_static_preflight(self):
        import situs_native_settings_service as service
        for action, before, after in [("inspect", None, None), ("set", False, True)]:
            source = native.build_lock_preroutes_program(PCB, OP, REPORT, action, before, after)
            self.assertIn("procedure RunSitusSettings;", service._preflight(source))

    def test_pascal_apostrophe_and_json_literal_are_escaped(self):
        source = native.build_lock_preroutes_program(PCB, OP, REPORT)
        self.assertIn("O''Brien.PcbDoc", source)
        self.assertIn('"pid_verified_by_native":false', source)
        self.assertIn('"scope":"global_pcb_system_options"', source)

    def test_read_rejects_mutation_arguments_and_set_requires_real_bools(self):
        for action, expected, value in [("inspect", False, None), ("inspect", None, True), ("set", None, True), ("set", False, 1), ("set", 0, True), ("toggle", False, True)]:
            with self.assertRaises(ValueError):
                request(action, expected, value)

    def test_bad_paths_operation_ids_and_ads_are_refused(self):
        for path in ["relative.PcbDoc", r"C:\boards\..\other.PcbDoc", "C:\\bad\n.PcbDoc", r"C:\boards\file:stream.PcbDoc"]:
            with self.assertRaises(ValueError):
                native.build_settings_request(path, OP)
        for op in ["", "a" * 31, "A" * 32, "a" * 31 + "g"]:
            with self.assertRaises(ValueError):
                native.build_settings_request(PCB, op)
        with self.assertRaises(ValueError):
            native.build_lock_preroutes_program(PCB, OP, r"C:\receipts\result.PcbDoc")

    def test_same_requested_and_expected_state_is_supported(self):
        source = native.build_lock_preroutes_program(PCB, OP, REPORT, "set", True, True)
        self.assertIn("BeforeValue = True", source)
        self.assertIn("BeforeValue <> True", source)


class ReceiptTests(unittest.TestCase):
    def parse(self, req, **changes):
        return native.parse_settings_receipt(json.dumps(receipt(req, **changes)), req)

    def test_inspect_completed_readback_is_not_persistence_or_pid_verification(self):
        result = self.parse(request())
        self.assertTrue(result["setting_readback_verified"])
        self.assertFalse(result["persistence_verified"])
        self.assertFalse(result["pid_verified_by_native"])
        self.assertFalse(result["retry_allowed"])

    def test_successful_compare_and_set_requires_exact_before_after(self):
        req = request("set", False, True)
        result = self.parse(req, before=False, after=True, setter_attempted=True, changed=True)
        self.assertTrue(result["completion_verified"])
        self.assertFalse(result["outcome_unknown"])
        for change in [dict(before=True), dict(after=False), dict(changed=False), dict(already_satisfied=True)]:
            values = dict(before=False, after=True, setter_attempted=True, changed=True)
            values.update(change)
            with self.assertRaises(ValueError): self.parse(req, **values)

    def test_idempotent_noop_accepts_already_desired_even_if_expected_differs(self):
        req = request("set", False, True)
        result = self.parse(req, before=True, after=True, already_satisfied=True)
        self.assertTrue(result["success"])
        self.assertFalse(result["setter_attempted"])

    def test_stale_expected_blocks_without_input(self):
        result = self.parse(request("set", True, True), stage="expected_state_mismatch", before=False,
                            after=None, error="stale_expected_state")
        self.assertFalse(result["success"])
        self.assertFalse(result["outcome_unknown"])
        self.assertFalse(result["retry_allowed"])

    def test_partial_setter_receipt_is_unknown_and_never_replayed(self):
        result = self.parse(request("set", False, True), stage="before_setter", before=False,
                            after=None, setter_attempted=True)
        self.assertFalse(result["completion_verified"])
        self.assertTrue(result["outcome_unknown"])
        self.assertFalse(result["retry_allowed"])

    def test_identity_contract_schema_and_boolean_fabrication_rejected(self):
        req = request()
        for change in [dict(operation_id="b" * 32), dict(pcb_path=r"C:\other.PcbDoc"), dict(schema_version=True), dict(persistence_verified=True), dict(before=0), dict(success=1), dict(stage="entered", success=True), dict(unexpected=True)]:
            with self.assertRaises(ValueError):
                native.parse_settings_receipt(json.dumps(receipt(req, **change)), req)

    def test_inspection_cannot_assert_write(self):
        for change in [dict(setter_attempted=True), dict(changed=True), dict(already_satisfied=True), dict(after=True)]:
            with self.assertRaises(ValueError): self.parse(request(), **change)

    def test_incomplete_or_inconsistent_success_is_rejected(self):
        for change in [dict(before=None), dict(after=None), dict(error="failed"), dict(success=False)]:
            with self.assertRaises(ValueError): self.parse(request(), **change)

    def test_duplicate_keys_invalid_json_and_oversized_receipts_are_rejected(self):
        req = request()
        for text in ['{"stage":"entered","stage":"completed"}', "{", " " * 65537, "[]"]:
            with self.assertRaises(ValueError): native.parse_settings_receipt(text, req)

    def test_bounded_receipt_file_read_and_missing_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "receipt.json"
            self.assertTrue(native.read_settings_receipt(path, request())["outcome_unknown"])
            path.write_text(json.dumps(receipt(request())), encoding="utf-8-sig")
            self.assertTrue(native.read_settings_receipt(path, request())["success"])
            path.write_bytes(b" " * 65537)
            with self.assertRaises(ValueError): native.read_settings_receipt(path, request())

    def test_newest_partial_marker_is_read_without_overwriting_earlier_markers(self):
        req = request("set", False, True)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "native.json"
            for stage in native.PARTIAL_STAGES:
                payload = receipt(req, stage, before=False, after=None,
                                  setter_attempted=stage == "before_setter")
                marker = path.with_name("native." + stage + ".json")
                marker.write_text(json.dumps(payload), encoding="utf-8")
                observed = native.read_settings_receipt(path, req)
                self.assertEqual(observed["stage"], stage)
                self.assertFalse(path.exists())
            self.assertTrue(observed["outcome_unknown"])
            self.assertEqual(len(list(Path(folder).glob("*.json"))), 4)
            path.write_text(json.dumps(receipt(req, before=False, after=True,
                            setter_attempted=True, changed=True)), encoding="utf-8")
            self.assertTrue(native.read_settings_receipt(path, req)["success"])

    def test_stage_filename_mismatch_and_partial_marker_identity_are_refused(self):
        req = request()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "native.json"
            marker = path.with_name("native.before_getter.json")
            for payload in [receipt(req), receipt(req, "before_getter", operation_id="b" * 32)]:
                marker.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(ValueError): native.read_settings_receipt(path, req)

    def test_incomplete_terminal_does_not_fall_back_to_earlier_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "native.json"
            path.with_name("native.entered.json").write_text(
                json.dumps(receipt(request(), "entered")), encoding="utf-8")
            path.write_text("{", encoding="utf-8")
            with self.assertRaises(ValueError): native.read_settings_receipt(path, request())


@unittest.skipUnless(os.name == "nt", "Windows file sharing semantics")
class WindowsReceiptSharingTests(unittest.TestCase):
    def test_reader_blocks_truncate_but_not_creation_of_next_immutable_file(self):
        # File-only reproduction: no Altium/process/window handles are used.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        invalid = ctypes.c_void_p(-1).value
        with tempfile.TemporaryDirectory() as folder:
            previous = Path(folder) / "native.before_getter.json"
            following = Path(folder) / "native.before_setter.json"
            previous.write_bytes(b"existing immutable receipt")
            with previous.open("rb"):
                old_handle = create(str(previous), 0x40000000, 0, None, 2, 0x80, None)
                error = ctypes.get_last_error()
                if old_handle != invalid:
                    close(old_handle)
                self.assertEqual(old_handle, invalid)
                self.assertEqual(error, 32)  # ERROR_SHARING_VIOLATION
                new_handle = create(str(following), 0x40000000, 0, None, 1, 0x80, None)
                try:
                    self.assertNotEqual(new_handle, invalid)
                finally:
                    if new_handle != invalid:
                        close(new_handle)


if __name__ == "__main__":
    unittest.main()
