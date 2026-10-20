"""Offline late-probe tests. All process adapters and dispatches are mocked."""
import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import script_recovery as recovery
import emergency_stop


class LateProbeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.exe = r'C:\Altium\X2.EXE'
        self.token = '6c3bd806804049389a6f201a8b2986f5'
        self.main = {'pid': 10, 'creation_filetime': 134369500943014153,
            'exe': emergency_stop._canonical(self.exe), 'user_sid': 'test-user', 'running': True}
        self.actual = copy.deepcopy(self.main)
        self.mode, self.change = 'missing', None
        self.result = self.root / 'recovery' / self.token / 'result.json'
        self.result.parent.mkdir(parents=True)
        self.result.write_text(json.dumps({'probe': 'executor ok', 'token': self.token}), encoding='utf-8')
        self.latch = self.root / 'script_recovery_state.json'
        self.old = {'phase': 'probe_pending', 'token': self.token,
            'probe_result_path': str(self.result), 'fresh_instance_identity': copy.deepcopy(self.main),
            'manual_stop_confirmed': False, 'launches': [{'phase': 'probe', 'pid': 11, 'exit_code': None}]}
        self.save()
        self.environment = {'ready': True, 'target_pid': 10, 'configured_process_ids': [10],
            'foreground_confirmed': True, 'process_windows': {10: [{'class': 'TDocumentForm', 'hwnd': 101}]},
            'dialog_handles': [], 'script_errors': []}

    def save(self):
        self.latch.write_text(json.dumps(self.old), encoding='utf-8')

    @staticmethod
    def error(number):
        error = OSError('mock process query')
        error.winerror = number
        return error

    def factory(self, pid, *args, **kwargs):
        owner = self
        if pid == 11 and self.mode == 'missing':
            raise self.error(87)
        if pid == 10 and self.mode == 'main_missing':
            raise self.error(87)
        assert pid in (10, 11)
        class Handle:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def identity(self):
                assert pid == 10, 'Ended launcher must not query SID/path'
                return copy.deepcopy(owner.actual)
            def exit_observation(self):
                assert pid == 11
                if owner.mode == 'query_error_87':
                    raise owner.error(87)
                return {'pid': 11, 'creation_filetime': 134369500943014154,
                    'exit_code': 259 if owner.mode == 'live' else 0}
        return Handle()

    def inspect(self, *args, **kwargs):
        if self.change:
            self.change()
        return copy.deepcopy(self.environment)

    def call(self):
        with patch.object(emergency_stop, 'WinProcess', self.factory), \
                patch.object(emergency_stop, '_current_sid', return_value='test-user'), \
                patch.object(recovery, 'inspect_environment', self.inspect), \
                patch.object(recovery, '_launches', {}), patch.object(recovery, '_launch') as launch:
            value = asyncio.run(recovery._recover_emergency_serialized(
                self.exe, str(self.root), manual_stop_confirmed=False))
            launch.assert_not_called()
            return value

    def blocked(self, result):
        self.assertFalse(result['recovered'], result)
        self.assertTrue(result['blocked'], result)
        self.assertEqual(json.loads(self.latch.read_text('utf-8')), self.old)

    def test_success_without_new_dispatch_or_manual_claim(self):
        result = self.call()
        self.assertTrue(result['recovered'], result)
        self.assertFalse(result['blocked'], result)
        self.assertTrue(result['no_new_dispatch'], result)
        saved = json.loads(self.latch.read_text('utf-8'))
        self.assertEqual(saved['phase'], 'recovered')
        self.assertFalse(saved['manual_stop_confirmed'])
        self.assertEqual(saved['late_probe_completion']['new_dispatches'], 0)
        self.assertFalse(saved['late_probe_completion']['main_restarted_by_reconciliation'])

    def test_result_token_mismatch(self):
        self.result.write_text(json.dumps({'probe': 'executor ok', 'token': 'a' * 32}), encoding='utf-8')
        self.blocked(self.call())

    def test_live_probe(self):
        self.mode = 'live'
        self.blocked(self.call())

    def test_changed_main_creation(self):
        self.actual['creation_filetime'] += 1
        self.blocked(self.call())

    def test_changed_main_sid_or_executable(self):
        for key, value in [('user_sid', 'other'), ('exe', emergency_stop._canonical(r'C:\Other\X2.EXE'))]:
            with self.subTest(key=key):
                self.actual = copy.deepcopy(self.main)
                self.actual[key] = value
                self.blocked(self.call())

    def test_changed_result_during_revalidation(self):
        self.change = lambda: self.result.write_text(json.dumps(
            {'probe': 'executor ok', 'token': 'b' * 32}), encoding='utf-8')
        self.blocked(self.call())

    def test_substituted_result_path(self):
        other = self.root / 'other.json'
        other.write_bytes(self.result.read_bytes())
        self.old['probe_result_path'] = str(other)
        self.save()
        self.blocked(self.call())

    def test_error_87_during_exit_query_is_not_process_absence(self):
        self.mode = 'query_error_87'
        self.blocked(self.call())

    def test_closed_old_main_cannot_certify_new_session(self):
        self.mode = 'main_missing'
        self.environment.update(target_pid=12, configured_process_ids=[12])
        self.blocked(self.call())

    def test_ended_handle_success_without_token_query(self):
        self.mode = 'exited'
        self.assertTrue(self.call()['recovered'])


if __name__ == '__main__':
    unittest.main()
