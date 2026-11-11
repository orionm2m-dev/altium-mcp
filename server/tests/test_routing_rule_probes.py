"""Pure rendering and isolation tests; no Altium or worker calls."""
from pathlib import Path
import importlib.util
import json
import unittest

DIRECTORY = Path(__file__).resolve().parents[2] / "dev/probes/routing_rules"
spec = importlib.util.spec_from_file_location("render_routing_probe", DIRECTORY / "render_probe.py")
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


class ProbeTests(unittest.TestCase):
    def test_all_nine_probes_are_standalone_and_readonly(self):
        names = json.loads((DIRECTORY / "probe_manifest.json").read_text("utf-8"))["sequence"]
        self.assertEqual(len(names), 9)
        for name in names:
            source = renderer.render_probe(name, r"C:\boards\candidate.PcbDoc", r"C:\receipts\result.json", "a" * 32)
            self.assertIn("procedure RunProbe;", source)
            self.assertIn("'before_getters'", source)
            self.assertIn("'completed'", source)
            self.assertIn("Rows.Count > 0", source)
            for token in ("RunProcess(", "PreProcess", "PostProcess", "DM_Save", "SaveDocument", "OpenDocument", "SetState_", "BeginModify", "AddPCBObject"):
                self.assertNotIn(token, source)

    def test_apostrophe_is_pascal_escaped_and_substitution_is_one_pass(self):
        source = renderer.render_probe("01_width_scalar", r"C:\O'Brien\__REPORT_PATH__.PcbDoc", r"C:\reports\result.json", "b" * 32)
        self.assertIn(r"PcbPath := 'C:\O''Brien\__REPORT_PATH__.PcbDoc';", source)
        self.assertIn(r"ReportPath := 'C:\reports\result.json';", source)

    def test_unsafe_paths_ids_and_unlisted_templates_are_rejected(self):
        args = ["01_width_scalar", r"C:\boards\candidate.PcbDoc", r"C:\receipts\result.json", "c" * 32]
        cases = [(0,"../candidate"),(1,"relative.PcbDoc"),(1,r"C:\boards\..\other.PcbDoc"),(1,"C:\bad\n.PcbDoc\nRunProcess"),(2,r"C:\boards\candidate.PcbDoc"),(3,"not-a-uuid")]
        for index, value in cases:
            changed = list(args); changed[index] = value
            with self.assertRaises(ValueError):
                renderer.render_probe(*changed)

    def test_no_external_helper_units_and_zero_matches_is_not_qualification(self):
        manifest = json.loads((DIRECTORY / "probe_manifest.json").read_text("utf-8"))
        self.assertEqual(manifest["required_external_units"], [])
        self.assertFalse(manifest["setter_qualification"])
        self.assertIn("matched_rules>0", manifest["completion_contract"])


if __name__ == "__main__":
    unittest.main()
