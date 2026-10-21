"""Offline native strategy service tests. All process/native actions are mocked."""
import ast
import asyncio
from contextlib import ExitStack, contextmanager
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import native_situs_service as service

PID = 123
CREATED = '134370000000000123'
PCB = r'C:\boards\scratch.PcbDoc'
EXE = r'C:\Altium\X2.EXE'
OP = 'a' * 32


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lock = asyncio.Lock()
        self.clock = 0
        self.mode = 'success'
        self.launch_code = 0
        self.claims = 0
        self.gate = asyncio.Event()
        self.sleep_entered = asyncio.Event()
        self.native_override = {}
        self.native_data = {'success': True, 'outcome_unknown': False, 'readback_verified': True}
        self.identity = dict(pid=PID, creation_filetime=int(CREATED), exe=EXE, user_sid='test', running=True)
        self.process = Mock()
        self.process.__enter__ = Mock(return_value=self.process)
        self.process.__exit__ = Mock(return_value=False)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(service.emergency_stop, 'WinProcess', return_value=self.process))
        self.stack.enter_context(patch.object(service.emergency_stop, 'emergency_claim', side_effect=self.claim))
        self.identify = self.stack.enter_context(patch.object(service, '_identity', side_effect=lambda *a: dict(self.identity)))
        self.environment = self.stack.enter_context(patch.object(service, '_environment', return_value={'ready': True, 'foreground_required': False}))
        self.stack.enter_context(patch.object(service.script_recovery, '_launches', {}))
        self.launch = self.stack.enter_context(patch.object(service.script_recovery, '_launch', side_effect=self.launch_native))
        self.stack.enter_context(patch.object(service, 'time', SimpleNamespace(monotonic=lambda: self.clock)))
        self.real_sleep = asyncio.sleep
        self.stack.enter_context(patch.object(service.asyncio, 'sleep', side_effect=self.sleep))

    @contextmanager
    def claim(self, exchange):
        self.claims += 1
        try:
            yield
        finally:
            self.claims -= 1

    async def sleep(self, seconds):
        self.clock += seconds
        if self.mode == 'cancel':
            self.sleep_entered.set()
            await self.gate.wait()
            self.mode = 'success'
            self.publish_response()
        elif self.mode == 'partial_json':
            self.mode = 'success'
            self.publish_response()
        await self.real_sleep(0)

    def publish_response(self):
        folders = list((self.root / 'native_situs_control').iterdir())
        self.folder = folders[-1]
        raw = (self.folder / 'request.json').read_bytes()
        request = json.loads(raw)
        response = {key: request[key] for key in ('schema_version', 'request_id', 'action', 'pcb_path', 'pid', 'creation_filetime', 'exe')}
        response.update(request_sha256=hashlib.sha256(raw).hexdigest(), success=True,
                        native_handler_completed=True, operation_invoked=True,
                        outcome_unknown=False, retry_allowed=False, document_saved=False,
                        data=dict(self.native_data))
        response.update(self.native_override)
        (self.folder / 'response.json').write_text(json.dumps(response), encoding='utf-8')

    def launch_native(self, exe, argument):
        self.assertTrue(self.lock.locked())
        self.assertEqual(self.claims, 1)
        state = service.script_recovery.blocked_state(self.root)
        self.assertEqual(state['phase'], 'native_module_pending')
        self.assertEqual(state['token'], OP)
        self.assertEqual(exe, EXE)
        self.assertRegex(argument, r'^-RNativeSitusControl:ExecuteRequest\(Request=[A-Za-z0-9_-]+\|Sha256=[0-9a-f]{64}\)$')
        if self.mode == 'launch_error':
            raise OSError('simulated launch failure')
        if self.mode not in ('missing', 'cancel', 'partial_json'):
            self.publish_response()
        elif self.mode == 'partial_json':
            (self.root / 'native_situs_control' / OP / 'response.json').write_text('{', encoding='utf-8')
        launcher = Mock(pid=321)
        launcher.poll.side_effect = lambda: self.launch_code
        return launcher

    async def call(self, **changes):
        arguments = dict(action='read_settings', pcb_path=PCB, target_pid=PID,
                         expected_creation_filetime='ft:' + CREATED, payload={},
                         operation_id=OP, timeout_seconds=1)
        arguments.update(changes)
        return await service.control(EXE, self.root, self.lock, **arguments)

    def set_payload(self, changes=None):
        return {'expected_snapshot_hash': '1' * 64, 'changes': changes or {'prefer_orthogonal': True}}

    async def test_complete_correlated_native_result_and_identity_checks(self):
        result = await self.call()
        self.assertTrue(result['success'])
        self.assertFalse(result['outcome_unknown'])
        self.assertTrue(result['completion_verified'])
        self.assertIsNone(service.script_recovery.blocked_state(self.root))
        self.assertEqual(self.launch.call_count, 1)
        self.assertFalse(self.lock.locked())
        self.assertEqual(self.claims, 0)
        self.assertEqual(self.identify.call_count, 3)
        for call in self.identify.call_args_list:
            self.assertEqual(call.args, (self.process, EXE, PID, int(CREATED)))
        self.assertEqual(self.environment.call_count, 3)
        saved = json.loads((self.root / 'native_situs_control' / OP / 'result.json').read_text('utf-8'))
        self.assertTrue(saved['success'])
        self.assertFalse(saved['document_saved'])

    async def test_set_payload_and_full_filetime_are_losslessly_bound(self):
        result = await self.call(action='set_settings', payload=self.set_payload({'name': 'scratch', 'layer_directions': [{'layer_id': 4294967295, 'direction': 11}]}))
        self.assertTrue(result['success'])
        request = json.loads((self.folder / 'request.json').read_bytes())
        self.assertEqual(request['creation_filetime'], CREATED)
        self.assertEqual(request['payload']['changes']['layer_directions'][0]['layer_id'], 4294967295)

    async def test_same_operation_returns_history_without_recheck_or_launch(self):
        first = await self.call()
        identities = self.identify.call_count
        second = await self.call()
        self.assertTrue(first['success'] and second['success'])
        self.assertTrue(second['replayed'])
        self.assertFalse(second['current_state_rechecked'])
        self.assertEqual(self.launch.call_count, 1)
        self.assertEqual(self.identify.call_count, identities)

    async def test_same_operation_different_binding_refused_without_launch(self):
        await self.call()
        for changed in ({'pcb_path': r'C:\boards\other.PcbDoc'}, {'target_pid': 999},
                        {'expected_creation_filetime': 'ft:134370000000000124'},
                        {'action': 'set_settings', 'payload': self.set_payload()}):
            result = await self.call(**changed)
            self.assertFalse(result['success'])
            self.assertIn('different request', result['error'])
        self.assertEqual(self.launch.call_count, 1)

    async def test_missing_response_launcher_zero_stays_unknown_and_blocks_next(self):
        self.mode = 'missing'
        result = await self.call()
        self.assertFalse(result['success'])
        self.assertTrue(result['outcome_unknown'])
        self.assertEqual(result['launcher_exit_code'], 0)
        self.assertEqual(service.script_recovery.blocked_state(self.root)['phase'], 'native_module_pending')
        repeat = await self.call()
        self.assertTrue(repeat['replayed'] and repeat['outcome_unknown'])
        fresh = await self.call(operation_id='b' * 32)
        self.assertTrue(fresh['blocked'])
        self.assertEqual(self.launch.call_count, 1)

    async def test_existing_request_without_result_is_never_replayed(self):
        await self.call()
        (self.folder / 'result.json').unlink()
        result = await self.call()
        self.assertTrue(result['outcome_unknown'] and result['replayed'])
        self.assertEqual(self.launch.call_count, 1)

    async def test_nested_partial_mutation_stays_unknown_and_blocked(self):
        self.native_data = dict(success=False, outcome_unknown=True, mutation_attempted=True)
        result = await self.call(action='set_settings', payload=self.set_payload())
        self.assertFalse(result['success'])
        self.assertTrue(result['outcome_unknown'])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))
        self.assertTrue(result['native_response']['data']['mutation_attempted'])

    async def test_known_stale_native_refusal_can_complete_without_claiming_success(self):
        self.native_data = dict(success=False, outcome_unknown=False, mutation_attempted=False, error='stale_expected_snapshot')
        result = await self.call(action='set_settings', payload=self.set_payload())
        self.assertFalse(result['success'])
        self.assertFalse(result['outcome_unknown'])
        self.assertTrue(result['completion_verified'])
        self.assertIsNone(service.script_recovery.blocked_state(self.root))

    async def test_native_refusal_before_dispatch_is_known_not_success(self):
        self.native_override = dict(success=False, native_handler_completed=False, operation_invoked=False)
        result = await self.call()
        self.assertFalse(result['success'])
        self.assertFalse(result['outcome_unknown'])
        self.assertIsNone(service.script_recovery.blocked_state(self.root))

    async def test_hash_pid_creation_and_path_mismatch_never_complete(self):
        for override in ({'request_sha256': '0' * 64}, {'pid': 999}, {'creation_filetime': str(int(CREATED) + 1)}, {'pcb_path': r'C:\else.PcbDoc'}):
            with self.subTest(override=override):
                self.native_override = override
                result = await self.call()
                self.assertFalse(result['success'])
                self.assertTrue(result['outcome_unknown'])
                for file in (self.root / 'native_situs_control' / OP).iterdir():
                    file.unlink()
                (self.root / 'native_situs_control' / OP).rmdir()
                (self.root / 'script_recovery_state.json').unlink()

    async def test_native_success_with_live_or_failed_launcher_is_not_verified(self):
        for code in (None, 1):
            with self.subTest(code=code):
                self.launch_code = code
                result = await self.call()
                self.assertFalse(result['success'])
                self.assertTrue(result['outcome_unknown'])
                for file in (self.root / 'native_situs_control' / OP).iterdir():
                    file.unlink()
                (self.root / 'native_situs_control' / OP).rmdir()
                (self.root / 'script_recovery_state.json').unlink()

    async def test_partial_json_is_observed_until_complete(self):
        self.mode = 'partial_json'
        result = await self.call()
        self.assertTrue(result['success'])
        self.assertEqual(self.launch.call_count, 1)

    async def test_launch_exception_keeps_prelaunch_pending_latch(self):
        self.mode = 'launch_error'
        result = await self.call()
        self.assertFalse(result['success'])
        self.assertTrue(result['dispatch_attempted'] and result['outcome_unknown'])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))

    async def test_exact_process_identity_change_after_dispatch_is_unknown(self):
        self.identify.side_effect = [dict(self.identity), dict(self.identity),
                                    dict(self.identity, creation_filetime=int(CREATED) + 1)]
        result = await self.call()
        self.assertFalse(result['success'])
        self.assertTrue(result['outcome_unknown'])
        self.assertIn('Process identity changed', result['error'])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))

    async def test_preflight_identity_refusal_does_not_create_request_or_launch(self):
        self.identify.side_effect = ValueError('Exact process identity mismatch')
        result = await self.call()
        self.assertFalse(result['success'])
        self.assertFalse(result['dispatch_attempted'])
        self.assertFalse(result['outcome_unknown'])
        self.launch.assert_not_called()
        self.assertFalse((self.root / 'native_situs_control').exists())

    async def test_native_identity_or_readiness_change_after_dispatch_is_unknown(self):
        self.environment.side_effect = [{'ready': True}, {'ready': True}, ValueError('modal after')]
        result = await self.call()
        self.assertFalse(result['success'])
        self.assertTrue(result['outcome_unknown'])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))

    async def test_cancel_retains_shared_lock_claim_and_process_until_observation_finishes(self):
        self.mode = 'cancel'
        task = asyncio.create_task(self.call())
        await self.sleep_entered.wait()
        task.cancel()
        await self.real_sleep(0)
        self.assertTrue(self.lock.locked())
        self.assertEqual(self.claims, 1)
        self.process.__exit__.assert_not_called()
        self.assertFalse(task.done())
        task.cancel()
        await self.real_sleep(0)
        self.assertTrue(self.lock.locked())
        self.gate.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.lock.locked())
        self.assertEqual(self.claims, 0)
        self.process.__exit__.assert_called_once()
        self.assertEqual(self.launch.call_count, 1)
        self.assertIsNone(service.script_recovery.blocked_state(self.root))

    async def test_shared_lock_serializes_the_entire_preflight_and_dispatch(self):
        await self.lock.acquire()
        task = asyncio.create_task(self.call())
        await self.real_sleep(0)
        self.identify.assert_not_called()
        self.launch.assert_not_called()
        self.lock.release()
        self.assertTrue((await task)['success'])

    async def test_invalid_payloads_are_rejected_before_identity_or_dispatch(self):
        invalid = [dict(action='read_settings', payload={'x': 1}), dict(action='start'),
                   dict(action='set_settings', payload=self.set_payload({'passes': 'opaque'})),
                   dict(action='set_settings', payload=self.set_payload({'via_bias': 1})),
                   dict(action='set_settings', payload=self.set_payload({'prefer_orthogonal': 1})),
                   dict(action='set_settings', payload=self.set_payload({'layer_directions': [{'layer_id': True, 'direction': 1}]})),
                   dict(expected_creation_filetime=CREATED), dict(target_pid=True), dict(timeout_seconds=True)]
        for changed in invalid:
            with self.subTest(changed=changed):
                result = await self.call(**changed)
                self.assertFalse(result['success'])
                self.assertFalse(result['dispatch_attempted'])
        self.identify.assert_not_called()
        self.launch.assert_not_called()

    async def test_native_direction_count_and_utf16_text_bounds_rejected_before_dispatch(self):
        for changes in ({'layer_directions': [{'layer_id': i, 'direction': 1} for i in range(257)]},
                        {'name': '\U0001f600' * 129}, {'description': '\U0001f600' * 2049}):
            with self.subTest(field=next(iter(changes))):
                result = await self.call(action='set_settings', payload=self.set_payload(changes))
                self.assertFalse(result['success'])
                self.assertFalse(result['dispatch_attempted'])
        self.identify.assert_not_called()
        self.launch.assert_not_called()

    async def test_final_result_persistence_failure_cannot_return_success_or_clear_latch(self):
        original = service._write
        def failing(path, data):
            if path.name == 'result.json' and data.get('completion_verified'):
                raise OSError('final result disk failure')
            return original(path, data)
        with patch.object(service, '_write', side_effect=failing):
            result = await self.call()
        self.assertFalse(result['success'])
        self.assertTrue(result['outcome_unknown'])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))
        self.assertEqual(self.launch.call_count, 1)

    async def test_recovery_clear_failure_does_not_return_success(self):
        original = service.script_recovery._write_state
        def failing(exchange, state):
            if state['phase'] == 'recovered':
                raise OSError('recovery state disk failure')
            return original(exchange, state)
        with patch.object(service.script_recovery, '_write_state', side_effect=failing):
            result = await self.call()
        self.assertFalse(result['success'])
        self.assertTrue(result['outcome_unknown'])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))


class PublicWrapperTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_registered_wrapper_fastmcp_invocation_without_main_import(self):
        from mcp.server.fastmcp import FastMCP, Context
        main_before = sys.modules.get('main')
        tree = ast.parse((SERVER / 'main.py').read_text('utf-8'))
        nodes = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'control_situs_strategy']
        self.assertEqual(len(nodes), 1)
        mcp = FastMCP('native-situs-service-offline')
        lock = asyncio.Lock()
        bridge = SimpleNamespace(config=SimpleNamespace(altium_exe_path=EXE), _command_lock=lock)
        env = dict(Context=Context, json=json, mcp=mcp, altium_bridge=bridge, EXCHANGE_DIR='exchange')
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'actual_native_situs_wrapper', 'exec'), env)
        registered = {tool.name: tool for tool in await mcp.list_tools()}
        self.assertEqual(set(registered), {'control_situs_strategy'})
        self.assertEqual(registered['control_situs_strategy'].inputSchema['properties']['expected_creation_filetime']['type'], 'string')
        with patch.object(service, 'control', return_value={'success': True}) as mock_control:
            result = await mcp.call_tool('control_situs_strategy', dict(action='read_settings', pcb_path=PCB,
                target_pid=PID, expected_creation_filetime='ft:' + CREATED, payload={}, operation_id=OP))
        self.assertTrue(json.loads(result[0].text)['success'])
        args = mock_control.call_args
        self.assertEqual(args.args, (EXE, 'exchange', lock))
        self.assertEqual(args.kwargs['expected_creation_filetime'], 'ft:' + CREATED)
        self.assertEqual(args.kwargs['payload'], {})
        self.assertIs(sys.modules.get('main'), main_before)


if __name__ == '__main__':
    unittest.main()
