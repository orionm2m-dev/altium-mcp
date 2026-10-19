"""Offline validation tests; never import the server or start Altium."""

import ast
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pcb_primitives import build_pad_spec, build_keepout_spec, build_process_recovery_spec


PCB = r"C:\Boards\Controller.PcbDoc"
ALL = ["via", "track", "copper", "smd_pad", "th_pad"]


def pad(**changes):
    result = dict(operation="add", name="MH1", x_mm=-12.5, y_mm=10.5,
                  size_x_mm=0, size_y_mm=0, hole_mm=2.2, plated=False,
                  layer="multi", shape="round")
    result.update(changes)
    return result


def region(**changes):
    result = dict(unique_id="KO000001", kind="region", layer="bottom", restrictions=ALL,
                  vertices=[[0, 0], [2, 0], [2, 1], [0, 1]])
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
    def test_named_region_keeps_existing_input_encoding(self):
        fields = build_keepout_spec(PCB, [region()]).splitlines()[1].split("|")
        self.assertEqual(fields[:6], ["KEEPOUT", "KO000001", "region", "bottom", "31", "1"])
        self.assertEqual(fields[6:], ["0", "0", "2", "0", "2", "1", "0", "1"])

    def test_all_layers_only_when_explicit(self):
        self.assertIn("|all|31|", build_keepout_spec(PCB, [region(layer="all")]))
        with self.assertRaises(ValueError):
            build_keepout_spec(PCB, [region(layer=None)])

    def test_selective_restrictions_fail_explicitly(self):
        with self.assertRaisesRegex(ValueError, "selective"):
            build_keepout_spec(PCB, [region(restrictions=["via"])])

    def test_unsupported_kinds_rejected_even_with_otherwise_valid_geometry(self):
        for kind in ("arc", "track", "fill", None, "Region"):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "only kind=region"):
                build_keepout_spec(PCB, [region(kind=kind)])

    def test_unique_names_and_explicit_restrictions_are_required(self):
        for change in ({"unique_id": "lower"}, {"unique_id": "KO000001|"},
                       {"restrictions": []}, {"restrictions": ["everything"]},
                       {"locked": "false"}, {"width_mm": 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                build_keepout_spec(PCB, [region(**change)])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_keepout_spec(PCB, [region(), region()])

    def test_region_vertex_validation(self):
        invalid = ([], [[0, 0], [1, 0]], [[0, 0], [1, 1], [2, 2]],
                   [[0, 0], [1, 0], [0, 0]], [[0, 0], [1, 0], [0, float("inf")]],
                   [[0, 0], [1, 0], [0, float("nan")]], [[0, 0], [True, 0], [0, 1]],
                   [[0, 0], [1, 0], [0, 1, 2]])
        for points in invalid:
            with self.subTest(points=points), self.assertRaises(ValueError):
                build_keepout_spec(PCB, [region(vertices=points)])
        self.assertIn("|region|top|", build_keepout_spec(PCB, [region(layer="top", vertices=[[0, 0], [1, 0], [0, 1]])]))

    def test_native_region_identity_guard_prevents_unverified_save(self):
        source = Path(__file__).resolve().parents[1].joinpath("AltiumScript/pcb_primitives.pas").read_text(encoding="utf-8")
        body = source.split("function SetPcbKeepoutsReport", 1)[1]
        rejection = "only kind=region is supported"
        self.assertLess(body.index(rejection), body.index("OpenBoardAtPath(PcbPath)"))
        self.assertLess(body.index("duplicate requested keepout identity"), body.index("PCBServer.PreProcess;"))
        for guard in ("not P.IsKeepout", "P.Component <> nil", "P.InPolygon", "Count > 1",
                      "P.ObjectId <> eRegionObject", "Region.Name <> Id", "Region.Kind <> eRegionKind_Copper"):
            self.assertIn(guard, body)
            self.assertLess(body.index(guard), body.index("PCBServer.PreProcess;"))
        self.assertIn("Region.Name := Id", body)
        self.assertNotIn("P.UniqueId :=", body)
        self.assertLess(body.index("Board.AddPCBObject(P)"), body.index("ActualId := Region.Name"))
        self.assertIn("ItemIdentity := ActualId = Id", body)
        self.assertIn("IdentityVerified := False", body)
        self.assertIn("if IdentityVerified and FlagsVerified and (Error = '') then Saved := SaveBoardDocument(PcbPath)", body)
        self.assertIn('"identity_kind": "region_name"', body)
        self.assertIn("Contour := Region.MainContour", body)
        for unsupported in ("IPCB_Arc", "IPCB_Track", "IPCB_Fill", "Fill.X1", "KeepoutObjectId"):
            self.assertNotIn(unsupported, body)

    def test_native_lookup_counts_region_names_and_foreign_uid_collisions(self):
        source = Path(__file__).resolve().parents[1].joinpath("AltiumScript/pcb_primitives.pas").read_text(encoding="utf-8")
        lookup = source.split("function FindKeepoutPrimitive", 1)[1].split("function SetPcbKeepoutsReport", 1)[0]
        for token in ("Matches := P.UniqueId = Id", "Region.Name = Id", "if Matches then", "Count := Count + 1"):
            self.assertIn(token, lookup)
        self.assertEqual(lookup.count("Count := Count + 1"), 1)

    def test_server_python_parses_without_import_side_effects(self):
        ast.parse(Path(__file__).resolve().parents[1].joinpath("main.py").read_text(encoding="utf-8"))


class ProcessRecoveryTests(unittest.TestCase):
    def test_report_is_default_and_encodes_explicit_expected_depth(self):
        self.assertEqual(build_process_recovery_spec(PCB, 1), f"PCB|{PCB}\nPROCESS|1|0\n")
        self.assertEqual(build_process_recovery_spec(PCB, 0), f"PCB|{PCB}\nPROCESS|0|0\n")
        self.assertIn("PROCESS|1|1", build_process_recovery_spec(PCB, 1, True))

    def test_repair_never_accepts_nested_or_idle_depths(self):
        for value in (0, 2, 15, -1, True, 1.0, "1", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_process_recovery_spec(PCB, value, True)
        for value in (-1, True, 1.0, "1", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_process_recovery_spec(PCB, value)

    def test_requires_absolute_pcb_path_and_boolean_repair(self):
        for value in ("board.PcbDoc", r"C:board.PcbDoc", r"\board.PcbDoc", r"C:\board.PcbLib", PCB + "\nPROCESS|1|1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_process_recovery_spec(value, 1)
        for value in (1, "true", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_process_recovery_spec(PCB, 1, value)

    def test_native_recovery_is_single_guarded_postprocess_without_save(self):
        source = Path(__file__).resolve().parents[1].joinpath("AltiumScript/pcb_primitives.pas").read_text(encoding="utf-8")
        recovery = source.split("function RecoverPcbProcessReport", 1)[1].split("procedure KeepoutTrace", 1)[0]
        self.assertEqual(recovery.count("PCBServer.PostProcess;"), 1)
        for forbidden in ("SetProcessDepth", "PreProcess;", "SaveBoardDocument(", "OpenBoardAtPath(", "ShowDocument(", ".Focus;"):
            self.assertNotIn(forbidden, recovery)
        for guard in ("ClientBefore <> 0", "PcbBefore <> ExpectedDepth", "ExpectedDepth <> 1",
                      "Doc.FileName, PcbPath", "Board.FileName, PcbPath", "(Error = '') and Repair"):
            self.assertIn(guard, recovery)
            self.assertLess(recovery.index(guard), recovery.index("PCBServer.PostProcess;"))

    def test_mutators_guard_before_preprocess_and_keepouts_have_bounded_trace(self):
        source = Path(__file__).resolve().parents[1].joinpath("AltiumScript/pcb_primitives.pas").read_text(encoding="utf-8")
        for start in ("function SetPcbPadsReport", "function SetPcbKeepoutsReport"):
            body = source.split(start, 1)[1]
            self.assertLess(body.index("PcbPrimitiveProcessGuard(0)"), body.index("PCBServer.PreProcess;"))
        self.assertIn("while Log.Count >= 64 do Log.Delete(0)", source)
        self.assertIn("Log.SaveToFile(Path)", source)
        for phase in ("mutation_lookup", "set_region_name", "set_keepout_flag", "region_kind", "postprocess_completed", "save_if_verified"):
            self.assertIn("'" + phase + "'", source)

    def test_public_wrapper_does_not_dispatch_invalid_repair(self):
        import asyncio
        import json
        import tempfile
        from unittest.mock import AsyncMock
        tree = ast.parse(Path(__file__).resolve().parents[1].joinpath("main.py").read_text(encoding="utf-8"))
        selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name in {"_script_result", "recover_pcb_process", "set_pcb_keepouts"}]
        for node in selected:
            node.decorator_list = []
        module = ast.Module(body=selected, type_ignores=[])
        class Bridge:
            execute_command = AsyncMock(return_value={"success": True, "result": {"success": True}})
        with tempfile.TemporaryDirectory() as directory:
            env = {"json": json, "Context": object, "EXCHANGE_DIR": Path(directory), "SPEC_ENCODING": "utf-8",
                   "build_process_recovery_spec": build_process_recovery_spec, "build_keepout_spec": build_keepout_spec,
                   "altium_bridge": Bridge()}
            exec(compile(module, "isolated_public_wrappers", "exec"), env)
            invalid = json.loads(asyncio.run(env["recover_pcb_process"](None, PCB, 2, True)))
            self.assertFalse(invalid["success"])
            env["altium_bridge"].execute_command.assert_not_awaited()
            asyncio.run(env["recover_pcb_process"](None, PCB, 1))
            env["altium_bridge"].execute_command.assert_awaited_once_with("recover_pcb_process", {})
            self.assertIn("PROCESS|1|0", Path(directory, "pcb_process_recovery_spec.txt").read_text())
            env["altium_bridge"].execute_command.reset_mock()
            for kind in ("arc", "fill", "track"):
                rejected = json.loads(asyncio.run(env["set_pcb_keepouts"](None, PCB, [region(kind=kind)])))
                self.assertFalse(rejected["success"])
                self.assertIn("only kind=region", rejected["error"])
            env["altium_bridge"].execute_command.assert_not_awaited()
            env["altium_bridge"].execute_command.return_value = {"success": False, "error": "timeout"}
            failed = json.loads(asyncio.run(env["set_pcb_keepouts"](None, PCB, [region()])))
            self.assertFalse(failed["success"])
            self.assertEqual(failed["last_phase"], "dispatch_pending")
            self.assertTrue(Path(failed["trace_path"]).exists())


if __name__ == "__main__":
    unittest.main()
