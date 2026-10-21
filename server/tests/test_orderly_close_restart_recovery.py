"""Recorded ordinary exit followed by public normal start permits a probe only."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import script_recovery as recovery
import emergency_stop
import altium_orderly_close

class OrderlyRestartTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.exe=r'C:\Altium\X2.EXE';self.pcb=r'C:\boards\scratch.PcbDoc'
        self.token='a'*32;self.probe='b'*32;self.inventory_id='c'*32
        self.previous=dict(pid=10,creation_filetime=1000,exe=emergency_stop._canonical(self.exe),user_sid='owner',running=True)
        self.identity=dict(self.previous,pid=12,creation_filetime=2000)
        self.current=copy.deepcopy(self.identity);self.old_exited=True;self.launcher_alive=False;self.change=None;self.after_launch=None
        self.folder=self.root/'orderly_close'/self.token;self.folder.mkdir(parents=True)
        self.old=dict(phase='orderly_close_pending',token=self.token,process_identity=self.previous,
            launches=[dict(phase='ordinary_quit',pid=11,exit_code=None)],close_receipt=str(self.folder/'result.json'))
        self.request=dict(exe=self.exe,target_pid=10,creation_filetime='1000',pcb_path=self.pcb,operation_id=self.token)
        self.inventory=dict(request_id=self.inventory_id,success=True,complete=True,current_pcb_preserved=True,
            scope='loaded_server_document_containers',document_save_requested=False,document_close_requested=False,
            pcb_path=self.pcb,error='',modules=[dict(module='PCB',documents=[dict(kind='PCB',path=self.pcb,modified=False)])])
        self.result=dict(self.request,dispatch_attempted=True,outcome_unknown=True,force_termination_requested=False,
            save_requested=False,prompt_input_requested=False,retry_allowed=False,replayed=False,
            launcher_exit_code=0,inventory_request_id=self.inventory_id,
            inventory_sha256=hashlib.sha256(json.dumps(self.inventory).encode()).hexdigest())
        self.startup=dict(success=True,launched=True,script_dispatched=False,mutation_replayed=False,
            status='process_started_ui_unverified',exit_code=None,configured_exe=self.exe,target_pid=12,
            identity=self.identity,creation_filetime_decimal='2000')
        self.env=dict(ready=True,target_pid=12,configured_process_ids=[12],foreground_confirmed=True,
            process_windows={12:[dict(hwnd=100,class_name='TDocumentForm')]},dialog_handles=[],script_errors=[])
        self.env['process_windows'][12][0]={'class':'TDocumentForm','hwnd':100}
        self.save()

    def save(self):
        for path,value in ((self.root/'script_recovery_state.json',self.old),(self.folder/'request.json',self.request),
            (self.folder/'result.json',self.result),(self.folder/'inventory.json',self.inventory),
            (self.root/'altium_startup.json',self.startup)):
            path.write_text(json.dumps(value),encoding='utf-8')

    def factory(self,pid,*args,**kwargs):
        if pid==11:
            if not self.launcher_alive:
                error=OSError('absent');error.winerror=87;raise error
            observed=Mock();observed.__enter__=Mock(return_value=observed);observed.__exit__=Mock(return_value=False)
            observed.exit_observation.return_value=dict(pid=11,creation_filetime=1500,exit_code=259)
            return observed
        self.assertEqual(pid,12)
        observed=Mock();observed.__enter__=Mock(return_value=observed);observed.__exit__=Mock(return_value=False)
        observed.identity.side_effect=lambda:copy.deepcopy(self.current)
        return observed

    def environment(self,*args):
        if self.change:self.change()
        rows = self.env['process_windows'][12]
        if not self.env['ready'] or len(rows) != 1 or rows[0].get('class') != 'TDocumentForm':
            raise ValueError('Native main not ready')
        return copy.deepcopy(rows[0])

    def launch(self,exe,argument):
        self.assertNotIn('QuitFromEDAClient',argument);self.assertNotIn('EditScript:Stop',argument)
        result=self.root/'recovery'/self.probe/'result.json'
        result.write_text(json.dumps(dict(probe='executor ok',token=self.probe)),encoding='utf-8')
        if self.after_launch:self.after_launch()
        return Mock(pid=13,poll=Mock(return_value=0))

    def call(self,launches=0):
        with patch.object(emergency_stop,'WinProcess',self.factory), \
             patch.object(emergency_stop,'_current_sid',return_value='owner'), \
             patch.object(emergency_stop,'_exact_target_has_exited',return_value=self.old_exited), \
             patch.object(recovery,'inspect_environment',side_effect=AssertionError('Foreground preflight must not run')), \
             patch.object(altium_orderly_close,'_ready',self.environment), \
             patch.object(recovery,'_launches',{}), \
             patch.object(recovery,'uuid',SimpleNamespace(uuid4=lambda:SimpleNamespace(hex=self.probe))), \
             patch.object(recovery,'_wait_exit',AsyncMock(return_value=0)), \
             patch.object(recovery,'_launch',side_effect=self.launch) as launch:
            result=asyncio.run(recovery._recover_emergency_serialized(self.exe,self.root,timeout_seconds=1,dispatch_timeout_seconds=1))
            self.assertEqual(launch.call_count,launches,result)
            return result

    def blocked(self,result):
        self.assertTrue(result['blocked'],result);self.assertFalse(result['recovered'])
        self.assertEqual(json.loads((self.root/'script_recovery_state.json').read_text()),self.old)

    def test_new_normal_start_only_dispatches_unique_readonly_probe(self):
        result=self.call(launches=1);self.assertTrue(result['recovered'],result)
        state=result['state'];self.assertFalse(state['manual_stop_confirmed'])
        self.assertEqual([x['phase'] for x in state['launches']],['probe'])
        evidence=state['verified_normal_restart']
        self.assertIsNone(evidence['previous_main_historical_exit_code'])
        self.assertFalse(evidence['previous_close_retroactively_completed'])
        self.assertFalse(evidence['quit_replayed']);self.assertFalse(evidence['stop_dispatched'])
        self.assertTrue(state['restart_after_probe']['exact_restart_identity_verified'])

    def test_background_and_minimized_main_can_run_readonly_probe(self):
        self.env['foreground_confirmed']=False
        self.env['process_windows'][12][0]['minimized']=True
        result=self.call(launches=1)
        self.assertTrue(result['recovered'],result)
        self.assertFalse(result['state']['environment']['foreground_required'])

    def test_identity_change_after_probe_keeps_latch(self):
        self.after_launch=lambda:self.current.update(creation_filetime=3000)
        result=self.call(launches=1)
        self.assertTrue(result['blocked'],result)
        self.assertEqual(result['state']['phase'],'failed_unresolved')
        self.assertNotEqual(json.loads((self.root/'script_recovery_state.json').read_text())['phase'],'recovered')

    def test_new_modal_after_probe_keeps_latch(self):
        self.after_launch=lambda:self.env.update(ready=False)
        result=self.call(launches=1)
        self.assertTrue(result['blocked'],result)
        self.assertEqual(result['state']['phase'],'failed_unresolved')

    def test_previous_exact_main_still_alive_blocks(self):
        self.old_exited=False;self.blocked(self.call())

    def test_old_quit_launcher_still_alive_blocks(self):
        self.launcher_alive=True;self.blocked(self.call())

    def test_modified_inventory_even_matching_hash_blocks(self):
        self.inventory['modules'][0]['documents'][0]['modified']=True
        self.result['inventory_sha256']=hashlib.sha256(json.dumps(self.inventory).encode()).hexdigest()
        self.save();self.blocked(self.call())

    def test_inventory_hash_change_blocks(self):
        self.inventory['error']='changed';self.save();self.blocked(self.call())

    def test_close_result_identity_mismatch_blocks(self):
        self.result['target_pid']=99;self.save();self.blocked(self.call())

    def test_quit_dispatch_not_recorded_blocks(self):
        self.result['dispatch_attempted']=False;self.save();self.blocked(self.call())

    def test_startup_must_be_newer_than_prior_process(self):
        self.startup['identity']['creation_filetime']=1000;self.startup['creation_filetime_decimal']='1000'
        self.save();self.blocked(self.call())

    def test_live_identity_must_match_startup_receipt(self):
        self.current['creation_filetime']=3000;self.blocked(self.call())

    def test_startup_window_or_modal_does_not_authorize_probe(self):
        self.env['process_windows'][12]=[{'class':'TStartUpWindow'}];self.blocked(self.call())

    def test_changed_evidence_during_verification_blocks(self):
        self.change=lambda:(self.root/'altium_startup.json').write_text('{}')
        self.blocked(self.call())

    def test_foreign_receipt_path_blocks(self):
        self.old['close_receipt']=str(self.root/'other.json');self.save();self.blocked(self.call())

if __name__=='__main__':unittest.main()
