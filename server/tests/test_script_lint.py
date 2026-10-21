"""Offline lexical/API checks and dispatch gating; never launch Altium."""
import ast
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import script_lint

BAD = 'Board.GetState_DesignRuleCheckerOptions'

class LintTests(unittest.TestCase):
    def errors(self, script, declarations=''):
        return [x for x in script_lint.lint(script,declarations)['findings'] if x['severity']=='error']

    def test_actual_typed_api_failure_is_case_insensitive(self):
        errors = self.errors('Options := bOaRd.getstate_designrulecheckeroptions;', 'var Board, Other: iPcB_bOaRd;')
        self.assertEqual(len(errors),1)
        self.assertEqual(errors[0]['code'],'unsupported_delphiscript_board_drc_options')
        self.assertEqual(errors[0]['source'],'script')
        self.assertEqual(errors[0]['line'],1)
        self.assertTrue(errors[0]['evidence'])

    def test_helper_local_receiver_and_location(self):
        declarations = 'function Probe: String;\nvar Board: IPCB_Board;\nbegin\n  Result := '+BAD+';\nend;'
        error = self.errors('ResultText := Probe;',declarations)[0]
        self.assertEqual((error['source'],error['line'],error['column']),('declarations',4,19))

    def test_parameters_and_nested_scope(self):
        code = 'procedure Outer(Board: IPCB_Board); procedure Inner(X: String); begin Board.GetState_DesignRuleCheckerOptions; end; begin Inner(\'x\'); end;'
        self.assertEqual(len(self.errors('Outer(nil);',code)),1)

    def test_local_shadow_does_not_inherit_unrelated_receiver(self):
        code = 'var Board: IPCB_Board; procedure Check(Board: IDispatch); begin '+BAD+'; end;'
        self.assertEqual(self.errors('Check(nil);',code),[])

    def test_sibling_parameter_names_do_not_leak(self):
        code = 'procedure First(Board: IPCB_Board); begin end; procedure Second(Board: IDispatch); begin '+BAD+'; end;'
        self.assertEqual(self.errors('Second(nil);',code),[])

    def test_strings_and_all_comment_styles_are_opaque(self):
        code = "// " + BAD + "\n{ " + BAD + " } (* " + BAD + " *)\nS1 := 'It''s " + BAD + "';"
        self.assertEqual(self.errors(code,'var Board: IPCB_Board;'),[])
        self.assertEqual(script_lint.lint(code,'var Board: IPCB_Board;')['findings'],[])

    def test_comment_inside_real_member_access_does_not_hide_error(self):
        self.assertEqual(len(self.errors('Board {skip}. (*skip*) GetState_DesignRuleCheckerOptions;','var Board: IPCB_Board;')),1)

    def test_unknown_and_chained_receivers_warn_not_claim_compatibility(self):
        report = script_lint.lint('Other.GetState_DesignRuleCheckerOptions; Board.UnknownAPI; Parent.Board.GetState_DesignRuleCheckerOptions;', 'var Board: IPCB_Board; Other: IDispatch;')
        self.assertTrue(report['success'])
        self.assertEqual(report['status'],'no_known_blockers_found')
        self.assertTrue(report['findings'])
        self.assertTrue(all(x['severity']=='warning' for x in report['findings']))
        self.assertFalse(report['native_compilation_verified'])
        self.assertFalse(report['native_api_compatibility_verified'])

    def test_unknown_standalone_helper_is_warning(self):
        report = script_lint.lint("ResultText := MissingJsonHelper('x');")
        self.assertEqual(report['findings'][0]['code'],'unresolved_standalone_call')
        self.assertEqual(report['findings'][0]['severity'],'warning')

    def test_declared_helpers_are_not_reported_missing(self):
        report=script_lint.lint("ResultText := Helper('x');",'function Helper(S: String): String; begin Result := S; end;')
        self.assertNotIn('unresolved_standalone_call',[x['code']for x in report['findings']])

    def test_duplicate_variable_and_parameter_and_sandbox_collision(self):
        for declarations in ['var Board: IPCB_Board; BOARD: String;', 'procedure Check(A: String; a: Integer); begin end;', 'var S1: String;']:
            with self.subTest(declarations=declarations): self.assertTrue(self.errors('',declarations))

    def test_duplicate_names_in_separate_routines_are_valid(self):
        self.assertEqual(self.errors('','procedure A; var X: String; begin end; procedure B; var X: Integer; begin end;'),[])

    def test_unknown_type_alias_is_not_falsely_resolved(self):
        report=script_lint.lint(BAD+';','type Alias = IPCB_Board; var Board: Alias;')
        self.assertTrue(report['success'])
        self.assertIn('type_declaration_unresolved',[x['code'] for x in report['findings']])

    def test_with_scope_is_not_falsely_treated_as_global_variable(self):
        report=script_lint.lint('with Parent do '+BAD+';', 'var Board: IPCB_Board;')
        self.assertTrue(report['success'])
        self.assertTrue(report['findings'])

    def test_lexical_errors_rejected(self):
        for code in ["S1 := 'unterminated",'{ unterminated','(* unterminated']:
            self.assertEqual(self.errors(code)[0]['code'],'lexical_error')

    def test_file_inputs_bom_precedence_and_bad_encoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'declarations.pas';p.write_text('var Board: IPCB_Board;',encoding='utf-8-sig')
            declarations, report=script_lint.prepare(BAD+';',declarations_file=str(p))
            self.assertEqual(declarations,'var Board: IPCB_Board;');self.assertFalse(report['success'])
            _, report=script_lint.prepare(BAD+';','var Board: IDispatch;',str(p))
            self.assertTrue(report['success'])
            p.write_bytes(b'\xff\xfe\x00')
            self.assertEqual(script_lint.prepare('',declarations_file=str(p))[1]['status'],'input_error')
        self.assertFalse(script_lint.prepare('',declarations_file=str(p))[1]['success'])

