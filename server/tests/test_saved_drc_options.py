"""Saved DRC decoder contracts; no Altium or COM access."""
from io import BytesIO
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import saved_drc_options as reader


def record(text=None):
    if text is None:
        text = "|RECORD=DesignRuleCheckerOptions|RULESETTOCHECK=0,2,63|ONLINERULESETTOCHECK=0,2|MAXVIOLATIONCOUNT=5000|VERIFYSHORTINGCOPPER=TRUE"
    payload = text.encode("latin1") + b"\0"
    return struct.pack("<I", len(payload)) + payload


class DecodeTests(unittest.TestCase):
    def test_exact_ids_limit_and_boolean_are_read_without_claiming_live_coverage(self):
        result = reader.parse_drc_options_stream(record(), {0: "eRule_Clearance", 2: "eRule_MaxMinWidth"})
        self.assertEqual(result["max_violation_count"], 5000)
        self.assertEqual([x["rule_kind_id"] for x in result["batch_checks"]], [0, 2, 63])
        self.assertEqual(result["batch_checks"][-1]["mapping_status"], "UNKNOWN")
        self.assertTrue(result["boolean_options"]["VERIFYSHORTINGCOPPER"])
        self.assertFalse(result["reflects_unsaved_native_state"])
        self.assertFalse(result["enabled_check_coverage_live_verified"])
        self.assertFalse(result["full_drc_verified"])
        self.assertEqual(len(result["stream_sha256"]), 64)

    def test_empty_check_sets_are_kept_empty_not_promoted_to_all(self):
        result = reader.parse_drc_options_stream(record("|RECORD=DesignRuleCheckerOptions|RULESETTOCHECK=|ONLINERULESETTOCHECK=|MAXVIOLATIONCOUNT=0"))
        self.assertEqual(result["batch_checks"], [])
        self.assertEqual(result["online_checks"], [])
        self.assertEqual(result["max_violation_count"], 0)

    def test_truncated_oversized_extra_and_embedded_nul_records_are_rejected(self):
        raw = record()
        cases = [raw[:-1], raw + b"x", b"short", b"x" * (reader.MAX_STREAM_BYTES + 1),
                 struct.pack("<I", 4) + b"a\0b\0"]
        for data in cases:
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                reader.parse_drc_options_stream(data)

    def test_wrong_record_missing_fields_and_duplicate_fields_are_rejected(self):
        text = record()[4:-1].decode("latin1")
        for value in [text.replace("DesignRuleCheckerOptions", "Other"),
                      text.replace("|RULESETTOCHECK=0,2,63", ""),
                      text + "|RULESETTOCHECK=1", text + "|MALFORMED", text + "|=value"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                reader.parse_drc_options_stream(record(value))

    def test_duplicate_negative_and_noninteger_rule_ids_are_rejected(self):
        text = record()[4:-1].decode("latin1")
        for value in ["0,0", "-1", "1.0", "1,", "65536", "a", " 1"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                reader.parse_drc_options_stream(record(text.replace("0,2,63", value)))

    def test_unknown_fields_and_new_rule_ids_are_preserved(self):
        text = record()[4:-1].decode("latin1").replace("0,2,63", "1000") + "|NEWOPTION=value"
        result = reader.parse_drc_options_stream(record(text), {0: "eRule_Clearance"})
        self.assertEqual(result["batch_checks"], [dict(rule_kind_id=1000, rule_kind_name=None, mapping_status="UNKNOWN")])
        self.assertEqual(result["raw_fields"]["NEWOPTION"], "value")

    def test_invalid_limit_and_known_boolean_are_rejected(self):
        text = record()[4:-1].decode("latin1")
        for invalid in [text.replace("5000", "-1"), text.replace("5000", "2147483648"),
                        text.replace("5000", "1e3"), text.replace("TRUE", "1")]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                reader.parse_drc_options_stream(record(invalid))

    def test_installed_mapping_retains_provenance_without_runtime_claim(self):
        names, source = reader._mapping()
        self.assertEqual(names[0], "eRule_Clearance")
        self.assertEqual(names[2], "eRule_MaxMinWidth")
        self.assertEqual(names[63], "eRule_BoardOutlineClearance")
        self.assertFalse(source["runtime_verified"])
        self.assertEqual(len(source["assembly_sha256"]), 64)


class ReadOnlyFileTests(unittest.TestCase):
    def test_reader_opens_bytes_copy_readonly_and_never_changes_file(self):
        stream = record()
        observations = []
        class Storage:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def exists(self, name): return name == reader.STREAM
            def openstream(self, name): return BytesIO(stream)
        def factory(source, **kwargs):
            observations.append(kwargs)
            self.assertIsInstance(source, BytesIO)
            return Storage()
        fake = SimpleNamespace(OleFileIO=factory, DEFECT_INCORRECT=30)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "saved.PcbDoc"
            initial = b"x" * 512
            path.write_bytes(initial)
            with patch.dict(sys.modules, {"olefile": fake}):
                result = reader.read_saved_drc_options(str(path))
            self.assertTrue(result["success"], result)
            self.assertEqual(path.read_bytes(), initial)
            self.assertEqual(observations, [dict(write_mode=False, raise_defects=30)])
            self.assertFalse(result["native_dispatched"])
            self.assertTrue(result["no_write"])

    def test_missing_dependency_and_invalid_path_are_explicit_errors(self):
        with patch.dict(sys.modules, {"olefile": None}):
            result = reader.read_saved_drc_options(str(Path.cwd() / "abs.PcbDoc"))
        self.assertFalse(result["success"])
        self.assertIn("olefile", result["error"])
        for value in [None, "relative.PcbDoc", str(Path.cwd() / "not-pcb.txt")]:
            result = reader.read_saved_drc_options(value)
            self.assertFalse(result["success"])
            self.assertFalse(result["native_dispatched"])


if __name__ == "__main__":
    unittest.main()
