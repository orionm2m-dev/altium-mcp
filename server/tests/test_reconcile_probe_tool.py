"""Public late-probe wrapper contract; no native calls."""
import ast
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class ReconcileToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text("utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == "reconcile_script_probe")
        self.assertEqual([a.arg for a in node.args.args], ["ctx", "recovery_token"])
        node.decorator_list = []
        self.lock = asyncio.Lock()
        self.claimed = False
        self.state = {"phase": "probe_pending", "token": "a" * 32}
        @contextmanager
        def claim(exchange):
            self.assertTrue(self.lock.locked())
            self.claimed = True
            try:
                yield
            finally:
                self.claimed = False
        def reconcile(*args):
            self.assertTrue(self.lock.locked())
            self.assertTrue(self.claimed)
            return {"recovered": True, "no_new_dispatch": True}
        self.recovery = SimpleNamespace(
            blocked_state=Mock(side_effect=lambda *_: self.state),
            _blocked=Mock(side_effect=lambda state, error, **kw:
                dict(recovered=False, blocked=True, error=error, **kw)),
            _reconcile_late_probe=Mock(side_effect=reconcile))
        self.emergency = SimpleNamespace(emergency_claim=Mock(side_effect=claim))
        env = dict(Context=object, json=json, EXCHANGE_DIR="exchange",
            altium_bridge=SimpleNamespace(_command_lock=self.lock,
                config=SimpleNamespace(altium_exe_path="configured.exe")),
            script_recovery=self.recovery, emergency_stop=self.emergency)
        exec(compile(ast.Module(body=[node], type_ignores=[]), "public_reconcile", "exec"), env)
        self.call = env["reconcile_script_probe"]

    async def test_only_existing_reconciliation_runs_after_both_locks(self):
        await self.lock.acquire()
        task = asyncio.create_task(self.call(None, "a" * 32))
        await asyncio.sleep(0)
        self.emergency.emergency_claim.assert_not_called()
        self.lock.release()
        result = json.loads(await task)
        self.assertTrue(result["recovered"])
        self.assertTrue(result["no_new_dispatch"])
        self.recovery._reconcile_late_probe.assert_called_once_with("configured.exe", "exchange", self.state)
        self.assertFalse(self.lock.locked())
        self.assertFalse(self.claimed)

    async def test_wrong_token_or_phase_cannot_reconcile(self):
        for state, token in [(self.state, "b" * 32), (self.state, None),
                             (self.state, ""), (None, "a" * 32),
                             ({"phase": "blocked_after_stop", "token": "a" * 32}, "a" * 32)]:
            with self.subTest(state=state, token=token):
                self.state = state
                result = json.loads(await self.call(None, token))
                self.assertTrue(result["blocked"])
                self.assertTrue(result["no_new_dispatch"])
        self.recovery._reconcile_late_probe.assert_not_called()

    async def test_claim_failure_blocks_without_evidence_or_dispatch(self):
        self.emergency.emergency_claim.side_effect = OSError("busy")
        result = json.loads(await self.call(None, "a" * 32))
        self.assertTrue(result["blocked"])
        self.assertTrue(result["no_new_dispatch"])
        self.recovery.blocked_state.assert_not_called()
        self.recovery._reconcile_late_probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
