"""Guarded public DRC contract checks; no Altium process, bridge or native call."""
import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import script_lint
PCB=r'C:\Boards\Controller.PcbDoc'

class PublicDrcTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree=ast.parse((ROOT/'main.py').read_text(encoding='utf-8'))
        nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in {'_script_result','run_pcb_drc'}]
        public=next(n for n in nodes if n.name=='run_pcb_drc')
        self.assertEqual(ast.unparse(public.decorator_list[0]),'mcp.tool()')
        self.assertEqual([a.arg for a in public.args.args],['ctx','pcb_path','run','timeout_s'])
        for n in nodes:n.decorator_list=[]
        self.bridge=SimpleNamespace(execute_command=AsyncMock(return_value={'success':True,'result':{'success':True,'ran':True}}))
        self.env=dict(json=json,Context=object,logger=Mock(),altium_bridge=self.bridge)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'isolated_public_drc','exec'),self.env)
    async def test_valid_dispatch_uses_unique_report_id_and_no_dialog_driver(self):
        r=json.loads(await self.env['run_pcb_drc'](None,PCB))
        name,args=self.bridge.execute_command.call_args.args
        self.assertEqual(name,'run_pcb_drc');self.assertEqual(args['pcb_path'],PCB);self.assertEqual(args['run'],'true')
        self.assertRegex(args['report_id'],r'^[0-9a-f]{32}$');self.assertEqual(self.bridge.execute_command.call_args.kwargs,{'timeout':630})
        self.assertFalse(r['dialog_driver_used']);self.assertEqual(r['request_report_id'],args['report_id'])
        first=args['report_id'];await self.env['run_pcb_drc'](None,PCB)
        self.assertNotEqual(first,self.bridge.execute_command.call_args.args[1]['report_id'])
    async def test_list_only_dispatch_does_not_request_run(self):
        await self.env['run_pcb_drc'](None,PCB,False,42)
        self.assertEqual(self.bridge.execute_command.call_args.args[1]['run'],'false')
        self.assertEqual(self.bridge.execute_command.call_args.kwargs,{'timeout':72})
    async def test_bad_paths_refuse_without_dispatch(self):
        for path in ['Controller.PcbDoc',r'C:Controller.PcbDoc',r'C:\Boards\Library.PcbLib',PCB+'\nOther',None,5]:
            with self.subTest(path=path):
                r=json.loads(await self.env['run_pcb_drc'](None,path));self.assertFalse(r['success']);self.assertFalse(r['native_dispatched'])
        self.bridge.execute_command.assert_not_awaited()
    async def test_invalid_control_types_and_timeouts_refuse_without_dispatch(self):
        for args in [('true',600),(1,600),(True,0),(True,3601),(True,False),(True,2.5)]:
            with self.subTest(args=args):self.assertFalse(json.loads(await self.env['run_pcb_drc'](None,PCB,*args))['success'])
        self.bridge.execute_command.assert_not_awaited()
    async def test_bridge_failure_preserved_without_retry(self):
        self.bridge.execute_command.return_value={'success':False,'error':'unknown result after timeout'}
        r=json.loads(await self.env['run_pcb_drc'](None,PCB));self.assertFalse(r['success']);self.assertIn('timeout',r['error'])
        self.bridge.execute_command.assert_awaited_once()

