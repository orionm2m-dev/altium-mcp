"""Offline tests: no Altium installation or MCP process is required."""

import asyncio
import configparser
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from step_export import build_export_project, build_step_outjob, export_step, NO_VARIATIONS

STEP = (b"ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('board'),'2;1');\n"
        b"FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\nENDSEC;\nDATA;\n"
        b"#1=PRODUCT('board','board','',());\nENDSEC;\nEND-ISO-10303-21;\n")


class StepExportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="step test ")
        self.root = Path(self.temp.name)
        self.project = self.root / "Controller.PrjPcb"
        self.pcb = self.root / "Controller.PcbDoc"
        self.panel = self.root / "Panel.PcbDoc"
        self.existing_job = self.root / "Production.OutJob"
        self.output = self.root / "Controller.step"
        self.project.write_text(
            "[Design]\nVersion=1.0\n[Document1]\nDocumentPath=Controller.PcbDoc\n"
            "[Document2]\nDocumentPath=Panel.PcbDoc\n[Document3]\nDocumentPath=Production.OutJob\n"
            "[ProjectVariant1]\nDescription=Wireless\n[ProjectVariant2]\nDescription=Wired\n",
            encoding="utf-8")
        self.pcb.write_bytes(b"pcb source")
        self.panel.write_bytes(b"panel source")
        self.existing_job.write_bytes(b"existing production job")
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    async def run_export(self, producer=None, **overrides):
        async def bridge(command, params):
            self.calls.append((command, params))
            if command == "export_pcb_step":
                job = Path(params["outjob_path"])
                if producer:
                    answer = producer(job)
                    if answer is not None:
                        return answer
                else:
                    suffix = "" if params["variant"] == NO_VARIATIONS else f"({params['variant']})"
                    (job.parent / f"Controller{suffix}.STEP").write_bytes(STEP)
            return {"success": True, "result": {"success": True}}
        args = dict(project_path=str(self.project), pcb_path=str(self.pcb),
                    output_path=str(self.output), variant="Wireless", encoding="utf-8",
                    verification_timeout=0.02)
        args.update(overrides)
        return await export_step(bridge, **args)

    async def test_exports_requested_pcb_and_variant_without_changing_sources(self):
        before = {p: p.read_bytes() for p in (self.project, self.pcb, self.panel, self.existing_job)}
        result = await self.run_export()
        self.assertTrue(result["success"], result)
        self.assertEqual(self.output.read_bytes(), STEP)
        self.assertEqual(result["size_bytes"], len(STEP))
        ini = configparser.ConfigParser(interpolation=None)
        ini.read(result["outjob_path"], encoding="utf-8")
        group = ini["OutputGroup1"]
        self.assertEqual(group["OutputDocumentPath1"], str(self.pcb.resolve()))
        self.assertEqual(group["VariantName"], "Wireless")
        self.assertEqual(group["OutputVariantName1"], "Wireless")
        self.assertEqual(group["OutputType1"], "ExportSTEP")
        self.assertEqual(group["OutputEnabled1_OutputMedium1"], "1")
        self.assertIn("ExportAsSinglePart=False", group["Configuration1_Item1"])
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    async def test_export_project_owns_job_and_references_original_sources(self):
        result = await self.run_export()
        self.assertTrue(result["success"], result)
        ini = configparser.ConfigParser(interpolation=None)
        ini.read(result["export_project_path"], encoding="utf-8")
        self.assertEqual(Path(ini["Document1"]["DocumentPath"]), self.pcb.resolve())
        self.assertEqual(Path(ini["Document2"]["DocumentPath"]), self.panel.resolve())
        self.assertEqual(Path(ini["Document3"]["DocumentPath"]), self.existing_job.resolve())
        self.assertEqual(Path(ini["Document4"]["DocumentPath"]), Path(result["outjob_path"]))
        self.assertEqual(ini["ProjectVariant1"]["Description"], "Wireless")
        self.assertEqual(ini["ProjectVariant2"]["Description"], "Wired")
        command, params = self.calls[0]
        self.assertEqual(command, "export_pcb_step")
        self.assertEqual(params["project_path"], str(self.project.resolve()))
        self.assertEqual(params["export_project_path"], result["export_project_path"])

    def test_project_copy_preserves_custom_settings_variants_and_path_resolution(self):
        original = (
            "; custom project comment\r\n[Design]\r\nVersion=1.0\r\nCustomSetting=100%|A=B\r\n"
            "[Document2]\r\nDocumentPath=sub folder\\..\\Controller.PcbDoc\r\nCustomDocument=Keep\r\n"
            "[Document7]\r\nDocumentPath=..\\Sibling.PcbDoc\r\n"
            "[ProjectVariant1]\r\nDescription=Wireless\r\n"
            "Variation1=Designator=R1|UniqueId=\\ABC|Kind=1|AlternatePart==Value\r\n"
            "[CustomSection]\r\nDescription=Do not change\r\nDocumentPath=leave this setting alone\r\n")
        self.project.write_bytes(original.encode("utf-8"))
        job = self.root / "new job.OutJob"
        copied = build_export_project(self.project, job, "utf-8").decode("utf-8")
        expected = original.replace("DocumentPath=sub folder\\..\\Controller.PcbDoc",
                                    "DocumentPath=" + str(self.pcb.resolve()))
        expected = expected.replace("DocumentPath=..\\Sibling.PcbDoc",
                                    "DocumentPath=" + str((self.root.parent / "Sibling.PcbDoc").resolve()))
        self.assertEqual(copied, expected + f"\r\n[Document8]\r\nDocumentPath={job}\r\n")
        self.assertEqual(self.project.read_bytes(), original.encode("utf-8"))

    async def test_explicit_no_variations_is_supported(self):
        result = await self.run_export(variant=NO_VARIATIONS)
        self.assertTrue(result["success"], result)

    async def test_wrong_variant_file_is_never_published(self):
        for name in ("Controller(Wired).step", "Controller.step", "Other(Wireless).step"):
            with self.subTest(name=name):
                def produce(job):
                    (job.parent / name).write_bytes(STEP)
                result = await self.run_export(produce)
                self.assertFalse(result["success"])
                self.assertIn("requested assembly variant", result["error"])
                self.assertFalse(self.output.exists())

    async def test_base_design_refuses_variant_file(self):
        def produce(job):
            (job.parent / "Controller(Wired).step").write_bytes(STEP)
        result = await self.run_export(produce, variant=NO_VARIATIONS)
        self.assertFalse(result["success"])
        self.assertIn("requested assembly variant", result["error"])
        self.assertFalse(self.output.exists())

    async def test_unknown_variant_fails_before_bridge(self):
        result = await self.run_export(variant="Not a variant")
        self.assertFalse(result["success"])
        self.assertIn("Unknown assembly variant", result["error"])
        self.assertEqual(self.calls, [])

    async def test_foreign_pcb_fails_before_bridge(self):
        foreign = self.root / "Other.PcbDoc"
        foreign.write_bytes(b"not a project document")
        result = await self.run_export(pcb_path=str(foreign))
        self.assertFalse(result["success"])
        self.assertIn("not a document", result["error"])
        self.assertEqual(self.calls, [])

    async def test_existing_output_is_preserved(self):
        self.output.write_bytes(b"keep me")
        result = await self.run_export()
        self.assertFalse(result["success"])
        self.assertEqual(self.output.read_bytes(), b"keep me")
        self.assertEqual(self.calls, [])

    async def test_output_created_during_export_is_preserved(self):
        def produce(job):
            (job.parent / "Controller(Wireless).step").write_bytes(STEP)
            self.output.write_bytes(b"created concurrently")
        result = await self.run_export(produce)
        self.assertFalse(result["success"])
        self.assertEqual(self.output.read_bytes(), b"created concurrently")

    async def test_nested_export_failure_is_not_success(self):
        def produce(job):
            (job.parent / "Controller(Wireless).step").write_bytes(STEP)
            return {"success": True, "result": '{"success": false, "error": "export refused"}'}
        result = await self.run_export(produce)
        self.assertFalse(result["success"])
        self.assertIn("export refused", result["error"])
        self.assertFalse(self.output.exists())

    async def test_transport_failure_is_not_success(self):
        result = await self.run_export(lambda job: {"success": False, "error": "timeout"})
        self.assertFalse(result["success"])
        self.assertIn("timeout", result["error"])
        self.assertFalse(self.output.exists())

    async def test_success_without_a_file_is_not_success(self):
        result = await self.run_export(lambda job: {"success": True})
        self.assertFalse(result["success"])
        self.assertIn("No fresh, complete STEP", result["error"])
        self.assertFalse(self.output.exists())

    async def test_old_or_truncated_file_is_not_success(self):
        for data, stale in ((STEP, True), (STEP[:-22], False), (b"arbitrary content" * 20, False)):
            with self.subTest(stale=stale, length=len(data)):
                def produce(job):
                    path = job.parent / "Controller(Wireless).step"
                    path.write_bytes(data)
                    if stale:
                        os.utime(path, (1, 1))
                result = await self.run_export(produce)
                self.assertFalse(result["success"])
                self.assertFalse(self.output.exists())

    async def test_multiple_generated_files_are_ambiguous(self):
        def produce(job):
            (job.parent / "first.step").write_bytes(STEP)
            (job.parent / "second.stp").write_bytes(STEP)
        result = await self.run_export(produce)
        self.assertFalse(result["success"])
        self.assertIn("more than one", result["error"])

    async def test_changed_source_prevents_publishing(self):
        def produce(job):
            (job.parent / "Controller(Wireless).step").write_bytes(STEP)
            self.pcb.write_bytes(b"changed during export")
        result = await self.run_export(produce)
        self.assertFalse(result["success"])
        self.assertIn("Source files changed", result["error"])
        self.assertFalse(self.output.exists())

    async def test_invalid_paths_and_injection_are_rejected(self):
        for override in (dict(project_path="relative.PrjPcb"),
                         dict(output_path=str(self.root / "bad.txt")),
                         dict(output_path=str(self.root / "missing" / "board.step")),
                         dict(variant="Wireless\nOutputDocumentPath1=other"),
                         dict(variant="Wireless|other")):
            with self.subTest(override=override):
                result = await self.run_export(**override)
                self.assertFalse(result["success"])
                self.assertEqual(self.calls, [])

    async def test_unrepresentable_variant_is_not_sent_to_altium(self):
        with self.project.open("a", encoding="utf-8") as stream:
            stream.write("[ProjectVariant3]\nDescription=\u65e5\u672c\u8a9e\n")
        result = await self.run_export(variant="\u65e5\u672c\u8a9e", encoding="ascii")
        self.assertFalse(result["success"])
        self.assertEqual(self.calls, [])
        self.assertFalse(self.output.exists())

    def test_model_options_use_documented_enum_values(self):
        self.assertIn("ExportModelsOption=2", build_step_outjob("board", "folder", "Wired", True))
        self.assertIn("ExportModelsOption=0", build_step_outjob("board", "folder", "Wired"))


if __name__ == "__main__":
    unittest.main()
