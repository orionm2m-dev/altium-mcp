"""A verified new normal start requires its own probe; old success is never reused."""
import asyncio
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import script_recovery as recovery
import emergency_stop
from server.tests import test_late_probe_reconciliation as fixtures


class FreshNormalStartTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.LateProbeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.mode = 'main_missing'
        self.main = dict(self.fixture.main, pid=12,
                         creation_filetime=self.fixture.main['creation_filetime'] + 1000)
        self.current = copy.deepcopy(self.main)
        self.old_alive = False
        self.change = None
        self.startup_path = self.fixture.root / 'altium_startup.json'
        self.startup = {'success': True, 'launched': True, 'script_dispatched': False,
            'mutation_replayed': False, 'status': 'process_started_ui_unverified',
            'exit_code': None, 'configured_exe': self.fixture.exe, 'target_pid': 12,
            'identity': self.main, 'creation_filetime_decimal': str(self.main['creation_filetime'])}
        self.save_startup()
        self.environment = {'ready': True, 'target_pid': 12, 'configured_process_ids': [12],
            'foreground_confirmed': True, 'process_windows': {12: [{'class': 'TDocumentForm', 'hwnd': 102}]},
            'dialog_handles': [], 'script_errors': []}

    def save_startup(self):
        self.startup_path.write_text(json.dumps(self.startup), encoding='utf-8')

    def factory(self, pid, *args, **kwargs):
        owner = self
        if pid == 11:
            return self.fixture.factory(pid)
        if pid == 10 and not self.old_alive:
            raise self.fixture.error(87)
        assert pid in (10, 12)
        class Handle:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def identity(self):
                return copy.deepcopy(owner.fixture.main if pid == 10 else owner.current)
            def exit_observation(self):
                assert pid == 10
                return {'pid': 10, 'creation_filetime': owner.fixture.main['creation_filetime'], 'exit_code': 259}
        return Handle()

    def inspect(self, *args, **kwargs):
        if self.change:
            self.change()
        return copy.deepcopy(self.environment)

    def launch(self, exe, argument):
        self.assertNotIn('EditScript:Stop', argument)
        result = self.fixture.root / 'recovery' / ('b' * 32) / 'result.json'
        result.write_text(json.dumps({'probe': 'executor ok', 'token': 'b' * 32}), encoding='utf-8')
        return Mock(pid=13, poll=Mock(return_value=0))

    def call(self, expected_launches=0):
        with patch.object(emergency_stop, 'WinProcess', self.factory), \
                patch.object(emergency_stop, '_current_sid', return_value='test-user'), \
                patch.object(recovery, 'inspect_environment', self.inspect), \
                patch.object(recovery, '_launches', {}), \
                patch.object(recovery, 'uuid', SimpleNamespace(uuid4=lambda: SimpleNamespace(hex='b' * 32))), \
                patch.object(recovery, '_wait_exit', AsyncMock(return_value=0)), \
                patch.object(recovery, '_launch', side_effect=self.launch) as launch:
            result = asyncio.run(recovery._recover_emergency_serialized(self.fixture.exe,
                str(self.fixture.root), timeout_seconds=1, dispatch_timeout_seconds=1))
            self.assertEqual(launch.call_count, expected_launches, result)
            return result

    def blocked(self, result):
        self.assertTrue(result['blocked'], result)
        self.assertFalse(result['recovered'], result)
        self.assertEqual(json.loads(self.fixture.latch.read_text('utf-8')), self.fixture.old)

    def test_new_session_requires_one_new_unique_probe_without_stop(self):
        result = self.call(expected_launches=1)
        self.assertTrue(result['recovered'], result)
        state = result['state']
        self.assertEqual(state['token'], 'b' * 32)
        self.assertFalse(state['manual_stop_confirmed'])
        self.assertEqual(state['fresh_instance_identity'], self.main)
        self.assertFalse(state['verified_normal_restart']['previous_probe_certifies_current_main'])
        self.assertEqual([item['phase'] for item in state['launches']], ['probe'])

    def test_still_live_old_main_blocks(self):
        self.old_alive = True
        self.blocked(self.call())

    def test_startup_window_blocks(self):
        self.environment['process_windows'][12] = [{'class': 'TStartUpWindow', 'hwnd': 102}]
        self.blocked(self.call())

    def test_import_wizard_blocks(self):
        self.environment['process_windows'][12].append({'class': 'TPCBImportWizard', 'hwnd': 103})
        self.blocked(self.call())

    def test_new_main_identity_must_match_receipt(self):
        self.current['creation_filetime'] += 1
        self.blocked(self.call())

    def test_start_receipt_creation_must_be_newer(self):
        self.startup['identity']['creation_filetime'] = self.fixture.main['creation_filetime']
        self.startup['creation_filetime_decimal'] = str(self.fixture.main['creation_filetime'])
        self.save_startup()
        self.blocked(self.call())

    def test_live_old_probe_blocks(self):
        self.fixture.mode = 'live'
        self.blocked(self.call())

    def test_changed_startup_receipt_blocks(self):
        self.change = lambda: self.startup_path.write_text('{}', encoding='utf-8')
        self.blocked(self.call())

    def test_old_unique_token_mismatch_blocks(self):
        self.fixture.result.write_text(json.dumps({'probe': 'executor ok', 'token': 'c' * 32}), encoding='utf-8')
        self.blocked(self.call())


if __name__ == '__main__':
    unittest.main()
