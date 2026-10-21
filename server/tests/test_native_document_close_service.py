"""Offline fixed-document-close service tests; native/process observations mocked."""
import ast
import asyncio
from contextlib import ExitStack, contextmanager
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SERVER=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SERVER))
import native_document_close_service as service

PID=123
CREATED='134370000000000123'
EXE=r'C:\Altium\X2.EXE'
ACTIVE=r'C:\boards\active.PcbDoc'
TARGET=r'C:\boards\old.PcbDoc'
OP='a'*32


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve(); self.lock=asyncio.Lock(); self.clock=0
        self.mode='success'; self.code=0; self.claims=0
        self.gate=asyncio.Event(); self.sleep_entered=asyncio.Event()
        self.identity=dict(pid=PID,creation_filetime=int(CREATED),exe=EXE,user_sid='test',running=True)
        self.receipt=dict(success=True,stage='completed',close_attempted=True,
            completion_verified=True,closed_verified=True,current_pcb_preserved=True,
            other_documents_unchanged=True,outcome_unknown=False,retry_allowed=False,error='')
        self.process=Mock(); self.process.__enter__=Mock(return_value=self.process)
        self.process.__exit__=Mock(return_value=False)
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.factory=self.stack.enter_context(patch.object(service.emergency_stop,'WinProcess',return_value=self.process))
        self.stack.enter_context(patch.object(service.emergency_stop,'emergency_claim',side_effect=self.claim))
        self.identify=self.stack.enter_context(patch.object(service,'_identity',side_effect=lambda *a:dict(self.identity)))
        self.environment=self.stack.enter_context(patch.object(service,'_environment',return_value={'ready':True,'foreground_required':False}))
        self.stack.enter_context(patch.object(service.script_recovery,'_launches',{}))
        self.launch=self.stack.enter_context(patch.object(service.script_recovery,'_launch',side_effect=self.launch_native))
        self.real_reader=service.native.read_close_receipt
        self.reader=self.stack.enter_context(patch.object(service.native,'read_close_receipt',side_effect=self.read_native))
        self.stack.enter_context(patch.object(service,'time',SimpleNamespace(monotonic=lambda:self.clock)))
        self.real_sleep=asyncio.sleep
        self.stack.enter_context(patch.object(service.asyncio,'sleep',side_effect=self.sleep))

    @contextmanager
    def claim(self,exchange):
        self.claims+=1
        try: yield
        finally: self.claims-=1

    async def sleep(self,seconds):
        self.clock+=seconds
        if self.mode=='cancel':
            self.sleep_entered.set(); await self.gate.wait(); self.mode='success'
        await self.real_sleep(0)

    def read_native(self,path,request):
        # Parser is tested separately; this boundary supplies already-validated
        # native observations while checking the service passes the exact binding.
        self.assertEqual(path,self.root/'native_document_close'/OP/'native.json')
        self.assertEqual(request['operation_id'],OP)
        self.assertEqual(request['active_pcb_path'],ACTIVE)
        self.assertEqual(request['target_document_path'],TARGET)
        if self.mode in ('missing','cancel'): raise FileNotFoundError('No native receipt yet')
        if self.mode=='malformed': raise ValueError('Native receipt correlation mismatch')
        return dict(self.receipt)

    def launch_native(self,exe,argument):
        self.assertTrue(self.lock.locked()); self.assertEqual(self.claims,1)
        self.assertEqual(exe,EXE)
        self.assertEqual(service.script_recovery.blocked_state(self.root)['phase'],'native_document_close_pending')
        folder=self.root/'native_document_close'/OP
        self.assertEqual(argument,'-RScriptingSystem:RunScript(ProjectName="'+str(folder/'CleanDocumentClose.PrjScr')+'"|ProcName="CleanDocumentClose>RunCleanDocumentClose")')
        self.assertNotIn('Sandbox',argument)
        self.assertTrue((folder/'CleanDocumentClose.pas').exists())
        self.assertIn('DocumentPath=CleanDocumentClose.pas',(folder/'CleanDocumentClose.PrjScr').read_text('utf-8'))
        if self.mode=='launch_error':raise OSError('launcher creation failed')
        launcher=Mock(pid=321);launcher.poll.side_effect=lambda:self.code
        return launcher

    async def call(self,**changes):
        args=dict(active_pcb_path=ACTIVE,target_document_path=TARGET,target_pid=PID,
                  expected_creation_filetime='ft:'+CREATED,operation_id=OP,timeout_seconds=1)
        args.update(changes)
        return await service.control(EXE,self.root,self.lock,**args)

    async def test_fixed_standalone_success_and_exact_external_identity(self):
        result=await self.call()
        self.assertTrue(result['success'],result)
        self.assertTrue(result['closed_verified'] and result['other_documents_unchanged'])
        self.assertFalse(result['save_requested'] or result['prompt_input_requested'])
        self.assertFalse(result['pid_verified_by_native'])
        self.assertFalse(result['outcome_unknown'])
        self.assertIsNone(service.script_recovery.blocked_state(self.root))
        self.launch.assert_called_once(); self.factory.assert_called_once_with(PID)
        self.assertEqual(self.identify.call_count,3)
        for call in self.identify.call_args_list:
            self.assertEqual(call.args,(self.process,EXE,PID,int(CREATED)))
        self.assertFalse(self.lock.locked()); self.assertEqual(self.claims,0)

    def enable_actual_receipt_parser(self, mismatch=False):
        self.reader.side_effect=self.real_reader
        original=self.launch_native
        def launch(exe,argument):
            launcher=original(exe,argument)
            folder=self.root/'native_document_close'/OP
            request=json.loads((folder/'request.json').read_text('utf-8'))['request']
            raw=dict(request,stage='completed',error='',success=True,close_attempted=True,
                closed_verified=True,current_pcb_preserved=True,other_documents_unchanged=True,
                before_document_count=3,after_document_count=2,before_target_count=1,after_target_count=0,
                before_module_count=4,after_module_count=4)
            if mismatch:raw['operation_id']='b'*32
            (folder/'native.json').write_text(json.dumps(raw),'utf-8')
            return launcher
        self.launch.side_effect=launch

    async def test_real_parser_and_generated_fixed_source_integrate(self):
        self.enable_actual_receipt_parser()
        result=await self.call()
        self.assertTrue(result['success'],result)
        self.assertTrue(result['native_receipt']['completion_verified'])
        self.assertEqual(result['native_receipt']['after_document_count'],2)
        self.assertEqual(result['native_receipt']['operation_id'],OP)

    async def test_real_parser_wrong_nonce_is_not_completion(self):
        self.enable_actual_receipt_parser(mismatch=True)
        result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])
        self.assertIsNone(result['native_receipt'])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))

    async def test_real_parser_not_observed_sentinel_remains_bounded_unknown(self):
        self.reader.side_effect=self.real_reader
        result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])
        self.assertIsNone(result['native_receipt'])
        self.assertIn('completion',result['error'])
        self.assertGreaterEqual(self.clock,1)

    async def test_repeated_id_returns_history_without_launch_or_current_state_claim(self):
        await self.call(); count=self.identify.call_count
        result=await self.call()
        self.assertTrue(result['success'] and result['replayed'])
        self.assertFalse(result['current_state_rechecked'])
        self.assertEqual(self.identify.call_count,count); self.launch.assert_called_once()

    async def test_repeated_id_different_target_or_process_refused(self):
        await self.call()
        for changed in ({'target_document_path':r'C:\boards\different.PcbDoc'},
                        {'target_pid':999},{'expected_creation_filetime':'ft:134370000000000124'}):
            result=await self.call(**changed)
            self.assertFalse(result['success']);self.assertIn('different request',result['error'])
        self.launch.assert_called_once()

    async def test_invalid_public_values_never_observe_or_launch(self):
        for changed in ({'target_document_path':ACTIVE},{'target_document_path':'relative.PcbDoc'},
                        {'target_document_path':r'C:\boards\old.SchDoc'},
                        {'target_pid':True},{'expected_creation_filetime':CREATED},
                        {'operation_id':'../bad'},{'timeout_seconds':True}):
            with self.subTest(changed=changed):
                result=await self.call(**changed)
                self.assertFalse(result['success']);self.assertFalse(result['dispatch_attempted'])
        self.identify.assert_not_called();self.launch.assert_not_called()

    async def test_lint_failure_never_creates_request_or_launches(self):
        with patch.object(service.script_lint,'lint',return_value={'success':False}):
            result=await self.call()
        self.assertFalse(result['dispatch_attempted']);self.launch.assert_not_called()
        self.assertFalse((self.root/'native_document_close').exists())

    async def test_wrong_fixed_entry_point_never_launches(self):
        with patch.object(service.native,'build_close_program',return_value='procedure Other; begin end;'):
            result=await self.call()
        self.assertFalse(result['dispatch_attempted']);self.launch.assert_not_called()

    async def test_preflight_identity_refusal_never_launches(self):
        self.identify.side_effect=ValueError('Exact identity mismatch')
        result=await self.call()
        self.assertFalse(result['dispatch_attempted']);self.launch.assert_not_called()

    async def test_missing_receipt_launcher_zero_stays_unknown_and_blocks_fresh_attempt(self):
        self.mode='missing';result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])
        self.assertEqual(result['launcher_exit_code'],0)
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))
        repeat=await self.call();self.assertTrue(repeat['replayed'] and repeat['outcome_unknown'])
        fresh=await self.call(operation_id='b'*32);self.assertTrue(fresh['blocked'])
        self.launch.assert_called_once()

    async def test_invalid_correlated_receipt_never_completes(self):
        self.mode='malformed';result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))

    async def test_native_guard_refusal_returns_known_failure_without_close(self):
        self.receipt.update(success=False,stage='guard_failed',close_attempted=False,
            completion_verified=False,closed_verified=False,outcome_unknown=False,error='target_modified')
        result=await self.call()
        self.assertFalse(result['success']);self.assertFalse(result['document_close_attempted'])
        self.assertFalse(result['outcome_unknown']);self.assertIsNone(service.script_recovery.blocked_state(self.root))

    async def test_attempted_close_with_failed_readback_stays_unknown(self):
        self.receipt.update(success=False,stage='readback_failed',closed_verified=False,
                            other_documents_unchanged=False,outcome_unknown=True)
        result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])
        self.assertTrue(result['document_close_attempted'])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))

    async def test_nonterminal_attempt_receipt_is_not_completion(self):
        self.receipt.update(success=False,stage='before_close',closed_verified=False,outcome_unknown=True)
        result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])
        self.assertGreaterEqual(self.clock,1)

    async def test_native_success_requires_owned_launcher_completion(self):
        self.code=None;result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))

    async def test_process_change_after_close_stays_unknown(self):
        self.identify.side_effect=[dict(self.identity),dict(self.identity),dict(self.identity,creation_filetime=int(CREATED)+1)]
        result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])
        self.assertIn('Process identity changed',result['error'])

    async def test_readiness_change_after_close_stays_unknown(self):
        self.environment.side_effect=[{'ready':True},{'ready':True},ValueError('Unexpected dialog')]
        result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])

    async def test_launcher_exception_retains_prelaunch_pending_latch(self):
        self.mode='launch_error';result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['dispatch_attempted'] and result['outcome_unknown'])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))

    async def test_cancellation_holds_both_locks_and_handle_until_observation_finishes(self):
        self.mode='cancel';task=asyncio.create_task(self.call());await asyncio.wait_for(self.sleep_entered.wait(),2)
        task.cancel();await self.real_sleep(0)
        self.assertTrue(self.lock.locked());self.assertEqual(self.claims,1)
        self.process.__exit__.assert_not_called();self.assertFalse(task.done())
        task.cancel();await self.real_sleep(0);self.assertTrue(self.lock.locked())
        self.gate.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(self.lock.locked());self.assertEqual(self.claims,0)
        self.process.__exit__.assert_called_once();self.launch.assert_called_once()
        self.assertIsNone(service.script_recovery.blocked_state(self.root))

    async def test_shared_lock_covers_preflight_identity_and_dispatch(self):
        await self.lock.acquire();task=asyncio.create_task(self.call());await self.real_sleep(0)
        self.identify.assert_not_called();self.launch.assert_not_called()
        self.lock.release();self.assertTrue((await task)['success'])

    async def test_final_receipt_failure_never_clears_pending_latch(self):
        original=service._write
        def failing(path,data):
            if path.name=='result.json' and data.get('service_completion_verified'):
                raise OSError('Result disk failure')
            return original(path,data)
        with patch.object(service,'_write',side_effect=failing):result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))

    async def test_recovery_persistence_failure_never_returns_success(self):
        original=service.script_recovery._write_state
        def failing(exchange,state):
            if state['phase']=='recovered':raise OSError('Recovery disk failure')
            return original(exchange,state)
        with patch.object(service.script_recovery,'_write_state',side_effect=failing):result=await self.call()
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])
        self.assertIsNotNone(service.script_recovery.blocked_state(self.root))


class PublicWrapperTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_fastmcp_wrapper_routes_prefixed_filetime_and_shared_lock(self):
        from mcp.server.fastmcp import FastMCP,Context
        before=sys.modules.get('main')
        tree=ast.parse((SERVER/'main.py').read_text('utf-8'))
        nodes=[n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='close_clean_altium_document']
        self.assertEqual(len(nodes),1)
        mcp=FastMCP('clean-document-close-offline');lock=asyncio.Lock()
        env=dict(mcp=mcp,Context=Context,json=json,EXCHANGE_DIR='exchange',
            altium_bridge=SimpleNamespace(_command_lock=lock,config=SimpleNamespace(altium_exe_path=EXE)))
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'public_clean_document_close','exec'),env)
        registered=await mcp.list_tools();self.assertEqual(len(registered),1)
        self.assertEqual(set(registered[0].inputSchema['properties']),{'active_pcb_path','target_document_path','target_pid','expected_creation_filetime','operation_id','timeout_seconds'})
        with patch.object(service,'control',return_value={'success':True,'closed_verified':True}) as backend:
            result=await mcp.call_tool('close_clean_altium_document',dict(active_pcb_path=ACTIVE,
                target_document_path=TARGET,target_pid=PID,expected_creation_filetime='ft:'+CREATED,operation_id=OP))
        self.assertTrue(json.loads(result[0].text)['closed_verified'])
        self.assertEqual(backend.call_args.args,(EXE,'exchange',lock))
        self.assertEqual(backend.call_args.kwargs['expected_creation_filetime'],'ft:'+CREATED)
        self.assertIs(sys.modules.get('main'),before)


if __name__=='__main__':unittest.main()
