"""Read-only public environment wrapper; all native inspection is mocked."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class EnvironmentToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_preserves_ready_and_blocked_inventory_without_dispatch(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text("utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == "get_altium_environment")
        self.assertEqual([a.arg for a in node.args.args], ["ctx"])
        node.decorator_list = []
        inspect = Mock()
        env = dict(Context=object, json=json,
            altium_bridge=SimpleNamespace(config=SimpleNamespace(altium_exe_path="configured.exe")),
            script_recovery=SimpleNamespace(inspect_environment=inspect))
        exec(compile(ast.Module(body=[node], type_ignores=[]), "environment_tool", "exec"), env)
        for ready in (True, False):
            with self.subTest(ready=ready):
                observation = {"ready": ready, "native_idle_verified": False,
                    "process_windows": {"123": [{"hwnd": 456, "class": "TForm", "title": "Setup"}]},
                    "dialog_handles": [456]}
                inspect.return_value = observation
                result = json.loads(await env["get_altium_environment"](None))
                self.assertEqual(result, dict(observation, no_new_dispatch=True))
                self.assertNotIn("no_new_dispatch", observation)
                inspect.assert_called_with("configured.exe")
        self.assertEqual(inspect.call_count, 2)


if __name__ == "__main__":
    unittest.main()
