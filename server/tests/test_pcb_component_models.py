"""Model attachment spec tests; no Altium or native dispatch."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pcb_component_models as models

PCB = r"C:\work\demo-board.PcbDoc"


class BodyNamePrefixTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        step = Path(folder.name) / "demo_part.step"
        step.write_bytes(b"ISO-10303-21;\nEND-ISO-10303-21;\n")
        self.record = dict(designator="U1", model_path=str(step),
                           sha256=hashlib.sha256(step.read_bytes()).hexdigest(),
                           x_mm=1.0, y_mm=2.0, rotation_deg=0, side="top", footprint="SOIC8",
                           bounds_xy_mm=[-1, -1, 1, 1], color=255)
        # Windows path canonicalization is covered elsewhere; keep file checks local.
        patcher = patch.object(models, "path", lambda value, suffixes: value)
        patcher.start()
        self.addCleanup(patcher.stop)
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop(models.BODY_PREFIX_ENV, None)

    def header(self, **kwargs):
        return models.build_models_spec(PCB, [self.record], **kwargs).splitlines()[0]

    def test_default_prefix_is_appended_to_the_board_record(self):
        self.assertEqual(self.header(), "PCB|" + PCB + "|MCP3D_")

    def test_environment_and_argument_select_the_prefix(self):
        os.environ[models.BODY_PREFIX_ENV] = "BOARD3D_"
        self.assertEqual(self.header(), "PCB|" + PCB + "|BOARD3D_")
        self.assertEqual(self.header(body_name_prefix="X_"), "PCB|" + PCB + "|X_")
        os.environ[models.BODY_PREFIX_ENV] = ""
        self.assertEqual(self.header(), "PCB|" + PCB + "|MCP3D_")

    def test_unsafe_prefix_is_refused(self):
        for bad in ("", "3D_", "a|b", "a b", "x" * 25, "\u00fc_", "A\n"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.header(body_name_prefix=bad)
        os.environ[models.BODY_PREFIX_ENV] = "bad prefix"
        with self.assertRaises(ValueError):
            self.header()

    def test_pascal_takes_the_prefix_from_the_board_record(self):
        source = (Path(models.__file__).with_name("AltiumScript") / "pcb_primitives.pas").read_text(encoding="utf-8")
        body = source[source.index("function SetComponentModelsReport"):]
        body = body[:body.index("\nend;")]
        self.assertIn("Prefix := GetFieldFromPipeString(Spec[0],2);", body)
        self.assertIn("if Prefix = '' then Prefix := 'MCP3D_';", body)
        self.assertIn("Bodies[I].Name := Prefix+Ref+'_'", body)


if __name__ == "__main__":
    unittest.main()
