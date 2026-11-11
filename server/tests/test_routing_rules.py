"""Offline rule-spec validation and mutation-boundary regression tests."""
import ast
from pathlib import Path
import sys
import unittest

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
from routing_rules import build_rule_spec, flatten_values

PATH = r"C:\Boards\Qualification.PcbDoc"


class RuleSpecTests(unittest.TestCase):
    def rule(self, **changes):
        result = dict(name="SignalWidth", kind="Width", values={"min": .15, "preferred": .25, "max": 2.0})
        result.update(changes)
        return result

    def test_scopes_and_omitted_priority_are_preserved(self):
        spec = build_rule_spec(PATH, [self.rule(scope1="InNet('CLOCK')")])
        fields = spec.splitlines()[1].split("|")
        self.assertEqual(fields[3], "InNet('CLOCK')")
        self.assertEqual(fields[5:8], ["", "", ""])
        self.assertEqual(fields[8], "min=0.15;preferred=0.25;max=2")

    def test_layer_overrides_do_not_replace_other_layers(self):
        values = flatten_values("Width", {"layer_widths": [{"layer_id": 1, "preferred": .15}]})
        self.assertEqual(values, {"layer_1_preferred": "0.15"})

    def test_allowed_layers_are_boolean_and_explicit(self):
        values = flatten_values("RoutingLayers", {"routing_layers": [
            {"layer_id": 1, "allowed": True}, {"layer_id": 32, "allowed": False}]})
        self.assertEqual(values, {"layer_1_allowed": "1", "layer_32_allowed": "0"})

    def test_fanout_enums_and_grid_are_not_coordinate_converted_together(self):
        values = flatten_values("FanoutControl", dict(fanout_style=3, fanout_direction=5,
                               bga_direction=2, bga_via_mode=1, via_grid=.1))
        self.assertEqual(values["fanout_style"], "3")
        self.assertEqual(values["via_grid"], "0.1")

    def test_via_dimensions_and_style(self):
        values = flatten_values("RoutingViaStyle", dict(hole_min=.2, hole_preferred=.3,
                               hole_max=.4, pad_min=.5, pad_preferred=.6, pad_max=.7, via_style=0))
        self.assertEqual(values["via_style"], "0")
        self.assertEqual(values["pad_preferred"], "0.6")

    def test_clearance_cells_preserve_unspecified_cells_and_mode(self):
        self.assertEqual(flatten_values("Clearance", {"clearance_matrix": [
            dict(first=1, second=4, gap=.15)]}), {"matrix_1_4": "0.15"})
        self.assertEqual(flatten_values("Clearance", dict(matrix_enabled=True,
                         ignore_pad_to_pad=False)), {"matrix_enabled": "1", "ignore_pad_to_pad": "0"})

    def test_symmetric_duplicate_cells_are_rejected(self):
        with self.assertRaises(ValueError):
            flatten_values("Clearance", {"clearance_matrix": [dict(first=1,second=4,gap=.15),
                           dict(first=4,second=1,gap=.2)]})

    def test_outline_cell_domains_and_global_matrix_conflict(self):
        valid = {"clearance_matrix": [dict(first=1,second=12,gap=.3)]}
        self.assertEqual(flatten_values("BoardOutlineClearance", valid), {"matrix_1_12": "0.3"})
        for kind, values in [("Clearance", valid), ("BoardOutlineClearance", {"clearance_matrix": [dict(first=1,second=4,gap=.3)]}),
                             ("Clearance", dict(gap=.2, clearance_matrix=[dict(first=1,second=4,gap=.3)]))]:
            with self.subTest(kind=kind, values=values), self.assertRaises(ValueError):
                flatten_values(kind, values)

    def test_impedance_is_not_limited_as_a_coordinate_and_huge_integers_fail_cleanly(self):
        self.assertEqual(flatten_values("Width", {"max_impedance": 10000}), {"max_impedance": "10000"})
        with self.assertRaises(ValueError):
            flatten_values("Width", {"min": 10 ** 10000})

    def test_no_silent_unknown_keys_or_delimiter_substitution(self):
        for rule in [self.rule(values={"prefered": .15}), self.rule(name="a|b"),
                     self.rule(scope1="All\nDELETE|Width"), self.rule(unexpected=True),
                     self.rule(kind="InventedRule"), self.rule(values={"layer_widths": [{"layer_id": 1, "extra": 0}]})]:
            with self.subTest(rule=rule), self.assertRaises(ValueError):
                build_rule_spec(PATH, [rule])

    def test_nonfinite_boolean_numeric_and_out_of_range_values(self):
        for values in [{"min": True}, {"min": float("nan")}, {"min": float("inf")},
                       {"min": -.1}, {"max": 6000}, {"min": .4, "preferred": .3},
                       {"impedance_driven": 1}, {"layer_widths": [{"layer_id": 33, "min": .15}]}]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                flatten_values("Width", values)
        for value in [-1, 4, True, 1.2, "1"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                flatten_values("RoutingViaStyle", {"via_style": value})

    def test_duplicate_rule_and_priority_operations_are_rejected(self):
        for rules in [[self.rule(), self.rule(name="signalwidth")],
                      [self.rule(priority=1), self.rule(name="Other", priority=1)],
                      [self.rule(priority=True)], [self.rule(priority=0)],
                      [dict(name="SignalWidth", priority=1)]]:
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                build_rule_spec(PATH, rules)

    def test_explicit_priority_swap_is_not_renumbered(self):
        spec = build_rule_spec(PATH, [self.rule(priority=2), self.rule(name="Other", priority=1)])
        self.assertEqual([line.split("|")[5] for line in spec.splitlines()[1:]], ["2", "1"])

    def test_delete_is_exclusive_and_metadata_update_needs_no_kind(self):
        self.assertIn("DELETE|Old", build_rule_spec(PATH, [dict(name="Old", delete=True)]))
        self.assertIn("RULE|Old|||||false||", build_rule_spec(PATH, [dict(name="Old", enabled=False)]))
        with self.assertRaises(ValueError):
            build_rule_spec(PATH, [dict(name="Old", delete=True, values={})])

    def test_absolute_board_path_and_nonempty_bounded_batch(self):
        for path in ["relative.PcbDoc", r"C:relative.PcbDoc", r"C:\Boards\x.txt", PATH + "\nPCB|other"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                build_rule_spec(path, [self.rule()])
        for rules in [[], None, {}, [self.rule()] * 513]:
            with self.subTest(rules=type(rules)), self.assertRaises(ValueError):
                build_rule_spec(PATH, rules)

    def test_subtractive_mask_expansion_remains_supported(self):
        self.assertEqual(flatten_values("PasteMaskExpansion", {"expansion": -.05}), {"expansion": "-0.05"})


class BackendBoundaryTests(unittest.TestCase):
    def test_builder_has_no_native_dependencies(self):
        tree = ast.parse((SERVER / "routing_rules.py").read_text("utf-8"))
        imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertLessEqual(imports, {"math", "pathlib"})

    def test_readback_does_not_open_focus_save_or_mutate(self):
        source = (SERVER.parent / "dev/probes/routing_rules/pcb_document.candidate.pas").read_text("utf-8-sig")
        body = source.split("function GetPcbRoutingRulesReport(", 1)[1].split("// Set the numbers of a rule", 1)[0]
        for forbidden in ["OpenBoardAtPath(", "SaveBoardDocument(", "PreProcess", ".Focus", ".DRCEnabled :=", "GetState_DesignRuleCheckerOptions"]:
            self.assertNotIn(forbidden, body)
        self.assertIn("Board.I_ObjectAddress <> Current.I_ObjectAddress", body)
        self.assertIn('"drc_coverage_verified":false', body)
        self.assertIn('"batch_rule_kinds":null', body)

    def test_rule_preflight_precedes_transaction_and_save_failure_is_reported(self):
        source = (SERVER.parent / "dev/probes/routing_rules/pcb_document.candidate.pas").read_text("utf-8-sig")
        body = source.split("function SetPcbRulesReport(", 1)[1].split("// A contour", 1)[0]
        self.assertLess(body.index("RoutingRulePreflight(Board, Spec)"), body.index("PCBServer.PreProcess"))
        self.assertIn("finally\n            PCBServer.PostProcess;", body)
        self.assertIn("if not SaveBoardDocument(PcbPath) then", body)
        self.assertIn("finally\n                        if Action = 'updated'", body)
        self.assertIn("RoutingRuleBoundsError(Rule, Kind", source)
        self.assertIn("MMsToCoord(GMin) > MMsToCoord(GPref)", source)


if __name__ == "__main__":
    unittest.main()
