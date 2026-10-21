"""Offline late-probe tests. All process adapters and dispatches are mocked."""
import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

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
                if pid == 10:
                    return {'pid': 10, 'creation_filetime': owner.actual['creation_filetime'], 'exit_code': 259}
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
                patch.object(recovery, '_launches', {}), patch.object(recovery, '_launch') as launch, \
                patch.dict(sys.modules, {'situs_native_settings_service': SimpleNamespace(
                    _environment=lambda exe, pid: {'main_responsive': True, 'main_hwnd': 101,
                        'foreground_required': False, 'focus_or_input_requested': False})}):
            value = asyncio.run(recovery._recover_emergency_serialized(
                self.exe, str(self.root), manual_stop_confirmed=False))
            launch.assert_not_called()
            return value

    def blocked(self, result):
        self.assertFalse(result['recovered'], result)
        self.assertTrue(result['blocked'], result)
        self.assertEqual(json.loads(self.latch.read_text('utf-8')), self.old)

    def manual_pending(self):
        self.old.pop('fresh_instance_identity', None)
        self.old['manual_stop_confirmed'] = True
        self.old['environment'] = dict(json.loads(json.dumps(self.environment)),
            foreground_confirmed=False, foreground_required=False,
            focus_or_input_requested=False, native_idle_verified=False,
            manual_stop_background_identity=copy.deepcopy(self.main),
            manual_stop_background_readiness={'main_hwnd': 101, 'main_responsive': True,
                'foreground_required': False, 'focus_or_input_requested': False})
        self.save()

    def test_manual_background_pending_probe_reconciles_without_new_dispatch(self):
        self.manual_pending()
        self.environment.update(ready=False, foreground_confirmed=False)
        result = self.call()
        self.assertTrue(result['recovered'], result)
        self.assertTrue(result['no_new_dispatch'])
        self.assertFalse(result['native_transaction_idle_verified'])
        self.assertTrue(result['state']['manual_stop_confirmed'])
        self.assertTrue(result['state']['late_probe_completion']['background_readiness_reverified'])
        self.assertEqual(result['state']['late_probe_completion']['new_dispatches'], 0)

    def test_manual_pending_probe_also_reconciles_after_user_foregrounds_app(self):
        self.manual_pending()
        result = self.call()
        self.assertTrue(result['recovered'], result)
        self.assertFalse(result['state']['late_probe_completion']['background_readiness_reverified'])

    def test_manual_pending_wrong_live_process_is_rejected(self):
        self.manual_pending()
        for change in ({'creation_filetime': self.main['creation_filetime'] + 1},
                       {'pid': 12}, {'user_sid': 'other'},
                       {'exe': emergency_stop._canonical(r'C:\Other\X2.EXE')}):
            with self.subTest(change=change):
                self.actual = dict(self.main, **change)
                self.blocked(self.call())

    def test_manual_pending_requires_explicit_historical_manual_confirmation(self):
        self.manual_pending()
        self.old['manual_stop_confirmed'] = False
        self.save()
        self.blocked(self.call())

    def test_manual_pending_rejects_inconsistent_recorded_identity_proof(self):
        self.manual_pending()
        baseline = copy.deepcopy(self.old)
        for change in ({'target_pid': 12}, {'configured_process_ids': [10, 12]},
                       {'foreground_required': True}, {'focus_or_input_requested': True},
                       {'native_idle_verified': True}, {'dialog_handles': [102]},
                       {'manual_stop_background_readiness': {}},
                       {'manual_stop_background_identity': {'pid': 10}}):
            with self.subTest(change=change):
                self.old = copy.deepcopy(baseline)
                self.old['environment'].update(change)
                self.save()
                self.blocked(self.call())
        self.old = copy.deepcopy(baseline)
        self.old['fresh_instance_identity'] = dict(self.main, creation_filetime=1)
        self.save()
        self.blocked(self.call())

    def test_manual_pending_rejects_checkpoint_or_result_changed_during_readback(self):
        self.manual_pending()
        raw = self.latch.read_bytes()
        self.change = lambda: self.latch.write_bytes(raw + b' ')
        result = self.call()
        self.assertTrue(result['blocked'], result)
        self.assertEqual(self.latch.read_bytes(), raw + b' ')
        self.latch.write_bytes(raw)
        self.change = lambda: self.result.write_text(json.dumps(
            {'probe': 'executor ok', 'token': 'b' * 32}), encoding='utf-8')
        self.blocked(self.call())

    def test_manual_pending_background_still_rejects_modal_or_extra_process(self):
        self.manual_pending()
        baseline = copy.deepcopy(self.environment)
        for change in ({'dialog_handles': [102]}, {'script_errors': [{'kind': 'native_crash'}]},
                       {'configured_process_ids': [10, 12]}, {'unresolved_launch_ids': [11]}):
            with self.subTest(change=change):
                self.environment = dict(baseline, ready=False, foreground_confirmed=False, **change)
                self.blocked(self.call())

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
