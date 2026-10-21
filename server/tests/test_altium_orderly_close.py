"""Offline normal-exit protocol tests; every native call is mocked."""
import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import altium_orderly_close as close

PID=123
CREATED='134370344544789711'
EXE=r'C:\Altium\X2.EXE'
PCB=r'C:\boards\scratch.PcbDoc'
OP='a'*32

class CloseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.lock=asyncio.Lock();self.clock=0
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.identity=dict(pid=PID,creation_filetime=int(CREATED),exe=EXE,user_sid='sid',running=True)
        self.main=Mock();self.main.identity.return_value=self.identity
        self.main.__enter__=Mock(return_value=self.main);self.main.__exit__=Mock(return_value=False)
        self.main.wait.return_value={'wait_status':0}
        self.main.exit_observation.return_value=dict(pid=PID,creation_filetime=int(CREATED),exit_code=0)
        self.factory=self.stack.enter_context(patch.object(close.emergency_stop,'WinProcess',return_value=self.main))
        self.stack.enter_context(patch.object(close.emergency_stop,'_valid_identity'))
        self.ready=self.stack.enter_context(patch.object(close,'_ready',return_value={'hwnd':1}))
        self.stack.enter_context(patch.object(close.script_recovery,'_launches',{}))
        self.api=Mock();self.api.windows.return_value=[]
        self.stack.enter_context(patch.object(close.altium_focus,'WindowAPI',return_value=self.api))
        self.launcher=Mock(pid=456);self.launcher.poll.return_value=0
        self.launch=self.stack.enter_context(patch.object(close.script_recovery,'_launch',side_effect=self.launch_native))
        self.stack.enter_context(patch.object(close,'time',SimpleNamespace(monotonic=lambda:self.clock)))
        self.stack.enter_context(patch.object(close.asyncio,'sleep',side_effect=self.sleep))
        self.mode='clean'

    async def sleep(self,seconds):
        self.clock+=seconds
        if self.mode=='cancel':raise asyncio.CancelledError()

    def inventory(self,request_id):
        return dict(request_id=request_id,success=True,complete=True,current_pcb_preserved=True,
                    scope='loaded_server_document_containers',document_save_requested=False,
                    document_close_requested=False,pcb_path=PCB,error='',
                    modules=[dict(module='PCB',documents=[dict(kind='PCB',path=PCB,modified=False)])])

    async def read_inventory(self,request_id):
        self.assertTrue(self.lock.locked())
        value=self.inventory(request_id)
        if self.mode=='dirty':value['modules'][0]['documents'][0]['modified']=True
        if self.mode=='unknown':value['modules'][0]['documents'][0]['modified']=None
        if self.mode=='empty':value['modules']=[]
        if self.mode=='wrong_id':value['request_id']='b'*32
        if self.mode=='incomplete':value['complete']=False
        return value

    def launch_native(self,exe,argument):
        self.assertTrue(self.lock.locked())
        phase=close.script_recovery.blocked_state(self.root)['phase']
        if argument==close.QUIT_ARGUMENT:
            self.assertEqual(phase,'orderly_close_pending')
        else:
            self.assertEqual(phase,'orderly_close_inventory_pending')
            self.assertIn('OpenDocuments>RunGetOpenDocuments',argument)
            folder=self.root/'orderly_close'/OP
            source=(folder/'OpenDocuments.pas').read_text()
            import re
            request_id=re.search(r"InvQuote\('([0-9a-f]{32})'\)",source)[1]
            (folder/(request_id+'.json')).write_text(json.dumps(self.inventory(request_id)))
            self.assertEqual((folder/'OpenDocuments.PrjScr').read_text().splitlines()[0],'[Design]')
            self.assertNotIn('Sandbox',source)
        return self.launcher

    async def run_close(self,**changes):
        args=dict(pcb_path=PCB,target_pid=PID,expected_creation_filetime=CREATED,operation_id=OP,timeout_seconds=1)
        args.update(changes)
        callback=args.pop('callback',self.read_inventory)
        return await close.close(EXE,self.root,self.lock,callback,**args)

    async def test_clean_inventory_exact_exit_and_latches(self):
        result=await self.run_close()
        self.assertTrue(result['success']);self.assertTrue(result['exact_main_exited'])
        self.assertIsNone(close.script_recovery.blocked_state(self.root))
        self.launch.assert_called_once_with(EXE,close.QUIT_ARGUMENT)
        self.factory.assert_called_once_with(PID,synchronize_access=True)
        self.main.terminate.assert_not_called();self.launcher.kill.assert_not_called()

    async def test_default_backend_runs_dedicated_inventory_then_quit(self):
        result=await self.run_close(callback=None)
        self.assertTrue(result['success'],result)
        self.assertEqual(self.launch.call_count,2)
        self.assertTrue(result['inventory_dispatch_attempted'])

    async def test_dirty_unknown_incomplete_empty_wrong_id_never_close(self):
        for index,mode in enumerate(('dirty','unknown','incomplete','empty','wrong_id')):
            self.mode=mode
            result=await self.run_close(operation_id=hex(index+1)[2:].zfill(32))
            self.assertFalse(result['dispatch_attempted'],mode)
        self.launch.assert_not_called()

    async def test_process_change_after_inventory_blocks(self):
        self.main.identity.side_effect=[self.identity,dict(self.identity,creation_filetime=int(CREATED)+1)]
        result=await self.run_close();self.assertFalse(result['dispatch_attempted'])
        self.launch.assert_not_called()

    async def test_repeated_id_returns_history_no_replay(self):
        await self.run_close();result=await self.run_close()
        self.assertTrue(result['replayed']);self.assertFalse(result['current_state_rechecked'])
        self.launch.assert_called_once()

    async def test_changed_request_same_id_refused(self):
        await self.run_close();result=await self.run_close(pcb_path=r'C:\boards\other.PcbDoc')
        self.assertFalse(result['success']);self.launch.assert_called_once()

    async def test_timeout_keeps_latch_no_kill(self):
        self.main.wait.return_value={'wait_status':258}
        result=await self.run_close();self.assertTrue(result['outcome_unknown'])
        self.assertIsNotNone(close.script_recovery.blocked_state(self.root))
        self.main.terminate.assert_not_called();self.launcher.terminate.assert_not_called()

    async def test_dialog_return_unknown_and_no_prompt_input(self):
        self.main.wait.return_value={'wait_status':258}
        self.api.windows.return_value=[{'class':'#32770','title':'Save changes?'}]
        result=await self.run_close();self.assertTrue(result['outcome_unknown'])
        self.assertFalse(result['prompt_input_requested']);self.assertIn('dialogs',result)

    async def test_pending_launcher_after_main_exit_not_complete(self):
        self.launcher.poll.return_value=None
        result=await self.run_close();self.assertTrue(result['exact_main_exited'])
        self.assertFalse(result['success']);self.assertTrue(result['outcome_unknown'])

    async def test_wrong_exit_identity_fails_closed(self):
        self.main.exit_observation.return_value=dict(pid=PID,creation_filetime=1,exit_code=0)
        result=await self.run_close();self.assertTrue(result['outcome_unknown'])
        self.assertFalse(result['exact_main_exited'])

    async def test_launch_exception_retains_latch(self):
        self.launch.side_effect=OSError('failed')
        result=await self.run_close();self.assertTrue(result['outcome_unknown'])
        self.assertIsNotNone(close.script_recovery.blocked_state(self.root))

    async def test_cancel_retains_latch(self):
        self.mode='cancel';self.main.wait.return_value={'wait_status':258}
        with self.assertRaises(asyncio.CancelledError):await self.run_close()
        self.assertIsNotNone(close.script_recovery.blocked_state(self.root))
        saved=json.loads((self.root/'orderly_close'/OP/'result.json').read_text())
        self.assertTrue(saved['outcome_unknown'])

    async def test_recovery_latch_blocks_close(self):
        close.script_recovery._write_state(self.root,{'phase':'probe_pending'})
        result=await self.run_close();self.assertFalse(result['dispatch_attempted'])
        self.launch.assert_not_called()

    async def test_default_missing_inventory_keeps_latch_and_never_quits(self):
        self.launch.side_effect=None;self.launch.return_value=self.launcher
        result=await self.run_close(callback=None)
        self.assertFalse(result['dispatch_attempted']);self.assertTrue(result['outcome_unknown'])
        self.assertTrue(result['inventory_dispatch_attempted'])
        self.assertIsNotNone(close.script_recovery.blocked_state(self.root))
        self.launch.assert_called_once()
        self.assertNotEqual(self.launch.call_args.args[1],close.QUIT_ARGUMENT)

    async def test_default_inventory_preflight_failure_never_launches(self):
        with patch.object(close.script_lint,'lint',return_value={'success':False}):
            result=await self.run_close(callback=None)
        self.assertFalse(result['dispatch_attempted']);self.launch.assert_not_called()
        self.assertIsNone(close.script_recovery.blocked_state(self.root))

    async def test_default_inventory_cancellation_preserves_own_pending_latch(self):
        self.mode='cancel';self.launch.side_effect=None;self.launch.return_value=self.launcher
        with self.assertRaises(asyncio.CancelledError):await self.run_close(callback=None)
        saved=json.loads((self.root/'orderly_close'/OP/'result.json').read_text())
        self.assertFalse(saved['dispatch_attempted']);self.assertTrue(saved['outcome_unknown'])
        self.assertEqual(close.script_recovery.blocked_state(self.root)['phase'],'orderly_close_inventory_pending')

    async def test_concurrent_identical_requests_dispatch_once(self):
        results=await asyncio.gather(self.run_close(),self.run_close())
        self.assertTrue(all(r['success'] for r in results))
        self.assertEqual(sum(r['replayed'] for r in results),1)
        self.launch.assert_called_once()

    async def test_invalid_types_paths_ids_never_close(self):
        for change in ({'target_pid':True},{'expected_creation_filetime':int(CREATED)},
                       {'timeout_seconds':31},{'operation_id':'../x'},{'pcb_path':'scratch.PcbDoc'}):
            result=await self.run_close(**change);self.assertFalse(result['dispatch_attempted'])
        self.launch.assert_not_called()

class PublicCloseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import ast
        from mcp.server.fastmcp import FastMCP,Context
        self.before_main=sys.modules.get('main')
        tree=ast.parse((Path(__file__).resolve().parents[1]/'main.py').read_text(encoding='utf-8'))
        nodes=[n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='close_altium_normally']
        self.assertEqual(len(nodes),1)
        self.server=FastMCP('normal-close-offline')
        self.lock=asyncio.Lock();self.exchange=Path('C:/exchange')
        env=dict(mcp=self.server,Context=Context,json=json,EXCHANGE_DIR=self.exchange,
                 altium_bridge=SimpleNamespace(_command_lock=self.lock,config=SimpleNamespace(altium_exe_path=EXE)))
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'close_public','exec'),env)

    async def asyncTearDown(self):
        self.assertIs(sys.modules.get('main'),self.before_main)

    async def test_registered_schema_has_no_inventory_or_executable(self):
        tools=await self.server.list_tools()
        self.assertEqual(len(tools),1)
        self.assertEqual(set(tools[0].inputSchema['properties']),
                         {'pcb_path','target_pid','expected_creation_filetime','operation_id','timeout_seconds'})

    async def test_exact_filetime_token_reaches_internal_backend_as_string(self):
        from unittest.mock import AsyncMock
        worker=AsyncMock(return_value={'success':True,'exact_main_exited':True})
        with patch.object(close,'close',worker):
            result=await self.server.call_tool('close_altium_normally',dict(pcb_path=PCB,target_pid=PID,
                expected_creation_filetime='ft:'+CREATED,operation_id=OP))
        self.assertTrue(json.loads(result[0].text)['success'])
        worker.assert_awaited_once_with(EXE,self.exchange,self.lock,pcb_path=PCB,target_pid=PID,
            expected_creation_filetime=CREATED,operation_id=OP,timeout_seconds=15)

    async def test_unprefixed_filetime_is_not_coerced_or_dispatched(self):
        from unittest.mock import AsyncMock
        worker=AsyncMock()
        from mcp.server.fastmcp.exceptions import ToolError
        with patch.object(close,'close',worker):
            with self.assertRaises(ToolError):
                await self.server.call_tool('close_altium_normally',dict(pcb_path=PCB,target_pid=PID,
                    expected_creation_filetime=CREATED))
        worker.assert_not_awaited()

    async def test_bad_filetime_prefix_returns_refusal_without_backend(self):
        from unittest.mock import AsyncMock
        worker=AsyncMock()
        with patch.object(close,'close',worker):
            result=await self.server.call_tool('close_altium_normally',dict(pcb_path=PCB,target_pid=PID,
                expected_creation_filetime='invalid'))
        self.assertFalse(json.loads(result[0].text)['success'])
        worker.assert_not_awaited()

if __name__=='__main__':unittest.main()
