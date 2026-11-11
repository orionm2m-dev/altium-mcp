"""Offline native-background recovery guards; no real application calls."""
from unittest.mock import Mock
from server.tests.test_script_recovery import RecoveryTests
import script_recovery as recovery

class NativeBackgroundRecoveryTests(RecoveryTests):
    def prepare_initial_background(self):
        self.install_manual_background()
        recovery._write_state(self.root, {'phase':'recovered'})

    def test_native_background_stop_probe_and_exact_identity_rechecks(self):
        self.prepare_initial_background()
        result=self.run_recovery()
        self.assertTrue(result['recovered'])
        self.assertFalse(result['state']['manual_stop_confirmed'])
        self.assertFalse(result['native_transaction_idle_verified'])
        self.assertEqual([z['phase'] for z in result['state']['launches']],['stop','probe'])
        self.assertEqual(self.background_readiness.call_count,3)
        self.assertEqual(self.background_process.identity.call_count,6)
        self.assertFalse(result['state']['environment']['focus_or_input_requested'])
        self.assertEqual(result['state']['process_identity'],self.background_identity)
        self.assert_no_termination()

    def test_native_background_modal_never_dispatches(self):
        self.prepare_initial_background()
        self.environment['dialog_handles']=[123]
        self.assertTrue(self.run_recovery()['blocked'])
        self.launcher.assert_not_called()
        self.background_factory.assert_not_called()

    def test_native_background_identity_changed_after_stop_never_probes(self):
        self.prepare_initial_background()
        original=dict(self.background_identity)
        self.background_process.identity.side_effect=[original,original,dict(original,creation_filetime=133777777777777778)]
        result=self.run_recovery()
        self.assertTrue(result['blocked'])
        self.assertEqual(self.launcher.call_count,1)
        self.assertIn('identity changed',result['error'])
        self.assertEqual(result['state']['phase'],'failed_unresolved')

    def test_native_background_new_dialog_after_stop_never_probes(self):
        self.prepare_initial_background()
        self.background_readiness.side_effect=[dict(self.background_readiness.return_value),ValueError('modal appeared')]
        result=self.run_recovery()
        self.assertTrue(result['blocked'])
        self.assertEqual(self.launcher.call_count,1)
        self.assertIn('modal appeared',result['error'])

    def test_native_background_readiness_failure_never_stops(self):
        self.prepare_initial_background()
        self.background_readiness.return_value['focus_or_input_requested']=True
        self.assertTrue(self.run_recovery()['blocked'])
        self.launcher.assert_not_called()

    def test_native_background_identity_changed_after_probe_is_not_success(self):
        self.prepare_initial_background()
        original=dict(self.background_identity)
        self.background_process.identity.side_effect=[original]*4+[dict(original,creation_filetime=133777777777777778)]
        result=self.run_recovery()
        self.assertTrue(result['blocked'])
        self.assertEqual(self.launcher.call_count,2)
        self.assertIn('identity changed',result['error'])

    def test_native_background_pending_launcher_is_not_bypassed(self):
        self.prepare_initial_background()
        pending=Mock();pending.poll.return_value=None
        recovery._launches[99]=pending
        self.assertTrue(self.run_recovery()['blocked'])
        self.launcher.assert_not_called()
        self.background_factory.assert_not_called()

    def prepare_late_native_stop(self):
        self.prepare_initial_background()
        original=dict(self.background_identity)
        old={'phase':'stop_pending','token':'f'*32,'manual_stop_confirmed':False,
             'fresh_instance_confirmed':False,'process_identity':original,
             'environment':dict(self.environment,native_stop_identity=original),
             'launches':[{'phase':'stop','pid':99,'exit_code':None}]}
        recovery._write_state(self.root,old)
        return old

    def test_late_native_stop_permits_one_probe_without_second_stop(self):
        from unittest.mock import patch
        self.prepare_late_native_stop()
        with patch.object(recovery,'_native_stop_launcher_exit',return_value={
                'source':'pid_absent_at_open','exit_code':None,'exited':True}):
            result=self.run_recovery()
        self.assertTrue(result['recovered'])
        self.assertEqual([z['phase'] for z in result['state']['launches']],['probe'])
        self.assertFalse(result['state']['manual_stop_confirmed'])
        self.assertFalse(result['state']['verified_prior_stop_exit']['stop_delivery_verified'])
        self.assertEqual(self.launcher.call_count,1)
        self.assertNotIn('EditScript:Stop',self.launcher.call_args.args[0])

    def test_late_native_stop_live_launcher_blocks_every_launch(self):
        from unittest.mock import patch
        old=self.prepare_late_native_stop()
        with patch.object(recovery,'_native_stop_launcher_exit',side_effect=ValueError('live launcher')):
            self.assertTrue(self.run_recovery()['blocked'])
        self.launcher.assert_not_called()
        self.assertEqual(recovery.blocked_state(self.root),old)

    def test_late_native_stop_changed_main_blocks_probe(self):
        from unittest.mock import patch
        old=self.prepare_late_native_stop()
        self.background_identity['creation_filetime']+=1
        with patch.object(recovery,'_native_stop_launcher_exit',return_value={'exited':True,'exit_code':None}):
            self.assertTrue(self.run_recovery()['blocked'])
        self.launcher.assert_not_called()
        self.assertEqual(recovery.blocked_state(self.root),old)

    def test_late_native_stop_unknown_access_is_not_absence(self):
        from unittest.mock import patch
        import emergency_stop
        denied=OSError('denied');denied.winerror=5
        with patch.object(emergency_stop,'WinProcess',side_effect=denied):
            with self.assertRaises(OSError):recovery._native_stop_launcher_exit(99)

    def test_late_native_stop_absence_retains_unknown_exit_status(self):
        from unittest.mock import patch
        import emergency_stop
        absent=OSError('absent');absent.winerror=87
        with patch.object(emergency_stop,'WinProcess',side_effect=absent):
            result=recovery._native_stop_launcher_exit(99)
        self.assertTrue(result['exited']);self.assertIsNone(result['exit_code'])
        self.assertFalse(result['historical_creation_verified'])
