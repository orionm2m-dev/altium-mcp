"""Cached polygon field parsing preserves coordinate text and bounded waits."""
import ast
import asyncio
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pcb_polygons import build_polygon_spec
PCB=r'C:\Boards\Qualification.PcbDoc'

def old_field(text,index):
    start=0;field=0
    for i,char in enumerate(text):
        if char=='|':
            if field==index:return text[start:i]
            field+=1;start=i+1
    return text[start:] if field==index else ''

def split_like_native(text):
    fields=[];start=0
    for i,char in enumerate(text):
        if char=='|':fields.append(text[start:i]);start=i+1
    fields.append(text[start:]);return fields

class PolygonParseTests(unittest.TestCase):
    def test_empty_trailing_and_metadata_spaces_preserved(self):
        for text in ('','|','||','POLYGON|create|spaced name||top|1|','0|-0|1.23456789|1e-08'):
            fields=split_like_native(text)
            self.assertEqual(fields,text.split('|'))
            for index in range(-1,len(fields)+2):
                self.assertEqual(fields[index] if 0<=index<len(fields) else '',old_field(text,index))
    def test_720_vertex_spec_every_field_is_exactly_legacy_parser(self):
        vertices=[[22.2*math.cos(i*math.tau/720),22.2*math.sin(i*math.tau/720)]for i in range(720)]
        row=build_polygon_spec(PCB,[dict(operation='create',name='Ground',net='GND',layer='top',vertices=vertices)]).splitlines()[1]
        fields=split_like_native(row)
        self.assertEqual(len(fields),11+2*720)
        self.assertEqual(fields,[old_field(row,i)for i in range(len(fields))])
    def test_native_hot_loops_use_cached_fields_and_same_numeric_conversion(self):
        s=(ROOT/'AltiumScript/pcb_primitives.pas').read_text('utf-8-sig')
        helper=s.split('procedure SplitPourFields(',1)[1].split('function PourRequestKey(',1)[0]
        self.assertIn('Fields.Clear;',helper)
        self.assertIn("if Rec[i] = '|' then",helper)
        self.assertIn('Fields.Add(Copy(Rec, Start, Length(Rec) - Start + 1));',helper)
        self.assertIn('MMsToCoord(SafeStrToFloat(Field))',helper)
        for begin,end in [('function PourRequestKey(','function PourCopperRegionCount('),('procedure ApplyPourGeometry(','function SetPcbPolygonsReport(')]:
            part=s.split(begin,1)[1].split(end,1)[0]
            self.assertIn('SplitPourFields(Rec, Fields);',part)
            self.assertIn('finally Fields.Free;',part)
            self.assertNotIn('PrimitiveCoord(Rec,',part)
        pre=s.split('function SetPcbPolygonsReport(',1)[1].split('if DryRun then begin',1)[0]
        self.assertIn('SplitPourFields(Rec, Fields);',pre)
        self.assertNotIn('PrimitiveCoord(Rec,',pre)
    def test_timeout_is_validated_before_write_and_dispatch(self):
        tree=ast.parse((ROOT/'main.py').read_text('utf-8'))
        nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))and n.name in {'set_pcb_polygons','_script_result'}]
        for n in nodes:n.decorator_list=[]
        class Bridge:execute_command=AsyncMock(return_value={'success':False,'error':'timeout'})
        with tempfile.TemporaryDirectory()as directory:
            env=dict(json=json,Context=object,EXCHANGE_DIR=Path(directory),SPEC_ENCODING='utf-8',build_polygon_spec=build_polygon_spec,altium_bridge=Bridge())
            exec(compile(ast.Module(body=nodes,type_ignores=[]),'wrapper','exec'),env)
            for value in (True,0,3601,1.5,'900'):
                result=json.loads(asyncio.run(env['set_pcb_polygons'](None,PCB,dry_run=True,timeout_s=value)))
                self.assertFalse(result['success'])
            env['altium_bridge'].execute_command.assert_not_awaited()
            self.assertFalse((Path(directory)/'pcb_polygons_spec.txt').exists())
            asyncio.run(env['set_pcb_polygons'](None,PCB,dry_run=True,timeout_s=900))
            env['altium_bridge'].execute_command.assert_awaited_once_with('set_pcb_polygons',{},timeout=900)

if __name__=='__main__':unittest.main()
