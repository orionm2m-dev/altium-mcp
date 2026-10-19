"""Offline polygon contract and native boundary tests; no Altium access."""
import ast
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pcb_polygons import build_polygon_spec
ROOT=Path(__file__).resolve().parents[1]
PCB=r"C:\Boards\Controller.PcbDoc"


def polygon(**changes):
    item=dict(operation="upsert",name="GroundTop",net="GND",layer="top",vertices=[[0,0],[10,0],[10,10],[0,10]])
    item.update(changes)
    return item


def cutout(**changes):
    item=dict(operation="upsert",name="SensorVoid",layer="top",vertices=[[2,2],[4,2],[4,4],[2,4]])
    item.update(changes)
    return item


class PolygonSpecTests(unittest.TestCase):
    def test_cutouts_are_first_and_have_no_net_and_explicit_solid_options(self):
        lines=build_polygon_spec(PCB,[polygon()],[cutout()]).splitlines()
        self.assertEqual(lines[0],'PCB|'+PCB+'|0')
        self.assertTrue(lines[1].startswith('CUTOUT|upsert|SensorVoid||top|1|1|0.15|0.01|0|4|'))
        self.assertTrue(lines[2].startswith('POLYGON|upsert|GroundTop|GND|top|1|1|0.15|0.01|0|4|'))

    def test_mutation_requires_targets_but_inventory_is_read_only(self):
        with self.assertRaises(ValueError):build_polygon_spec(PCB)
        self.assertEqual(build_polygon_spec(PCB,dry_run=True),'PCB|'+PCB+'|1\n')
        for args in ({'polygons':{}},{'cutouts':{}},{'dry_run':'false'}):
            with self.subTest(args=args),self.assertRaises(ValueError):build_polygon_spec(PCB,**args)

    def test_identity_is_shared_and_case_collisions_fail(self):
        with self.assertRaisesRegex(ValueError,'duplicate'):build_polygon_spec(PCB,[polygon(),polygon(name='groundtop')])
        with self.assertRaisesRegex(ValueError,'duplicate'):build_polygon_spec(PCB,[polygon()],[cutout(name='GroundTop')])

    def test_bad_control_values_or_unsupported_fields_fail(self):
        for changes in ({'net':''},{'net':'GND|PCB'},{'name':' '},{'layer':'multi'}, {'layer':'mid1'},
                        {'operation':'add'},{'locked':1},{'remove_dead':'true'}, {'neck_width_mm':0},
                        {'arc_tolerance_mm':float('nan')},{'pour_over':'all'},{'holes':[]},{'allow_empty':'false'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):build_polygon_spec(PCB,[polygon(**changes)])
        with self.assertRaises(ValueError):build_polygon_spec(PCB,cutouts=[cutout(net='GND')])

    def test_bad_paths_or_names_cannot_inject_records(self):
        for path in ('Controller.PcbDoc',r'C:Controller.PcbDoc',PCB+'\nPOLYGON',r'C:\Board.PcbLib'):
            with self.subTest(path=path),self.assertRaises(ValueError):build_polygon_spec(path,[polygon()])
        for name in ('GND\nPOLYGON','GND|other','\x00','x'*129):
            with self.subTest(name=name),self.assertRaises(ValueError):build_polygon_spec(PCB,[polygon(name=name)])

    def test_geometry_rejects_degenerate_crossed_touching_or_rounded_duplicates(self):
        contours=[[],[[0,0],[1,0]],[[0,0],[1,0],[2,0]],[[0,0],[2,2],[0,2],[2,0]],
                  [[0,0],[4,0],[4,4],[2,0],[0,4]],[[0,0],[2,0],[1,0],[1,1]],
                  [[0,0],[1,0],[1,1],[0,0]],[[0,0],[1e-9,0],[1,1]],
                  [[0,0],[True,0],[1,1]],[[0,0],[float('inf'),0],[1,1]],
                  [[0,0],[2001,0],[1,1]],[[0,0],[1,0,3],[1,1]]]
        for vertices in contours:
            with self.subTest(vertices=vertices),self.assertRaises(ValueError):
                build_polygon_spec(PCB,[polygon(vertices=vertices)])

    def test_concave_simple_contours_and_both_orientations_work(self):
        vertices=[[0,0],[4,0],[4,4],[2,2],[0,4]]
        for shape in (vertices,vertices[::-1]):
            self.assertIn('|5|',build_polygon_spec(PCB,[polygon(vertices=shape)]))

    def test_delete_repour_require_exact_identity_without_geometry_or_options(self):
        for action in ('delete','repour'):
            item={k:v for k,v in polygon(operation=action).items() if k!='vertices'}
            self.assertIn('|'+action+'|',build_polygon_spec(PCB,[item]))
            with self.assertRaises(ValueError):build_polygon_spec(PCB,[dict(item,locked=True)])
        item=dict(operation='repour',name='GroundTop',net='GND',layer='top',allow_empty=True)
        self.assertIn('|1|0\n',build_polygon_spec(PCB,[item]))
        with self.assertRaises(ValueError):build_polygon_spec(PCB,cutouts=[dict(operation='repour',name='Void',layer='top')])

    def test_full_batch_is_validated_before_dispatch(self):
        tree=ast.parse((ROOT/'main.py').read_text(encoding='utf-8'))
        chosen=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in {'_script_result','set_pcb_polygons'}]
        for n in chosen:n.decorator_list=[]
        class Bridge: execute_command=AsyncMock(return_value={'success':False,'error':'timeout'})
        with tempfile.TemporaryDirectory() as directory:
            env=dict(json=json,Context=object,EXCHANGE_DIR=Path(directory),SPEC_ENCODING='utf-8',build_polygon_spec=build_polygon_spec,altium_bridge=Bridge())
            exec(compile(ast.Module(body=chosen,type_ignores=[]),'polygon_wrapper','exec'),env)
            result=json.loads(asyncio.run(env['set_pcb_polygons'](None,PCB,[polygon()],[cutout(vertices=[[0,0]])])))
            self.assertFalse(result['success']);env['altium_bridge'].execute_command.assert_not_awaited()
            result=json.loads(asyncio.run(env['set_pcb_polygons'](None,PCB,[polygon()],[cutout()],True)))
            self.assertFalse(result['success']);self.assertEqual(result['last_phase'],'dispatch_pending')
            env['altium_bridge'].execute_command.assert_awaited_once_with('set_pcb_polygons',{},timeout=180)
            self.assertEqual((Path(directory)/'pcb_polygons_spec.txt').read_text(),build_polygon_spec(PCB,[polygon()],[cutout()],True))

    def test_native_preflight_save_and_preferences_contract(self):
        text=(ROOT/'AltiumScript/pcb_primitives.pas').read_text(encoding='utf-8')
        body=text.split('function SetPcbPolygonsReport',1)[1]
        for guard in ('PcbPrimitiveProcessGuard(0)','PourOwnershipGuard(P, Rec)','PourShapeGuard(P)','PourAffectedGuard(Board, Spec)','NetCount <> 1'):
            self.assertLess(body.index(guard),body.index('PCBServer.PreProcess;'))
        self.assertEqual(body.count('PCBServer.PreProcess;'),1)
        self.assertEqual(body.count('PCBServer.PostProcess;'),1)
        self.assertIn('finally\n                KeepoutTrace',body)
        self.assertIn('finally\n            PCBServer.SystemOptions.PolygonRepour := OldRepourMode;',body)
        self.assertLess(body.index('if DryRun then begin'),body.index('PCBServer.PreProcess;'))
        self.assertLess(body.index('Poly.Rebuild;'),body.index("'final_readback'"))
        self.assertLess(body.index("'final_readback'"),body.index('SaveBoardDocument(PcbPath)'))
        self.assertIn("if Verified and (Error = '') then Saved := SaveBoardDocument(PcbPath)",body)
        self.assertIn('eRegionKind_Cutout',text)
        self.assertNotIn('eRegionKind_PolygonCutout',text)
        self.assertIn("'set_pcb_polygons':",(ROOT/'AltiumScript/Altium_API.pas').read_text())
        self.assertIn("CommandName = 'set_pcb_polygons'",(ROOT/'AltiumScript/other_utils.pas').read_text())

if __name__=='__main__':unittest.main()
