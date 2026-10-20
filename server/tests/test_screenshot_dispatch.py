"""Check diagnostic screenshots do not depend on the script executor."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock


class ScreenshotDispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
        node = next(n for n in ast.parse(source).body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == "_prepare_screenshot_view")
        self.dispatch = AsyncMock(return_value={"success": True, "result": {"focused_kind": "PCB"}})
        env = {"altium_bridge": SimpleNamespace(execute_command=self.dispatch)}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "screenshot_helper", "exec"), env)
        self.prepare = env["_prepare_screenshot_view"]

    async def test_current_never_dispatches_even_if_executor_is_broken(self):
        self.dispatch.side_effect = RuntimeError("executor unavailable")
        self.assertEqual(await self.prepare("CURRENT", None), {"success": True, "result": {}})
        self.dispatch.assert_not_awaited()

    async def test_current_rejects_zoom_before_dispatch(self):
        result = await self.prepare("current", ["U1"])
        self.assertFalse(result["success"])
        self.dispatch.assert_not_awaited()

    async def test_editor_preserves_exact_zoom_request(self):
        result = await self.prepare("PCB", ["U1"])
        self.assertTrue(result["success"])
        self.dispatch.assert_awaited_once_with("take_view_screenshot", {"view_type": "pcb", "designators": ["U1"]})

    async def test_editor_failure_is_not_retried_or_relabeled(self):
        self.dispatch.return_value = {"success": False, "error": "compile failure"}
        self.assertEqual(await self.prepare("sch", None), self.dispatch.return_value)
        self.assertEqual(self.dispatch.await_count, 1)

    async def test_unknown_mode_cannot_silently_bypass_dispatch(self):
        self.assertFalse((await self.prepare("anything", None))["success"])
        self.dispatch.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
