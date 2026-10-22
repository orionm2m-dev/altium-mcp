"""Polygon connection enum validation and native mutation boundaries (offline)."""
import ast
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

ROOT=Path(__file__).resolve().parents[1]
PCB=r'C:\Boards\GroundQualification.PcbDoc'

def load_wrapper(directory):
    tree=ast.parse((ROOT/'main.py').read_text('utf-8'))
    wanted={'_spec_number','_spec_field','_rule_values_spec','_script_result','set_pcb_rules'}
    nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in wanted]
    for node in nodes: node.decorator_list=[]
    class Logger:
        def info(self,*args): pass
    class Bridge:
        execute_command=AsyncMock(return_value={'success':True,'result':{'success':True,'saved':True}})
    env={'json':json,'Context':object,'logger':Logger(),'PCB_RULES_SPEC':Path(directory)/'rules.txt',
         'SPEC_ENCODING':'utf-8','altium_bridge':Bridge()}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'wrapper','exec'),env)
    return env

def rule(**changes):
    r={'name':'GND_Via_Direct','kind':'PolygonConnect','scope1':"IsVia And InNet('GND')",'scope2':'All',
       'priority':1,'enabled':True,'values':{'connect_style':'direct'}}
    r.update(changes)
    return r

class PolygonConnectTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.env=load_wrapper(self.temp.name)
    def call(self,rules,path=PCB):
        return json.loads(asyncio.run(self.env['set_pcb_rules'](None,path,rules)))
    def test_three_documented_styles_serialize_as_enum_not_mm(self):
        for style in ('direct','relief','none'):
            self.assertEqual(self.env['_rule_values_spec']('PolygonConnect',{'connect_style':style}),'connect_style='+style)
    def test_generic_thermal_rule_only_priority_changed(self):
        self.assertTrue(self.call([dict(name='PolygonConnect',kind='PolygonConnect',priority=2),rule()])['success'])
        lines=self.env['PCB_RULES_SPEC'].read_text().splitlines()
        self.assertEqual(lines[1],'RULE|PolygonConnect|PolygonConnect|||2|||')
        self.assertEqual(lines[2],"RULE|GND_Via_Direct|PolygonConnect|IsVia And InNet('GND')|All|1|true||connect_style=direct")
    def test_unknown_thermal_dimensions_not_silently_ignored(self):
        for values in ({'connect_style':'direct','air_gap':.254},{'relief_width':.254},{'connect_style':1},
                       {'connect_style':'Direct'},{'connect_style':True},{'connect_style':'direct;gap=0'}):
            with self.subTest(values=values):
                self.assertFalse(self.call([rule(values=values)])['success'])
        self.env['altium_bridge'].execute_command.assert_not_awaited()
    def test_style_requires_explicit_polygon_kind(self):
        self.assertFalse(self.call([rule(kind='Clearance')])['success'])
        self.env['altium_bridge'].execute_command.assert_not_awaited()
    def test_invalid_polygon_metadata_never_dispatches(self):
        for change in (dict(name='x|RULE'),dict(scope1='All\nDELETE|Width'),dict(priority=True),dict(priority=0),
                       dict(enabled='true'),dict(comment='x\x00')):
            with self.subTest(change=change):self.assertFalse(self.call([rule(**change)])['success'])
        self.env['altium_bridge'].execute_command.assert_not_awaited()
    def test_mixed_duplicate_delete_batches_rejected(self):
        for rules in ([rule(),dict(name='Other',kind='Width',values={'min':.15})],
                      [rule(),rule(name='gnd_via_direct')],[rule(delete=True)]):
            self.assertFalse(self.call(rules)['success'])
        self.env['altium_bridge'].execute_command.assert_not_awaited()
    def test_exact_absolute_path_required_for_polygon_batch(self):
        for path in ('x.PcbDoc',r'C:x.PcbDoc',r'C:\Boards\x.txt',PCB+'\nRULE|bad'):
            self.assertFalse(self.call([rule()],path)['success'])
        self.env['altium_bridge'].execute_command.assert_not_awaited()
    def test_full_batch_validated_before_any_write_or_dispatch(self):
        spec=self.env['PCB_RULES_SPEC'];spec.write_text('preserved')
        self.assertFalse(self.call([rule(),rule(name='Bad',values={'connect_style':'invalid'})])['success'])
        self.assertEqual(spec.read_text(),'preserved')
        self.env['altium_bridge'].execute_command.assert_not_awaited()
    def test_legacy_numeric_serialization_unchanged(self):
        self.assertEqual(self.env['_rule_values_spec']('Width',dict(min=.15,max=2)),'min=0.15;max=2.0')
        self.assertTrue(self.call([dict(name='Width',kind='Width',values={'min':.15})])['success'])
    def test_native_preflight_precedes_mutation_and_save_is_verified(self):
        s=(ROOT/'AltiumScript/pcb_document.pas').read_text('utf-8-sig')
        body=s.split('function SetPcbRulesReport(',1)[1].split('// A contour',1)[0]
        self.assertLess(body.index('PolygonRulePreflight(Board, Spec)'),body.index('PCBServer.PreProcess'))
        self.assertIn('finally\n            PCBServer.PostProcess;',body)
        self.assertIn("finally\n                        if Action = 'updated'",body)
        self.assertLess(body.index('PolygonConnect readback failed'),body.index('SaveBoardDocument(PcbPath)'))
        self.assertIn('if not SaveBoardDocument(PcbPath) then',body)
        guard=s.split('function PolygonRuleBoardGuard(',1)[1].split('function PolygonRulePreflight',1)[0]
        for token in ('Doc.FileName','Board.FileName','Current.I_ObjectAddress','ProcessDepth'):
            self.assertIn(token,guard)
        for token in ('OpenBoardAtPath','ShowDocument','.Focus','SaveBoardDocument'):
            self.assertNotIn(token,guard)
    def test_native_style_only_no_thermal_setters_and_priority_conflict_guard(self):
        s=(ROOT/'AltiumScript/pcb_document.pas').read_text('utf-8-sig')
        body=s.split('function ApplyRuleValues(',1)[1].split('function SetPcbRulesReport(',1)[0]
        style=body.split("if Kind = 'PolygonConnect' then",1)[1].split("else if Kind = 'Width'",1)[0]
        for name in ('eDirectConnectToPlane','eReliefConnectToPlane','eNoConnect'):self.assertIn(name,style)
        for name in ('ReliefAirGap','ReliefConductorWidth','ReliefEntries','PolygonReliefAngle'):self.assertNotIn(name,style)
        self.assertIn('existing rule kind mismatch',s)
        self.assertIn('PolygonConnect final priorities conflict',s)
        self.assertIn('new PolygonConnect requires explicit priority',s)

if __name__=='__main__':unittest.main()
