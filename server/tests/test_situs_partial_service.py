"""Offline selected-TP service checks; no Altium/process execution."""
import ast
import asyncio
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SERVER=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SERVER))
import situs_partial_service as service

PID=123
CREATED='134370000000000123'
EXE=r'C:\Altium\X2.EXE'
OP='a'*32
TP='TP1'
NET='SIGNAL'


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.lock=asyncio.Lock();self.clock=0
        self.qualification=self.root/'SitusQualification';self.qualification.mkdir()
        self.board=self.qualification/'candidate.PcbDoc';self.board.write_bytes(b'offline path fixture')
        self.pcb=str(self.board)
        self.mode='success';self.code=0;self.claims=0
        self.gate=asyncio.Event();self.sleep_entered=asyncio.Event()
        self.identity=dict(pid=PID,creation_filetime=int(CREATED),exe=EXE,user_sid='test',running=True)
        self.receipt=dict(success=True,stage='returned',route_attempted=True,return_verified=True,
                          outcome_unknown=False,retry_allowed=False,error='')
        self.process=Mock();self.process.__enter__=Mock(return_value=self.process)
        self.process.__exit__=Mock(return_value=False)
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.factory=self.stack.enter_context(patch.object(service.emergency_stop,'WinProcess',return_value=self.process))
        self.stack.enter_context(patch.object(service.emergency_stop,'emergency_claim',side_effect=self.claim))
        self.identify=self.stack.enter_context(patch.object(service,'_identity',side_effect=lambda *a:dict(self.identity)))
        self.environment=self.stack.enter_context(patch.object(service,'_environment',return_value={'ready':True,'foreground_required':False}))
        self.stack.enter_context(patch.object(service.script_recovery,'_launches',{}))
        self.launch=self.stack.enter_context(patch.object(service.script_recovery,'_launch',side_effect=self.launch_native))
        self.real_reader=service.native.read_partial_receipt
        self.reader=self.stack.enter_context(patch.object(service.native,'read_partial_receipt',side_effect=self.read_native))
        self.stack.enter_context(patch.object(service,'time',SimpleNamespace(monotonic=lambda:self.clock)))
        self.real_sleep=asyncio.sleep
        self.stack.enter_context(patch.object(service.asyncio,'sleep',side_effect=self.sleep))

    @contextmanager
    def claim(self,exchange):
        self.claims+=1
        try:yield
        finally:self.claims-=1

    async def sleep(self,seconds):
        self.clock+=seconds
        if self.mode=='cancel':
            self.sleep_entered.set();await self.gate.wait();self.mode='success'
        await self.real_sleep(0)

    def read_native(self,path,request):
        self.assertEqual(path,self.root/'situs_partial'/OP/'native.json')
        self.assertEqual(request['operation_id'],OP)
        self.assertEqual(request['pcb_path'],self.pcb)
        self.assertEqual(request['component_designator'],TP)
        self.assertEqual(request['expected_net'],NET)
        if self.mode in ('missing','cancel'):raise FileNotFoundError('No native receipt')
        if self.mode=='malformed':raise ValueError('Native correlation mismatch')
        return dict(self.receipt)

    def launch_native(self,exe,argument):
        self.assertTrue(self.lock.locked());self.assertEqual(self.claims,1)
        self.assertEqual(exe,EXE)
        self.assertEqual(service.script_recovery.blocked_state(self.root)['phase'],'situs_partial_pending')
        folder=self.root/'situs_partial'/OP
        self.assertEqual(argument,'-RScriptingSystem:RunScript(ProjectName="'+str(folder/'SelectedTPRoute.PrjScr')+'"|ProcName="SelectedTPRoute>RunSelectedTPRoute")')
        self.assertNotIn('Sandbox',argument)
        self.assertIn('DocumentPath=SelectedTPRoute.pas',(folder/'SelectedTPRoute.PrjScr').read_text('utf-8'))
        self.assertTrue((folder/'SelectedTPRoute.pas').exists())
        if self.mode=='launch_error':raise OSError('Launcher creation failed')
        launcher=Mock(pid=321);launcher.poll.side_effect=lambda:self.code
        return launcher

    async def call(self,**changes):
        args=dict(qualification_root=self.qualification,pcb_path=self.pcb,
                  component_designator=TP,expected_net=NET,target_pid=PID,
                  expected_creation_filetime=CREATED,operation_id=OP,timeout_seconds=1)
        args.update(changes)
        return await service.control(EXE,self.root,self.lock,**args)

    async def test_fixed_transport_return_is_not_drc_connectivity_or_save(self):
        result=await self.call()
        self.assertTrue(result['success'],result)
        self.assertTrue(result['routing_return_verified'])
        self.assertFalse(result['drc_completed'] or result['connectivity_verified'] or result['route_completeness_verified'] or result['board_saved'])
        self.assertFalse(result['pid_verified_by_native'])
        self.assertIsNone(service.script_recovery.blocked_state(self.root))
        self.assertEqual(self.identify.call_count,3)
        for call in self.identify.call_args_list:self.assertEqual(call.args,(self.process,EXE,PID,int(CREATED)))
        self.factory.assert_called_once_with(PID);self.launch.assert_called_once()
        self.assertFalse(self.lock.locked());self.assertEqual(self.claims,0)

    def publish_native(self, stage='returned', **changes):
        request=service.native.build_partial_request(self.pcb,OP,TP,NET)
        receipt=dict(request,**{key:True for key in service.native.BOOLS},
            stage=stage,error='',before_copper_records=['existing-track'],
            after_copper_records=['existing-track','new-track'],
            before_copper_count=1,after_copper_count=2,
            before_selected_components=['U1'],after_selected_components=['U1'])
        receipt.update(changes)
        folder=self.root/'situs_partial'/OP
        path=folder/('native.json.'+stage+'.json')
        path.write_text(json.dumps(receipt),'utf-8')
        self.reader.side_effect=self.real_reader

    async def test_real_parser_accepts_immutable_return_with_preserved_records(self):
        def launch(exe,argument):
            launcher=self.launch_native(exe,argument)
            self.publish_native()
            return launcher
        self.launch.side_effect=launch
        result=await self.call()
        self.assertTrue(result['success'],result)
        self.assertTrue(result['native_receipt']['old_copper_preserved'])
        self.assertFalse(result['native_receipt']['completion_verified'])
        self.assertFalse((self.root/'situs_partial'/OP/'native.json').exists())

    async def test_real_parser_rejects_false_copper_preservation_assertion(self):
        def launch(exe,argument):
            launcher=self.launch_native(exe,argument)
            self.publish_native(after_copper_records=['new-track'],after_copper_count=1)
            return launcher
        self.launch.side_effect=launch
        result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))

    async def test_real_parser_selection_mutation_without_route_remains_unknown(self):
        def launch(exe,argument):
            launcher=self.launch_native(exe,argument)
            self.publish_native(stage='native_exception',success=False,return_verified=False,
                route_attempted=False,selection_restored=False,error='selection_restore_failed',
                after_selected_components=[TP])
            return launcher
        self.launch.side_effect=launch
        result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])
        self.assertFalse(result['routing_attempted'])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))

    async def test_real_parser_progress_receipt_is_not_completion(self):
        def launch(exe,argument):
            launcher=self.launch_native(exe,argument)
            self.publish_native(stage='dispatch_attempted',success=False,return_verified=False,
                selection_restored=False,after_selected_components=[TP])
            return launcher
        self.launch.side_effect=launch
        result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])
        self.assertTrue(result['routing_attempted'])
        self.assertGreaterEqual(self.clock,1)

    async def test_same_operation_returns_history_without_new_native_observation(self):
        await self.call();count=self.identify.call_count
        result=await self.call()
        self.assertTrue(result['success'] and result['replayed'])
        self.assertFalse(result['current_state_rechecked'])
        self.assertEqual(self.identify.call_count,count);self.launch.assert_called_once()

    async def test_same_operation_with_different_tp_net_or_process_is_refused(self):
        await self.call()
        for changed in ({'component_designator':'TP2'},{'expected_net':'OTHER'},
                        {'target_pid':999},{'expected_creation_filetime':'134370000000000124'}):
            with self.subTest(changed=changed):
                result=await self.call(**changed)
                self.assertFalse(result['success']);self.assertIn('different request',result['error'])
        self.launch.assert_called_once()

    async def test_existing_request_without_final_result_never_replays(self):
        await self.call();(self.root/'situs_partial'/OP/'result.json').unlink()
        result=await self.call()
        self.assertTrue(result['outcome_unknown'] and result['replayed']);self.launch.assert_called_once()

    async def test_outside_scope_missing_or_traversal_candidates_never_launch(self):
        outside=self.root/'outside.PcbDoc';outside.write_bytes(b'outside')
        for pcb in (str(outside),str(self.qualification/'missing.PcbDoc'),
                    str(self.qualification/'..'/'outside.PcbDoc')):
            with self.subTest(pcb=pcb):
                result=await self.call(pcb_path=pcb)
                self.assertFalse(result['success']);self.assertFalse(result['dispatch_attempted'])
        self.identify.assert_not_called();self.launch.assert_not_called()

    async def test_unconfigured_or_relative_private_root_never_launches(self):
        for root in (None, '', '.', 'SitusQualification'):
            with self.subTest(root=root):
                result=await self.call(qualification_root=root)
                self.assertFalse(result['success'] or result['dispatch_attempted'])
        self.identify.assert_not_called();self.launch.assert_not_called()

    async def test_filesystem_reparse_candidate_is_refused_before_identity(self):
        with patch.object(service,'_reject_reparse',side_effect=ValueError('Reparse point')):
            result=await self.call()
        self.assertFalse(result['dispatch_attempted']);self.identify.assert_not_called();self.launch.assert_not_called()

    async def test_scope_is_rechecked_immediately_before_launch(self):
        original=service._candidate_scope
        calls=0
        def check(*args):
            nonlocal calls
            calls+=1
            if calls==2:raise ValueError('Candidate scope changed')
            return original(*args)
        with patch.object(service,'_candidate_scope',side_effect=check):result=await self.call()
        self.assertEqual(calls,2);self.assertFalse(result['dispatch_attempted']);self.launch.assert_not_called()

    async def test_invalid_types_filetime_timeout_and_native_builder_arguments(self):
        for changed in ({'target_pid':True},{'expected_creation_filetime':'ft:'+CREATED},
                        {'expected_creation_filetime':int(CREATED)}, {'expected_creation_filetime':'01'},
                        {'expected_creation_filetime':str(2**64)},
                        {'operation_id':'../x'},{'timeout_seconds':True},
                        {'component_designator':'U1'},{'expected_net':''}):
            with self.subTest(changed=changed):
                result=await self.call(**changed)
                self.assertFalse(result['success']);self.assertFalse(result['dispatch_attempted'])
        self.identify.assert_not_called();self.launch.assert_not_called()

    async def test_fixed_entry_and_lint_failures_never_launch(self):
        with patch.object(service.native,'build_partial_program',return_value='procedure Other; begin end;'):
            self.assertFalse((await self.call())['dispatch_attempted'])
        with patch.object(service.script_lint,'lint',return_value={'success':False}):
            self.assertFalse((await self.call())['dispatch_attempted'])
        self.identify.assert_not_called();self.launch.assert_not_called()

    async def test_preflight_identity_failure_never_creates_attempt(self):
        self.identify.side_effect=ValueError('Identity mismatch')
        result=await self.call()
        self.assertFalse(result['dispatch_attempted']);self.launch.assert_not_called()
        self.assertFalse((self.root/'situs_partial').exists())

    async def test_missing_receipt_and_launcher_zero_are_unknown_and_blocked(self):
        self.mode='missing';result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])
        self.assertEqual(result['launcher_exit_code'],0)
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))
        historical=await self.call();self.assertTrue(historical['replayed'] and historical['outcome_unknown'])
        fresh=await self.call(operation_id='b'*32);self.assertTrue(fresh['blocked'])
        self.launch.assert_called_once()

    async def test_malformed_receipt_never_completes(self):
        self.mode='malformed';result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))

    async def test_known_lock_or_target_guard_refusal_does_not_claim_routing(self):
        self.receipt.update(success=False,stage='guard_failed',route_attempted=False,
                            return_verified=False,outcome_unknown=False,error='lock_preroutes_required')
        result=await self.call()
        self.assertFalse(result['success'] or result['routing_return_verified'] or result['routing_attempted'])
        self.assertFalse(result['outcome_unknown']);self.assertIsNone(service.script_recovery.blocked_state(self.root))

    async def test_attempted_route_failed_readback_stays_unknown(self):
        self.receipt.update(success=False,stage='readback_failed',return_verified=False,outcome_unknown=True)
        result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])
        self.assertTrue(result['routing_attempted']);self.assertIsNotNone(service.script_recovery.blocked_state(self.root))

    async def test_nonterminal_dispatch_marker_never_means_return(self):
        self.receipt.update(success=False,stage='dispatch_attempted',return_verified=False,outcome_unknown=True)
        result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])
        self.assertGreaterEqual(self.clock,1)

    async def test_native_return_with_unfinished_launcher_stays_unknown(self):
        self.code=None;result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])
        self.assertFalse(result['routing_return_verified']);self.assertIsNotNone(service.script_recovery.blocked_state(self.root))

    async def test_process_identity_change_after_route_is_unknown(self):
        self.identify.side_effect=[dict(self.identity),dict(self.identity),dict(self.identity,creation_filetime=int(CREATED)+1)]
        result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])
        self.assertIn('Process identity changed',result['error'])

    async def test_readiness_change_after_route_is_unknown(self):
        self.environment.side_effect=[{'ready':True},{'ready':True},ValueError('New modal')]
        result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])

    async def test_launch_exception_preserves_attempt_latch(self):
        self.mode='launch_error';result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['dispatch_attempted'] and result['outcome_unknown'])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))

    async def test_cancel_keeps_locks_and_pinned_handle_until_bounded_observation(self):
        self.mode='cancel';task=asyncio.create_task(self.call())
        await asyncio.wait_for(self.sleep_entered.wait(),2)
        task.cancel();await self.real_sleep(0)
        self.assertTrue(self.lock.locked());self.assertEqual(self.claims,1)
        self.process.__exit__.assert_not_called();self.assertFalse(task.done())
        task.cancel();await self.real_sleep(0);self.assertTrue(self.lock.locked())
        self.gate.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(self.lock.locked());self.assertEqual(self.claims,0)
        self.process.__exit__.assert_called_once();self.launch.assert_called_once()

    async def test_shared_lock_covers_native_preflight_and_dispatch(self):
        await self.lock.acquire();task=asyncio.create_task(self.call());await self.real_sleep(0)
        self.identify.assert_not_called();self.launch.assert_not_called()
        self.lock.release();self.assertTrue((await task)['success'])

    async def test_final_receipt_failure_does_not_clear_pending_latch(self):
        original=service._write
        def fail(path,data):
            if path.name=='result.json' and data.get('service_completion_verified'):raise OSError('Result disk failure')
            return original(path,data)
        with patch.object(service,'_write',side_effect=fail):result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])
        self.assertFalse(result['service_completion_verified'] or result['routing_return_verified'] or result['current_state_rechecked'])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))

    async def test_recovery_persistence_failure_cannot_return_success(self):
        original=service.script_recovery._write_state
        def fail(exchange,state):
            if state['phase']=='recovered':raise OSError('Recovery disk failure')
            return original(exchange,state)
        with patch.object(service.script_recovery,'_write_state',side_effect=fail):result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))


    async def test_stale_success_receipt_cannot_override_its_pending_latch(self):
        original_state=service.script_recovery._write_state
        original_write=service._write
        def fail_state(exchange,state):
            if state['phase']=='recovered':raise OSError('Recovery disk failure')
            return original_state(exchange,state)
        def fail_corrected_result(path,data):
            if path.name=='result.json' and data.get('service_receipt_persisted') is False:
                raise OSError('Corrected result disk failure')
            return original_write(path,data)
        with patch.object(service.script_recovery,'_write_state',side_effect=fail_state), \
                patch.object(service,'_write',side_effect=fail_corrected_result):
            result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])
        stored=json.loads((self.root/'situs_partial'/OP/'result.json').read_text('utf-8'))
        self.assertTrue(stored['success'])
        historical=await self.call()
        self.assertTrue(historical['replayed'] and historical['outcome_unknown'])
        self.assertFalse(historical['success'] or historical['routing_return_verified'])
        self.launch.assert_called_once()



    async def failed_copper_return(self):
        def launch(exe,argument):
            launcher=self.launch_native(exe,argument)
            self.publish_native(stage='readback_failed',success=False,return_verified=False,
                old_copper_preserved=False,error='old_free_copper_changed_or_snapshot_incomplete',
                after_copper_records=['modified-track'],after_copper_count=1)
            return launcher
        self.launch.side_effect=launch
        result=await self.call()
        self.assertTrue(result['outcome_unknown'])
        self.assertFalse(result['success'])
        return self.root/'situs_partial'/OP

    async def reconcile(self,**changes):
        args=dict(qualification_root=self.qualification,operation_id=OP,target_pid=PID,
                  expected_creation_filetime=CREATED)
        args.update(changes)
        return await service.reconcile_known_return(EXE,self.root,self.lock,**args)

    async def test_reconcile_known_return_preserves_failed_route_and_original_evidence(self):
        folder=await self.failed_copper_return()
        saved={p.name:p.read_bytes() for p in folder.iterdir()}
        result=await self.reconcile()
        self.assertTrue(result['reconciled'] and result['executor_return_known'] and result['recovery_latch_cleared'],result)
        self.assertFalse(result['success'] or result['routing_success'] or result['routing_return_verified']
            or result['dispatch_attempted'] or result['copper_validation_passed']
            or result['native_idle_currently_verified'] or result['drc_completed'])
        self.assertTrue(result['diagnostics_required'])
        self.assertIsNone(service.script_recovery.blocked_state(self.root))
        self.launch.assert_called_once()
        for name,data in saved.items():self.assertEqual((folder/name).read_bytes(),data)
        proof=json.loads((folder/'known_return_reconciliation.json').read_text('utf-8'))
        self.assertTrue(proof['native_idle_verified_at_original_return'])
        self.assertFalse(proof['routing_success'] or proof['native_dispatch_performed'])
        repeat=await self.reconcile()
        self.assertFalse(repeat['reconciled'] or repeat['success'])
        self.launch.assert_called_once()

    async def test_reconcile_requires_exact_pending_token_phase_and_launcher(self):
        await self.failed_copper_return()
        state=service.script_recovery.blocked_state(self.root)
        variants=[dict(state,token='b'*32),dict(state,phase='native_situs_pending'),
                  dict(state,situs_partial_receipt='other'),dict(state,launches=[]),
                  dict(state,launches=[dict(state['launches'][0],exit_code=None)])]
        for changed in variants:
            with self.subTest(changed=changed):
                service.script_recovery._write_state(self.root,changed)
                result=await self.reconcile()
                self.assertFalse(result['reconciled'] or result['success'])
                self.assertIsNotNone(service.script_recovery.blocked_state(self.root))
        self.launch.assert_called_once()

    async def test_reconcile_rejects_identity_scope_and_source_changes(self):
        folder=await self.failed_copper_return()
        for args in ({'target_pid':PID+1},{'expected_creation_filetime':str(int(CREATED)+1)},
                     {'qualification_root':self.root},{'operation_id':'b'*32}):
            with self.subTest(args=args):
                result=await self.reconcile(**args)
                self.assertFalse(result['reconciled'])
        source=folder/'SelectedTPRoute.pas'
        source.write_text(source.read_text('utf-8')+'\n// source changed','utf-8')
        self.assertFalse((await self.reconcile())['reconciled'])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))
        self.launch.assert_called_once()

    async def test_reconcile_requires_original_outer_exit_and_identity_proof(self):
        folder=await self.failed_copper_return();path=folder/'result.json'
        original=json.loads(path.read_text('utf-8'))
        for change in ({'launcher_exit_code':None},{'launcher_exit_code':False},
                       {'process_identity_verified':False},{'success':True},{'source_sha256':'0'*64},
                       {'service_receipt_persisted':False},{'native_receipt':{}}):
            with self.subTest(change=change):
                path.write_text(json.dumps(dict(original,**change)),'utf-8')
                result=await self.reconcile()
                self.assertFalse(result['reconciled'])
                self.assertIsNotNone(service.script_recovery.blocked_state(self.root))
        self.launch.assert_called_once()

    async def test_reconcile_rejects_incomplete_native_return_or_other_failure(self):
        folder=await self.failed_copper_return();terminal=folder/'native.json.readback_failed.json'
        original=json.loads(terminal.read_text('utf-8'))
        for change in ({'after_snapshot_complete':False},{'selection_restored':False},
                       {'error':'exact_return_or_idle_failed'},{'route_attempted':False}):
            with self.subTest(change=change):
                terminal.write_text(json.dumps(dict(original,**change)),'utf-8')
                result=await self.reconcile()
                self.assertFalse(result['reconciled'])
                self.assertIsNotNone(service.script_recovery.blocked_state(self.root))
        self.launch.assert_called_once()

    async def test_reconcile_refuses_new_modal_or_process_identity_change(self):
        await self.failed_copper_return()
        self.environment.side_effect=ValueError('New modal')
        result=await self.reconcile();self.assertFalse(result['reconciled'])
        self.environment.side_effect=None
        self.identify.side_effect=[dict(self.identity),dict(self.identity,creation_filetime=int(CREATED)+1)]
        result=await self.reconcile();self.assertFalse(result['reconciled'])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))
        self.launch.assert_called_once()

    async def test_reconcile_never_clears_latch_without_durable_proof(self):
        await self.failed_copper_return()
        with patch.object(service,'_write',side_effect=OSError('Proof write failed')):
            result=await self.reconcile()
        self.assertFalse(result['reconciled'] or result['recovery_latch_cleared'])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))
        with patch.object(service.script_recovery,'_write_state',side_effect=OSError('State write failed')):
            result=await self.reconcile()
        self.assertFalse(result['reconciled'] or result['recovery_latch_cleared'])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))
        self.launch.assert_called_once()

    async def test_actual_reconcile_wrapper_preserves_failure_and_unlocks_only_diagnostics(self):
        await self.failed_copper_return()
        mcp,_=registered_wrapper(self.root,self.lock,'reconcile_situs_selected_tp')
        with patch.dict(os.environ,{'ALTIUM_SITUS_QUALIFICATION_ROOT':str(self.qualification)}):
            response=await mcp.call_tool('reconcile_situs_selected_tp',dict(
                operation_id=OP,target_pid=PID,expected_creation_filetime='ft:'+CREATED))
        result=json.loads(response[0].text)
        self.assertTrue(result['reconciled'],result)
        self.assertFalse(result['success'] or result['routing_success'] or result['dispatch_attempted'])
        self.assertIsNone(service.script_recovery.blocked_state(self.root))
        self.launch.assert_called_once()

    async def test_actual_public_wrapper_reaches_service_with_exact_filetime(self):
        mcp, unused_lock = registered_wrapper(self.root, self.lock)
        with patch.dict(os.environ, {'ALTIUM_SITUS_QUALIFICATION_ROOT': str(self.qualification)}):
            # Keep the real service, request builder and parser boundary. Only
            # OS/native execution is faked by this class's common fixture.
            function = await mcp.call_tool('route_situs_selected_tp', dict(
                pcb_path=self.pcb, component_designator=TP, expected_net=NET,
                target_pid=PID, expected_creation_filetime='ft:'+CREATED,
                operation_id=OP, timeout_seconds=1))
        result=json.loads(function[0].text)
        self.assertTrue(result['success'],result)
        self.assertEqual(result['creation_filetime_decimal'],CREATED)
        self.launch.assert_called_once()


