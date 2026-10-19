"""Offline validation tests; never import the server or start Altium."""

import ast
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pcb_primitives import build_pad_spec, build_keepout_spec


PCB = r"C:\Boards\Controller.PcbDoc"
ALL = ["via", "track", "copper", "smd_pad", "th_pad"]


def pad(**changes):
    result = dict(operation="add", name="MH1", x_mm=-12.5, y_mm=10.5,
                  size_x_mm=0, size_y_mm=0, hole_mm=2.2, plated=False,
                  layer="multi", shape="round")
    result.update(changes)
    return result


def arc(**changes):
    result = dict(unique_id="KO000001", kind="arc", layer="bottom", restrictions=ALL,
                  x_mm=0, y_mm=0, radius_mm=3.325, start_deg=0, end_deg=360, width_mm=1.95)
    result.update(changes)
    return result


class PadSpecTests(unittest.TestCase):
    def test_free_npth_uses_zero_copper_sizes(self):
        line = build_pad_spec(PCB, [pad()]).splitlines()[1].split("|")
        self.assertEqual(line[1:4], ["add", "", "MH1"])
        self.assertEqual(line[6:12], ["0", "0", "2.2", "0", "multi", "round"])

    def test_hole_only_update_does_not_emit_geometry_or_net_changes(self):
        line = build_pad_spec(PCB, [dict(operation="update", component="J1", name="1", hole_mm=0)]).splitlines()[1].split("|")
        self.assertEqual(line[2:4], ["J1", "1"])
        self.assertEqual(line[4:8], ["", "", "", ""])
        self.assertEqual(line[8], "0")
        self.assertEqual(line[9:], ["", "", "", ""])

    def test_entire_batch_validates_before_returning(self):
        with self.assertRaises(ValueError):
            build_pad_spec(PCB, [pad(), pad(name="MH2", hole_mm=-1)])

    def test_duplicates_are_case_insensitive(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_pad_spec(PCB, [pad(), pad(name="mh1")])

    def test_rejects_component_add_and_incomplete_resize(self):
        for item in (pad(component="J1"), {"operation": "update", "name": "1", "size_x_mm": 1}):
            with self.subTest(item=item), self.assertRaises(ValueError):
                build_pad_spec(PCB, [item])

    def test_rejects_invalid_values_without_silent_coercion(self):
        for change in ({"hole_mm": float("nan")}, {"x_mm": float("inf")}, {"x_mm": True},
                       {"plated": "false"}, {"layer": "unknown"}, {"layer": "top"},
                       {"operation": "delete"}, {"net": "GND"}, {"name": "1|PAD"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                build_pad_spec(PCB, [pad(**change)])

    def test_path_and_text_cannot_inject_records(self):
        for bad_path in (PCB + "\nPAD|add", PCB + "|x", PCB + "\0", r"C:\Library.PcbLib"):
            with self.assertRaises(ValueError):
                build_pad_spec(bad_path, [pad()])


class KeepoutSpecTests(unittest.TestCase):
    def test_annulus_keeps_explicit_side_and_full_circle(self):
        fields = build_keepout_spec(PCB, [arc()]).splitlines()[1].split("|")
        self.assertEqual(fields[:6], ["KEEPOUT", "KO000001", "arc", "bottom", "31", "1"])
        self.assertEqual(fields[6:], ["0", "0", "3.325", "0", "360", "1.95"])

    def test_all_layers_only_when_explicit(self):
        self.assertIn("|all|31|", build_keepout_spec(PCB, [arc(layer="all")]))
        with self.assertRaises(ValueError):
            build_keepout_spec(PCB, [arc(layer=None)])

    def test_selective_restrictions_fail_explicitly(self):
        with self.assertRaisesRegex(ValueError, "selective"):
            build_keepout_spec(PCB, [arc(restrictions=["via"])])

    def test_unique_ids_and_explicit_restrictions_are_required(self):
        for change in ({"unique_id": "lower"}, {"restrictions": []}, {"restrictions": ["everything"]},
                       {"start_deg": 360, "end_deg": 0}, {"end_deg": 720}, {"width_mm": 0}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                build_keepout_spec(PCB, [arc(**change)])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_keepout_spec(PCB, [arc(), arc()])

    def test_fill_and_region_validation(self):
        base = dict(unique_id="KO000002", layer="top", restrictions=ALL)
        self.assertIn("|fill|top|", build_keepout_spec(PCB, [dict(base, kind="fill", x1_mm=0, y1_mm=0, x2_mm=1, y2_mm=2)]))
        for points in ([[0, 0], [1, 1], [2, 2]], [[0, 0], [1, 0], [0, 0]]):
            with self.assertRaises(ValueError):
                build_keepout_spec(PCB, [dict(base, kind="region", vertices=points)])
        self.assertIn("|region|top|", build_keepout_spec(PCB, [dict(base, kind="region", vertices=[[0, 0], [1, 0], [0, 1]])]))

    def test_server_python_parses_without_import_side_effects(self):
        ast.parse(Path(__file__).resolve().parents[1].joinpath("main.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
