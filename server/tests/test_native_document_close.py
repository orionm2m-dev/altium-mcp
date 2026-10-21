"""Pure request, source-preflight and native-receipt tests; no Altium calls."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_document_close as close
import script_lint


class NativeDocumentCloseTests(unittest.TestCase):
    def setUp(self):
        self.request = close.build_close_request(r"C:\qualification\active.PcbDoc",
                                                r"Z:\qualification\old.PcbDoc", "a" * 32)

    def success_receipt(self):
        return dict(self.request, stage="completed", error="", success=True, close_attempted=True,
                    closed_verified=True, current_pcb_preserved=True, other_documents_unchanged=True,
                    before_document_count=25, after_document_count=24, before_target_count=1,
                    after_target_count=0, before_module_count=33, after_module_count=33)

    def parse(self, value):
        return close.parse_close_receipt(json.dumps(value), self.request)

    def test_fixed_source_passes_existing_linter(self):
        source = close.build_close_program(self.request, r"C:\exchange\unique.json")
        result = script_lint.lint("", source)
        self.assertTrue(result["success"], result)

    def test_one_addressed_close_and_no_save_focus_or_generic_dispatch(self):
        source = close.build_close_program(self.request, r"C:\exchange\unique.json")
        self.assertEqual(source.count("Client.CloseDocument("), 1)
        for token in ("RunProcess(", "SendMessage(", "ShowDocument(", "SetCurrentView(",
                      "SaveDocument(", "DoFileSave", "GetState_", "SetState_"):
            self.assertNotIn(token, source)
        self.assertIn("procedure RunCleanDocumentClose;", source)
        self.assertLess(source.index("CloseReceipt('before_close'"), source.index("Client.CloseDocument("))
        self.assertIn("CheckTarget.Modified", source)
        self.assertIn("Client.ProcessControl.ProcessDepth <> 0", source)
        self.assertIn("PcbModule.ProcessControl.ProcessDepth <> 0", source)

    def test_both_before_and_after_collect_twice(self):
        source = close.build_close_program(self.request, r"C:\exchange\unique.json")
        self.assertIn("BeforeOne := CloseCollect(", source)
        self.assertIn("BeforeTwo := CloseCollect(", source)
        self.assertIn("AfterOne := CloseCollect(", source)
        self.assertIn("AfterTwo := CloseCollect(", source)
        self.assertIn("BeforeTwo = AfterTwo", source)
        self.assertIn("if FileExists('C:\\exchange\\unique.json') then Exit;", source)

    def test_apostrophe_is_literal_not_pascal_input(self):
        request = close.build_close_request(r"C:\D'Angelo\active.PcbDoc", r"Z:\D'Angelo\old.PcbDoc", "b" * 32)
        source = close.build_close_program(request, r"C:\D'Angelo\unique.json")
        self.assertIn("'C:\\D''Angelo\\active.PcbDoc'", source)
        self.assertTrue(script_lint.lint("", source)["success"])

    def test_active_target_refused_across_case_and_slash_forms(self):
        for target in (r"C:\qualification\active.PcbDoc", "c:/QUALIFICATION/ACTIVE.PCBDOC"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                close.build_close_request(self.request["active_pcb_path"], target, "a" * 32)

    def test_invalid_target_paths_refused(self):
        for target in ("old.PcbDoc", r"C:\x\..\old.PcbDoc", "C:\\x\r\nold.PcbDoc",
                       r"C:\x\file:stream.PcbDoc", r"C:\old.SchDoc", "", None):
            with self.subTest(target=target), self.assertRaises(ValueError):
                close.build_close_request(self.request["active_pcb_path"], target, "a" * 32)

    def test_invalid_nonce_refused(self):
        for nonce in ("", "A" * 32, "a" * 31, "../id", 1):
            with self.subTest(nonce=nonce), self.assertRaises(ValueError):
                close.build_close_request(self.request["active_pcb_path"], self.request["target_document_path"], nonce)

    def test_changed_request_metadata_refused(self):
        for changes in ({"document_save_requested": True}, {"schema_version": True},
                        {"focus_requested": True}, {"arbitrary_command": "close all"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                close.build_close_program(dict(self.request, **changes), r"C:\exchange\unique.json")

    def test_bad_receipt_path_refused(self):
        for path in ("result.json", r"C:\exchange\..\result.json", r"C:\result.pas", "C:\\bad\0.json"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                close.build_close_program(self.request, path)

    def test_verified_success_requires_target_removed_and_others_preserved(self):
        result = self.parse(self.success_receipt())
        self.assertTrue(result["completion_verified"])
        self.assertTrue(result["terminal"])
        self.assertFalse(result["outcome_unknown"])
        self.assertFalse(result["retry_allowed"])

    def test_same_count_or_extra_document_removed_cannot_claim_success(self):
        for count in (25, 23, 0):
            with self.subTest(count=count), self.assertRaises(ValueError):
                self.parse(dict(self.success_receipt(), after_document_count=count))

    def test_target_missing_before_or_present_after_refuses_completion(self):
        for changes in ({"before_target_count": 0}, {"before_target_count": 2}, {"after_target_count": 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.parse(dict(self.success_receipt(), **changes))

    def test_missing_current_or_other_document_guard_refuses_completion(self):
        for key in ("current_pcb_preserved", "other_documents_unchanged", "closed_verified", "close_attempted"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.parse(dict(self.success_receipt(), **{key: False}))

    def test_new_missing_or_unobserved_module_count_refuses_completion(self):
        for value in (32, 34, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.parse(dict(self.success_receipt(), after_module_count=value))

    def test_partial_before_close_is_unknown_never_complete(self):
        value = dict(self.success_receipt(), stage="before_close", success=False, closed_verified=False,
                     current_pcb_preserved=False, other_documents_unchanged=False,
                     after_document_count=None, after_target_count=None, after_module_count=None)
        result = self.parse(value)
        self.assertTrue(result["outcome_unknown"])
        self.assertFalse(result["terminal"])
        self.assertFalse(result["completion_verified"])

    def test_verified_refusal_before_close_is_not_an_unknown_mutation(self):
        value = dict(self.success_receipt(), stage="guard_failed", success=False,
                     error="target_missing_ambiguous_wrong_kind_or_modified", close_attempted=False,
                     closed_verified=False, current_pcb_preserved=False, other_documents_unchanged=False,
                     after_document_count=None, after_target_count=None, after_module_count=None)
        result = self.parse(value)
        self.assertTrue(result["terminal"])
        self.assertFalse(result["outcome_unknown"])
        self.assertFalse(result["completion_verified"])

    def test_throw_after_close_or_changed_other_documents_remains_unknown(self):
        for stage in ("native_exception", "readback_failed"):
            value = dict(self.success_receipt(), stage=stage, success=False,
                         error="observed failure", other_documents_unchanged=False)
            result = self.parse(value)
            self.assertTrue(result["outcome_unknown"])
            self.assertTrue(result["terminal"])
            self.assertFalse(result["completion_verified"])

    def test_wrong_nonce_or_path_receipt_refused(self):
        for changes in ({"operation_id": "b" * 32}, {"target_document_path": r"C:\wrong.PcbDoc"},
                        {"active_pcb_path": r"C:\wrong.PcbDoc"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.parse(dict(self.success_receipt(), **changes))

    def test_unknown_duplicate_missing_receipt_fields_refused(self):
        with self.assertRaises(ValueError):
            self.parse(dict(self.success_receipt(), untrusted=True))
        missing = self.success_receipt()
        del missing["current_pcb_preserved"]
        with self.assertRaises(ValueError):
            self.parse(missing)
        raw = json.dumps(self.success_receipt()).replace('"success": true', '"success": true,"success": false')
        with self.assertRaises(ValueError):
            close.parse_close_receipt(raw, self.request)

    def test_bool_is_not_a_count_and_numbers_are_not_bools(self):
        for changes in ({"before_target_count": True}, {"before_target_count": -1},
                        {"success": 1}, {"schema_version": True}, {"pid_verified_by_native": 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.parse(dict(self.success_receipt(), **changes))

    def test_claimed_success_or_missing_error_in_wrong_stage_refused(self):
        for changes in ({"stage": "entered"}, {"stage": "readback_failed", "success": False},
                        {"stage": "unknown"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.parse(dict(self.success_receipt(), **changes))

    def test_missing_file_returns_unknown_without_creating_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.json"
            result = close.read_close_receipt(path, self.request)
            self.assertTrue(result["outcome_unknown"])
            self.assertFalse(path.exists())

    def test_bounded_utf8_bom_receipt_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            path.write_text(json.dumps(self.success_receipt()), encoding="utf-8-sig")
            self.assertTrue(close.read_close_receipt(path, self.request)["completion_verified"])
            path.write_bytes(b" " * 65537)
            with self.assertRaises(ValueError):
                close.read_close_receipt(path, self.request)


if __name__ == "__main__":
    unittest.main()