def registered_wrapper(exchange='exchange', lock=None, name='route_situs_selected_tp'):
    """Compile only the real decorated wrapper into a real FastMCP registry."""
    from mcp.server.fastmcp import FastMCP, Context
    tree=ast.parse((SERVER/'main.py').read_text('utf-8'))
    nodes=[n for n in tree.body if isinstance(n,ast.AsyncFunctionDef)
           and n.name==name]
    if len(nodes)!=1:raise AssertionError('Exact public wrapper required')
    mcp=FastMCP('selected-tp-offline')
    if lock is None:lock=asyncio.Lock()
    bridge=SimpleNamespace(config=SimpleNamespace(altium_exe_path=EXE),_command_lock=lock)
    env=dict(Context=Context,json=json,os=os,mcp=mcp,altium_bridge=bridge,EXCHANGE_DIR=exchange)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'actual_selected_tp_wrapper','exec'),env)
    return mcp,lock


class PublicWrapperTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_reconcile_schema_and_exact_filetime_conversion(self):
        main_before=sys.modules.get('main')
        mcp,lock=registered_wrapper(name='reconcile_situs_selected_tp')
        tools={tool.name:tool for tool in await mcp.list_tools()}
        self.assertEqual(set(tools),{'reconcile_situs_selected_tp'})
        schema=tools['reconcile_situs_selected_tp'].inputSchema
        self.assertEqual(set(schema['properties']),{'operation_id','target_pid','expected_creation_filetime'})
        self.assertEqual(schema['properties']['expected_creation_filetime']['type'],'string')
        with patch.dict(os.environ,{'ALTIUM_SITUS_QUALIFICATION_ROOT':r'C:\qualified'}), \
                patch.object(service,'reconcile_known_return',return_value={'success':False,'reconciled':True}) as reconcile:
            response=await mcp.call_tool('reconcile_situs_selected_tp',dict(
                operation_id=OP,target_pid=PID,expected_creation_filetime='ft:'+CREATED))
        result=json.loads(response[0].text)
        self.assertFalse(result['success']);self.assertTrue(result['reconciled'])
        self.assertEqual(reconcile.call_args.args,(EXE,'exchange',lock))
        self.assertEqual(reconcile.call_args.kwargs,dict(qualification_root=r'C:\qualified',
            operation_id=OP,target_pid=PID,expected_creation_filetime=CREATED))
        with patch.object(service,'reconcile_known_return') as reconcile:
            response=await mcp.call_tool('reconcile_situs_selected_tp',dict(
                operation_id=OP,target_pid=PID,expected_creation_filetime='ft:01'))
        result=json.loads(response[0].text)
        self.assertFalse(result['success'] or result['reconciled'])
        reconcile.assert_not_called()
        self.assertIs(sys.modules.get('main'),main_before)

    async def test_actual_fastmcp_schema_and_private_configuration(self):
        main_before=sys.modules.get('main')
        mcp,lock=registered_wrapper()
        tools={tool.name:tool for tool in await mcp.list_tools()}
        self.assertEqual(set(tools),{'route_situs_selected_tp'})
        schema=tools['route_situs_selected_tp'].inputSchema
        self.assertEqual(set(schema['properties']),{'pcb_path','component_designator','expected_net',
            'target_pid','expected_creation_filetime','operation_id','timeout_seconds'})
        self.assertEqual(schema['properties']['expected_creation_filetime']['type'],'string')
        self.assertNotIn('qualification_root',schema['properties'])
        with patch.dict(os.environ,{'ALTIUM_SITUS_QUALIFICATION_ROOT':r'C:\qualified'}), \
                patch.object(service,'control',return_value={'success':True}) as control:
            result=await mcp.call_tool('route_situs_selected_tp',dict(
                pcb_path=r'C:\qualified\candidate.PcbDoc',component_designator=TP,expected_net=NET,
                target_pid=PID,expected_creation_filetime='ft:'+CREATED,operation_id=OP))
        self.assertTrue(json.loads(result[0].text)['success'])
        self.assertEqual(control.call_args.args,(EXE,'exchange',lock))
        self.assertEqual(control.call_args.kwargs['expected_creation_filetime'],CREATED)
        self.assertEqual(control.call_args.kwargs['qualification_root'],r'C:\qualified')
        self.assertEqual(control.call_args.kwargs['component_designator'],TP)
        self.assertEqual(control.call_args.kwargs['expected_net'],NET)
        self.assertIs(sys.modules.get('main'),main_before)

    async def test_public_filetime_prefix_is_required_before_service_dispatch(self):
        mcp,_=registered_wrapper()
        for created in ('134370000000000123','ft:01','ft:'+str(2**64),'ft:-1','ft:'):
            with self.subTest(created=created),patch.object(service,'control') as control:
                try:
                    response=await mcp.call_tool('route_situs_selected_tp',dict(
                        pcb_path=r'C:\qualified\candidate.PcbDoc',component_designator=TP,expected_net=NET,
                        target_pid=PID,expected_creation_filetime=created,operation_id=OP))
                except Exception as error:
                    # FastMCP can reject the unprefixed numeric string itself
                    # before reaching the wrapper; either route must not dispatch.
                    self.assertIn('validation',str(error).lower())
                else:
                    result=json.loads(response[0].text)
                    self.assertFalse(result['success'] or result['dispatch_attempted'])
                control.assert_not_called()


if __name__=='__main__':unittest.main()