class NativeContractTests(unittest.TestCase):
    def setUp(self):
        source=(ROOT/'AltiumScript/pcb_document.pas').read_text(encoding='utf-8')
        self.body=source.split('function RunPcbDrcReport(',1)[1].split('// The design rule of the board with this name, or nil.',1)[0]
        self.call='Board.RunBatchDesignRuleCheck(ReportPath, 1, False, False);'
        self.pre,self.post=self.body.split(self.call)
    def test_exact_current_board_and_zero_depth_guards_precede_run(self):
        for token in ['FileExists(PcbPath)','Client.CurrentView = nil','Doc := Client.CurrentView.OwnerDocument',
                      'SameText(Doc.FileName, PcbPath)',"SameText(Doc.Kind, 'PCB')",'PCBServer.GetPCBBoardByPath(PcbPath)',
                      'PCBServer.GetCurrentPCBBoard','Board.I_ObjectAddress <> CurrentBoard.I_ObjectAddress',
                      'SameText(Board.FileName, PcbPath)',"Client.ServerModuleByName('PCB')",'ClientBefore <> 0','PcbBefore <> 0']:
            with self.subTest(token=token):self.assertIn(token,self.pre)
    def test_no_open_save_delete_ui_or_unsupported_drc_options(self):
        for forbidden in ['OpenBoardAtPath','OpenDocument','ShowDocument','SaveBoardDocument','DoFileSave','DeleteFile',
                          'RunProcess','GetState_DesignRuleCheckerOptions','SetState_MaxViolationCount']:
            with self.subTest(forbidden=forbidden):self.assertNotIn(forbidden,self.body)
        wrapper=ast.parse((ROOT/'main.py').read_text(encoding='utf-8'))
        public=next(n for n in wrapper.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='run_pcb_drc')
        self.assertNotIn('DialogDriver',ast.unparse(public));self.assertNotIn('dialogs.',ast.unparse(public))
    def test_fresh_report_required_before_listing(self):
        self.assertIn('if FileExists(ReportPath) then',self.pre)
        self.assertIn('Length(ReportId) <> 32',self.pre);self.assertIn("'0123456789abcdef'",self.pre)
        self.assertLess(self.post.index('if not FileExists(ReportPath) then'),self.post.index('Ran := True'))
        self.assertLess(self.post.index('Ran := True'),self.post.index('Iter := Board.BoardIterator_Create'))
        self.assertIn('if Requested then',self.pre)
    def test_after_run_path_identity_and_depth_guards_precede_collection(self):
        guards=self.post.split('Iter := Board.BoardIterator_Create')[0]
        for token in ['SameText(Doc.FileName, PcbPath)',"SameText(Doc.Kind, 'PCB')",'Board.I_ObjectAddress <> CurrentBoard.I_ObjectAddress',
                      'ClientAfter <> 0','PcbAfter <> 0']:
            self.assertIn(token,guards)
        self.assertGreaterEqual(guards.count('Board.I_ObjectAddress <> CurrentBoard.I_ObjectAddress'),2)
    def test_collector_is_uncapped_and_does_not_claim_native_completeness(self):
        self.assertNotIn('Kept',self.body);self.assertNotIn('< 500',self.body)
        loop=self.body.split('while V <> nil do',1)[1].split('finally',1)[0]
        self.assertIn('Items.Add(',loop);self.assertIn('V := Iter.NextPCBObject',loop)
        for token in ['"collector_truncated":false','"collector_limit":null','"native_violation_limit":null',
                      '"native_limit_verified":false','"native_check_complete":null','"full_drc_verified":false',
                      '"cached_violation_snapshot":','"path_before":','"path_after":','"client_depth_before":',
                      '"pcb_depth_before":','"client_depth_after":','"pcb_depth_after":']:
            self.assertIn(token,self.body)
        self.assertIn('finally\n            Board.BoardIterator_Destroy(Iter)',self.body)
    def test_dispatcher_receives_unique_report_argument_not_fixed_stale_path(self):
        text=(ROOT/'AltiumScript/Altium_API.pas').read_text(encoding='utf-8')
        dispatch=text.split("'run_pcb_drc':",1)[1].split("'set_pcb_rules':",1)[0]
        self.assertIn("ROOT_DIR + 'drc_report_' + ParamValueOf('report_id') + '.txt', ParamValueOf('report_id')",dispatch)
        self.assertNotIn("ROOT_DIR + 'drc_report.txt'",dispatch)
    def test_linter_has_no_known_blockers_but_is_not_a_native_compiler(self):
        declarations='function RunPcbDrcReport('+self.body
        result=script_lint.lint("ResultText := RunPcbDrcReport('C:\\Boards\\Controller.PcbDoc', 'false', 'C:\\Reports\\unique.txt', '0123456789abcdef0123456789abcdef');",declarations)
        self.assertTrue(result['success'],result)
        self.assertFalse(result['native_compilation_verified']);self.assertFalse(result['native_api_compatibility_verified'])

if __name__=='__main__':unittest.main()
