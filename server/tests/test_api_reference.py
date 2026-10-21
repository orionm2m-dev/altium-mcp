"""Offline API lookup and citation-only lint integration."""
import ast
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import api_reference
import script_lint

class ReferenceTests(unittest.TestCase):
    def test_exact_member_case_insensitive(self):
        report=api_reference.search('ipcb_board.filename')
        self.assertTrue(report['success'])
        match=report['matches'][0]
        self.assertEqual((match['interface'],match['member']),('IPCB_Board','FileName'))
        self.assertTrue(match['signature']);self.assertTrue(match['source_url'].startswith('https://www.altium.com/'))
        self.assertEqual(match['status'],'DOCUMENTED');self.assertFalse(match['runtime_verified'])
        self.assertTrue(report['no_new_dispatch']);self.assertFalse(report['documentation_is_runtime_allowlist'])

    def test_source_title_search(self):
        report=api_reference.search('System API',50)
        self.assertTrue(report['matches'])
        self.assertTrue(any(m['source_title']=='System API'for m in report['matches']))

    def test_signature_search(self):
        report=api_reference.search('TCoordRect')
        self.assertTrue(report['matches'])
        self.assertTrue(all('TCoordRect' in m['signature']for m in report['matches']))

    def test_limit_and_total_count(self):
        report=api_reference.search('IPCB',2)
        self.assertEqual(report['returned'],2);self.assertGreater(report['total_matches'],2)
        self.assertTrue(report['truncated'])

    def test_unknown_is_not_incompatible(self):
        report=api_reference.search('NonexistentMember998')
        self.assertTrue(report['success']);self.assertEqual(report['matches'],[])
        self.assertEqual(report['absence_means'],'UNKNOWN')
        self.assertFalse(report['runtime_verified'])

    def test_observed_failure_survives_documentation_absence(self):
        report=api_reference.search('IPCB_Board.GetState_DesignRuleCheckerOptions')
        self.assertTrue(report['success']);self.assertEqual(report['matches'],[])
        self.assertEqual(report['observed_incompatibilities'][0]['receiver_type'],'IPCB_Board')
        self.assertTrue(report['observed_incompatibilities'][0]['evidence'])

    def test_invalid_input(self):
        for query,limit in [('',10),('   ',10),('.',10),('x'*257,10),('Board',0),('Board',51),('Board',True),('Board',1.5)]:
            with self.subTest(query=query,limit=limit):self.assertFalse(api_reference.search(query,limit)['success'])

    def test_missing_and_invalid_reference_fail_clearly(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(api_reference,'REFERENCE_DIR',Path(tmp)):
            report=api_reference.search('Board')
            self.assertFalse(report['success']);self.assertIn('unavailable',report['error'])
            self.assertTrue(report['no_new_dispatch'])
            (Path(tmp)/'catalog.json').write_text('{invalid')
            self.assertFalse(api_reference.search('Board')['success'])

    def test_invalid_catalog_shape_fails_clearly(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(api_reference,'REFERENCE_DIR',Path(tmp)):
            (Path(tmp)/'catalog.json').write_text(json.dumps({'entries':['invalid']}))
            (Path(tmp)/'sources.json').write_text(json.dumps({'sources':[]}))
            report=api_reference.search('Board')
            self.assertFalse(report['success']);self.assertIn('JSON objects',report['error'])

    def test_lint_documentation_does_not_promote_warning(self):
        report=script_lint.lint('ResultText := Board.FileName;', 'var Board: IPCB_Board;')
        finding=report['findings'][0]
        self.assertEqual(finding['code'],'unverified_member');self.assertEqual(finding['severity'],'warning')
        self.assertEqual(finding['documentation'][0]['member'],'FileName')
        self.assertFalse(report['native_api_compatibility_verified'])
        self.assertFalse(report['documentation']['changes_lint_verdict'])

    def test_documented_match_cannot_override_negative_rule(self):
        entry={'interface':'IPCB_Board','member':'GetState_DesignRuleCheckerOptions','status':'DOCUMENTED','runtime_verified':False}
        with patch.object(api_reference,'_load',return_value=([entry],[], 'fixture', 'hash')):
            report=script_lint.lint('Board.GetState_DesignRuleCheckerOptions;', 'var Board: IPCB_Board;')
        self.assertFalse(report['success']);self.assertEqual(report['findings'][0]['severity'],'error')
        self.assertTrue(report['findings'][0]['documentation'])

    def test_missing_optional_docs_never_disables_negative_lint(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(api_reference,'REFERENCE_DIR',Path(tmp)):
            report=script_lint.lint('Board.GetState_DesignRuleCheckerOptions;', 'var Board: IPCB_Board;')
        self.assertFalse(report['success']);self.assertFalse(report['documentation']['available'])

class PublicTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_tool_without_native_dependencies(self):
        tree=ast.parse((ROOT/'main.py').read_text('utf-8'))
        node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef)and n.name=='search_altium_api')
        self.assertEqual([arg.arg for arg in node.args.args],['ctx','query','limit'])
        node.decorator_list=[];env=dict(Context=object,json=json)
        exec(compile(ast.Module(body=[node],type_ignores=[]),'search_tool','exec'),env)
        report=json.loads(await env['search_altium_api'](None,'BoardIterator_Create',1))
        self.assertTrue(report['success']);self.assertEqual(report['returned'],1)
        self.assertEqual(report['matches'][0]['member'],'BoardIterator_Create')
        self.assertTrue(report['no_new_dispatch'])

    async def test_real_mcp_registration_and_call_remain_offline(self):
        from mcp.server.fastmcp import FastMCP, Context
        tree=ast.parse((ROOT/'main.py').read_text('utf-8'))
        node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef)and n.name=='search_altium_api')
        mcp=FastMCP('api-reference-test');env=dict(Context=Context,json=json,mcp=mcp)
        exec(compile(ast.Module(body=[node],type_ignores=[]),'registered_search_tool','exec'),env)
        tools=await mcp.list_tools()
        self.assertEqual(tools[0].name,'search_altium_api')
        self.assertEqual(tools[0].inputSchema['properties']['query']['type'],'string')
        self.assertEqual(tools[0].inputSchema['properties']['limit']['type'],'integer')
        content=await mcp.call_tool('search_altium_api',{'query':'IPCB_Board.FileName','limit':1})
        report=json.loads(content[0].text)
        self.assertEqual(report['returned'],1);self.assertTrue(report['no_new_dispatch'])
        self.assertFalse(report['matches'][0]['runtime_verified'])

if __name__=='__main__':unittest.main()