class PublicTests(unittest.IsolatedAsyncioTestCase):
    def load(self,name,environment):
        node=next(n for n in ast.parse((ROOT/'main.py').read_text('utf-8')).body if isinstance(n,ast.AsyncFunctionDef)and n.name==name)
        node.decorator_list=[]
        exec(compile(ast.Module(body=[node],type_ignores=[]),'tool_under_test','exec'),environment)
        return environment[name]

    async def test_public_lint_has_no_native_dependency(self):
        tool=self.load('lint_altium_script',dict(Context=object,json=json))
        report=json.loads(await tool(None,BAD+';','var Board: IPCB_Board;',''))
        self.assertFalse(report['success']);self.assertTrue(report['no_new_dispatch'])

    async def test_mandatory_preflight_blocks_before_native_check_or_sandbox_write(self):
        native_check=Mock(side_effect=AssertionError('native inspection reached'))
        sandbox=Mock();launcher=Mock()
        env=dict(json=json,logger=Mock(),Path=Path,
                 script_guard=SimpleNamespace(blocking_crash_report=native_check),
                 SANDBOX_PAS=sandbox,subprocess=SimpleNamespace(Popen=launcher))
        tool=self.load('_run_altium_script_locked',env)
        result=json.loads(await tool(None,BAD+';',120,'var Board: IPCB_Board;',''))
        self.assertFalse(result['success']);self.assertEqual(result['mutation_state'],'not_dispatched')
        self.assertEqual(result['preflight']['status'],'rejected')
        native_check.assert_not_called();launcher.assert_not_called();self.assertEqual(sandbox.mock_calls,[])

    async def test_file_failure_also_blocks_before_native_access(self):
        tool=self.load('_run_altium_script_locked',dict(json=json,logger=Mock(),Path=Path))
        with tempfile.TemporaryDirectory() as tmp:
            result=json.loads(await tool(None,'',120,'',str(Path(tmp)/'missing.pas')))
        self.assertEqual(result['preflight']['status'],'input_error')
        self.assertTrue(result['no_new_dispatch'])

    async def test_accepted_input_dispatches_once_with_checked_file_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);pas=folder/'Sandbox.pas';prj=folder/'Sandbox.PrjScr'
            declarations=folder/'decl.pas';result_path=folder/'result.txt'
            pas.write_text('original');prj.write_text('project')
            declarations.write_text('var Board: IPCB_Board;')
            injected=[]
            def inject(source, begin, end, value, indent):
                injected.append(value)
                declarations.write_text('var S1: String;')
                return source
            def launch(*args,**kwargs):result_path.write_text('done')
            launcher=Mock(side_effect=launch)
            env=dict(json=json,logger=Mock(),Path=Path,
                script_guard=SimpleNamespace(blocking_crash_report=Mock(return_value=None)),
                altium_bridge=SimpleNamespace(config=SimpleNamespace(altium_exe_path='X2.EXE')),
                SANDBOX_PAS=pas,SANDBOX_PRJ=prj,SANDBOX_DIR=folder,SANDBOX_LOG=folder/'log',SANDBOX_RESULT=result_path,
                SANDBOX_DECL_BEGIN='d1',SANDBOX_DECL_END='d2',SANDBOX_BEGIN='b1',SANDBOX_END='b2',
                _inject_between=inject,subprocess=SimpleNamespace(Popen=launcher,DEVNULL=-3),
                time=SimpleNamespace(time=lambda:0),asyncio=asyncio)
            tool=self.load('_run_altium_script_locked',env)
            report=json.loads(await tool(None,'ResultText := Board.XOrigin;',120,'',str(declarations)))
            self.assertTrue(report['success']);self.assertEqual(launcher.call_count,1)
            self.assertEqual(injected[0],'var Board: IPCB_Board;')
            self.assertEqual(report['preflight']['status'],'no_known_blockers_found')
            self.assertTrue(report['preflight']['findings'])
            self.assertFalse(report['preflight']['native_api_compatibility_verified'])

if __name__=='__main__':unittest.main()
