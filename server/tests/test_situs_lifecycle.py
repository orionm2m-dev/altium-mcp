import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from situs_lifecycle import SitusJobManager, build_start_request, CONTROL_ACTIONS

BINDING = dict(pid=123, creation_time=987654321, exe_path=r'C:\Altium\X2.EXE', pcb_path=r'C:\boards\test.PcbDoc')
SHA = 'a' * 64


class Adapter:
    supports_concurrent_control = True

    def __init__(self):
        self.state = 'idle'
        self.dispatched = []
        self.control_operation = None
        self.release = asyncio.Event()
        self.failure = None
        self.patch = {}

    async def observe(self, job):
        request = job['request']
        value = dict(binding=request['binding'], state_verified=True,
                     observation_id=str(time.time_ns()), observed_at=time.time(),
                     router_state=self.state, modal_blocked=False,
                     saved_sha256=request['expected_sha256'], settings_digest=request['settings_digest'],
                     target_digest=request['target_digest'], global_rules_verified=True,
                     rules_digest='verified-rules', client_depth=0, pcb_depth=0,
                     job_id=job['job_id'], start_operation_id=job.get('start_operation_id'),
                     control_operation_id=self.control_operation,
                     router_returned=self.state in {'completed', 'stopped', 'reset'})
        value.update(self.patch)
        return value

    async def dispatch(self, job, action, operation_id):
        self.dispatched.append((action, operation_id))
        # Demonstrate durable intent already exists before calling the adapter.
        assert any(op['id'] == operation_id and op['dispatch_attempted'] for op in job['operations'])
        if self.failure:
            raise self.failure
        if action == 'start':
            await self.release.wait()
        return {'accepted': True}


class RequestTests(unittest.TestCase):
    def test_typed_inputs(self):
        for patch in ({'pid': True}, {'creation_time': 0}, {'pcb_path': r'C:\boards\..\a.PcbDoc'},
                      {'exe_path': r'C:\other.exe'}):
            with self.assertRaises(ValueError):
                build_start_request(dict(BINDING, **patch), SHA)
        for args in ({'scope': 'all', 'targets': {}}, {'scope': 'area', 'targets': [0, 0, 0, 1]},
                     {'scope': 'net', 'targets': ['A', 'A']}, {'options': {'via_cost': float('nan')}},
                     {'options': {'rip_up_violations': 1}}, {'options': {'unknown': True}}):
            with self.assertRaises(ValueError):
                build_start_request(BINDING, SHA, **args)

    def test_pause_resume_same_router_action_and_no_debugger_stop(self):
        self.assertEqual(CONTROL_ACTIONS, dict(stop='Stop', pause='Pause', resume='Pause', reset='Reset'))
        request = build_start_request(BINDING, SHA, 'net', ['SCK'])
        self.assertEqual(request['process'], 'PCB:Autoroute')
        self.assertFalse(request['runtime_qualified'])


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.adapter = Adapter()
        self.manager = SitusJobManager(self.temp.name, self.adapter)
        self.request = build_start_request(BINDING, SHA)

    async def asyncTearDown(self):
        self.adapter.release.set()
        if self.manager._tasks:
            await asyncio.gather(*list(self.manager._tasks), return_exceptions=True)
        self.temp.cleanup()

    async def start(self):
        job = await self.manager.start(self.request)
        await asyncio.sleep(0)
        return job

    async def test_async_start_status_no_dispatch_and_single_owner(self):
        job = await self.start()
        self.assertEqual(job['state'], 'start_pending')
        with self.assertRaisesRegex(ValueError, 'unresolved Situs job'):
            await self.manager.start(self.request)
        status = await self.manager.status(job['job_id'])
        self.assertEqual(status['state'], 'start_pending')  # Idle is not completion.
        self.assertTrue(status['no_new_dispatch'])
        self.assertEqual(len(self.adapter.dispatched), 1)
        self.adapter.state = 'running'
        status = await self.manager.status(job['job_id'])
        self.assertEqual(status['state'], 'running')

    async def test_terminal_requires_correlated_return_and_never_regresses(self):
        job = await self.start()
        self.adapter.state = 'completed'
        self.adapter.patch['router_returned'] = False
        self.assertIn('observation_error', await self.manager.status(job['job_id']))
        self.adapter.patch.clear()
        self.assertEqual((await self.manager.status(job['job_id']))['state'], 'completed')
        self.adapter.state = 'running'
        status = await self.manager.status(job['job_id'])
        self.assertEqual(status['state'], 'completed')
        self.assertIn('observation_error', status)

    async def test_control_never_replays_toggle_until_transition(self):
        job = await self.start()
        self.adapter.state = 'running'
        first = await self.manager.control(job['job_id'], 'pause')
        await asyncio.sleep(0)
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            await self.manager.control(job['job_id'], 'pause')
        self.adapter.state = 'paused'
        # Observing desired state alone is insufficient: match exact operation.
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            await self.manager.control(job['job_id'], 'resume')
        self.adapter.control_operation = first['operations'][-1]['id']
        resumed = await self.manager.control(job['job_id'], 'resume')
        self.assertEqual(resumed['operations'][-1]['parameters'], {'Action': 'Pause'})
        self.assertEqual(resumed['operations'][-1]['process'], 'PCB:Autoroute')

    async def test_stop_independent_channel_and_reset_confirmation(self):
        job = await self.start()
        self.adapter.state = 'running'
        self.adapter.supports_concurrent_control = False
        with self.assertRaisesRegex(ValueError, 'independently qualified'):
            await self.manager.control(job['job_id'], 'stop')
        self.adapter.supports_concurrent_control = True
        stopped = await self.manager.control(job['job_id'], 'stop')
        await asyncio.sleep(0)
        self.adapter.state = 'stopped'
        self.adapter.control_operation = stopped['operations'][-1]['id']
        with self.assertRaisesRegex(ValueError, 'confirm_reset'):
            await self.manager.control(job['job_id'], 'reset')
        reset = await self.manager.control(job['job_id'], 'reset', confirm_reset=True)
        self.assertEqual(reset['state'], 'reset_pending')

    async def test_timeout_and_restart_do_not_replay(self):
        self.adapter.failure = TimeoutError('native start uncertain')
        job = await self.start()
        await asyncio.sleep(0)
        status = await self.manager.status(job['job_id'])
        self.assertEqual(status['state'], 'outcome_unknown')
        restarted = SitusJobManager(self.temp.name, self.adapter)
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            await restarted.start(self.request)
        self.assertEqual(len(self.adapter.dispatched), 1)

    async def test_invalid_observation_never_dispatches(self):
        patches = [{'binding': dict(BINDING, creation_time=1)}, {'observed_at': 1},
                   {'modal_blocked': True}, {'global_rules_verified': False},
                   {'client_depth': 1}, {'saved_sha256': 'b'*64},
                   {'progress': {'routed': 2, 'total': 1}}]
        for patch in patches:
            self.adapter.patch = patch
            with self.assertRaises(ValueError):
                await self.manager.start(self.request)
        self.assertFalse(self.adapter.dispatched)

    async def test_status_rejects_different_job_even_same_pcb(self):
        job = await self.start()
        self.adapter.state = 'running'
        self.adapter.patch = {'job_id': 'b' * 32}
        status = await self.manager.status(job['job_id'])
        self.assertIn('not correlated', status['observation_error'])
        self.assertEqual(status['state'], 'start_pending')


if __name__ == '__main__':
    unittest.main()
