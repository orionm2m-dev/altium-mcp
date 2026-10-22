"""Offline routing-contract tests; no Altium import, process or native writes."""
import ast
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pcb_routing import build_routing_spec

PCB = r"C:\Boards\Controller.PcbDoc"
ROOT = Path(__file__).resolve().parents[1]


def track(**changes):
    value = dict(operation="add", net="SIGNAL", layer="bottom", x1_mm=1, y1_mm=2,
                 x2_mm=3, y2_mm=4, width_mm=.25)
    value.update(changes)
    return value


def via(**changes):
    value = dict(operation="add", net="GND", x_mm=3, y_mm=4, diameter_mm=.6,
                 hole_mm=.3, start_layer="top", end_layer="bottom")
    value.update(changes)
    return value


class RoutingSpecTests(unittest.TestCase):
    def test_track_and_via_have_explicit_geometry_and_net(self):
        lines = build_routing_spec(PCB, [track()], [via()]).splitlines()
        self.assertEqual(lines, ["PCB|" + PCB,
            "TRACK|add|SIGNAL|bottom|1|2|3|4|0.25|1", "VIA|add|GND|top|bottom|3|4|0.6|0.3|1"])

    def test_delete_requires_complete_exact_geometry(self):
        self.assertIn("TRACK|delete|SIGNAL|bottom|1|2|3|4|0.25|1", build_routing_spec(PCB, [track(operation="delete")]))
        for item in (track(operation="delete", locked=False), {"operation":"delete", "net":"SIGNAL"}):
            with self.assertRaises(ValueError):
                build_routing_spec(PCB, [item])

    def test_reverse_track_and_conflicting_actions_or_nets_are_duplicate_targets(self):
        for second in (track(), track(x1_mm=3,y1_mm=4,x2_mm=1,y2_mm=2),
                       track(net="OTHER"), track(operation="delete")):
            with self.subTest(second=second), self.assertRaisesRegex(ValueError, "duplicate"):
                build_routing_spec(PCB, [track(), second])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_routing_spec(PCB, vias=[via(), via(net="OTHER")])

    def test_distinct_layers_are_not_duplicate_geometry(self):
        self.assertEqual(len(build_routing_spec(PCB, [track(), track(layer="top")]).splitlines()), 3)

    def test_only_explicit_through_vias_with_annular_copper(self):
        for changes in ({"start_layer":"mid1"}, {"end_layer":"top"}, {"diameter_mm":.3},
                        {"hole_mm":0}, {"diameter_mm":-1}, {"plated":False}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_routing_spec(PCB, vias=[via(**changes)])

    def test_invalid_geometry_and_unknown_controls_fail(self):
        for changes in ({"x1_mm":float("nan")}, {"y2_mm":float("inf")}, {"x1_mm":True},
                        {"width_mm":0}, {"width_mm":-1}, {"x2_mm":1,"y2_mm":2},
                        {"unique_id":"ABCDEFGH"}, {"locked":"true"}, {"operation":"ripup"},
                        {"layer":"all"}, {"layer":"multi"}, {"layer":"mid31"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_routing_spec(PCB, [track(**changes)])

    def test_net_names_and_paths_cannot_inject_records(self):
        for net in ("", "  ", "GND|VIA", "GND\nTRACK", "GND\x00"):
            with self.subTest(net=net), self.assertRaises(ValueError):
                build_routing_spec(PCB, [track(net=net)])
        for path in ("board.PcbDoc", r"C:board.PcbDoc", PCB+"\nVIA", r"C:\test.PcbLib"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                build_routing_spec(path, [track()])
        self.assertIn("|NetC_1+|", build_routing_spec(PCB, [track(net="NetC_1+")]))

    def test_entire_batch_is_validated_without_partial_return(self):
        with self.assertRaises(ValueError):
            build_routing_spec(PCB, [track()], [via(hole_mm=-1)])
        for args in ((None,None), ([],[]), ({},[]), ([],{})):
            with self.subTest(args=args), self.assertRaises(ValueError):
                build_routing_spec(PCB,*args)

    def test_real_native_rounding_validation_is_before_mutation(self):
        source=(ROOT/'AltiumScript/pcb_primitives.pas').read_text(encoding='utf-8').split('// Explicit free copper track/via batches.',1)[1]
        body=source.split('function SetPcbRoutingReport',1)[1].split('// Named solid copper polygons',1)[0]
        for guard in ('RoutingRecordGuard(Board, Rec)', 'NetCount <> 1', 'Keys.IndexOf(Key)',
                      'Count > 1', "(Action = 'delete') and (Count <> 1)",
                      'RoutingOwnershipGuard(P, NetName)', 'PcbPrimitiveProcessGuard(0)'):
            self.assertIn(guard,body)
            self.assertLess(body.index(guard),body.index('PCBServer.PreProcess;'))
        self.assertIn('track endpoints coincide in native coordinates',source)
        self.assertIn('duplicate native routing geometry in batch',source)
        for guard in ('P.Component <> nil', 'P.InPolygon', 'P.IsKeepout', 'P.Net = nil',
                      'P.Net.Name <> NetName', 'Via.Mode <> ePadMode_Simple'):
            self.assertIn(guard,source)
        self.assertNotIn('UniqueId :=',source)
        self.assertNotIn('SetState_Unique',source)

    def test_native_save_is_after_final_geometry_and_net_verification(self):
        source=(ROOT/'AltiumScript/pcb_primitives.pas').read_text(encoding='utf-8').split('// Explicit free copper track/via batches.',1)[1]
        body=source.split('function SetPcbRoutingReport',1)[1].split('// Named solid copper polygons',1)[0]
        self.assertEqual(body.count('PCBServer.PreProcess;'),1)
        self.assertEqual(body.count('PCBServer.PostProcess;'),1)
        self.assertIn("finally\n            KeepoutTrace(Trace, TracePath, 'postprocess'",body)
        self.assertLess(body.index('Board.ConnectivelyValidateNets'),body.index("'final_readback'"))
        self.assertLess(body.index("'final_readback'"),body.index('SaveBoardDocument(PcbPath)'))
        self.assertIn("if Verified and (Error = '') then Saved := SaveBoardDocument(PcbPath)",body)
        self.assertIn('Board.RemovePCBObject(P)',body)
        self.assertNotIn('DestroyPCBOBject',body)
        self.assertIn('delete requires exactly one existing routing object',source)
        self.assertIn('deleted routing is still present',source)

    def test_public_tool_is_registered_and_invalid_batch_never_dispatches(self):
        tree=ast.parse((ROOT/'main.py').read_text(encoding='utf-8'))
        chosen=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))
                and n.name in {'_script_result','set_pcb_routing'}]
        for n in chosen:n.decorator_list=[]
        class Bridge:
            execute_command=AsyncMock(return_value={'success':False,'error':'timeout'})
        with tempfile.TemporaryDirectory() as directory:
            env={'json':json,'Context':object,'EXCHANGE_DIR':Path(directory),'SPEC_ENCODING':'utf-8',
                 'build_routing_spec':build_routing_spec,'altium_bridge':Bridge()}
            exec(compile(ast.Module(body=chosen,type_ignores=[]),'isolated_routing_wrapper','exec'),env)
            bad=json.loads(asyncio.run(env['set_pcb_routing'](None,PCB,[track(width_mm=0)])))
            self.assertFalse(bad['success'])
            env['altium_bridge'].execute_command.assert_not_awaited()
            result=json.loads(asyncio.run(env['set_pcb_routing'](None,PCB,[track()],[via()])))
            env['altium_bridge'].execute_command.assert_awaited_once_with('set_pcb_routing',{},timeout=120)
            self.assertFalse(result['success'])
            self.assertEqual(result['last_phase'],'dispatch_pending')
            self.assertEqual((Path(directory)/'pcb_routing_spec.txt').read_text(),build_routing_spec(PCB,[track()],[via()]))
        self.assertIn("'set_pcb_routing':",(ROOT/'AltiumScript/Altium_API.pas').read_text())
        self.assertIn("CommandName = 'set_pcb_routing'",(ROOT/'AltiumScript/other_utils.pas').read_text())
        self.assertIn('DocumentPath=pcb_primitives.pas',(ROOT/'AltiumScript/Altium_API.PrjScr').read_text())
        self.assertNotIn('DocumentPath=pcb_routing.pas',(ROOT/'AltiumScript/Altium_API.PrjScr').read_text())



    def test_timeout_bounds_fail_before_exchange_writes_or_dispatch(self):
        tree=ast.parse((ROOT/'main.py').read_text(encoding='utf-8'))
        chosen=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))
                and n.name in {'_script_result','set_pcb_routing'}]
        for n in chosen:n.decorator_list=[]
        with tempfile.TemporaryDirectory() as directory:
            exchange=Path(directory)
            for filename in ('pcb_routing_spec.txt','pcb_routing_spec.trace.txt'):
                (exchange/filename).write_text('previous operation',encoding='utf-8')
            bridge=type('Bridge',(),{'execute_command':AsyncMock()})()
            env=dict(json=json,Context=object,EXCHANGE_DIR=exchange,SPEC_ENCODING='utf-8',
                     build_routing_spec=build_routing_spec,altium_bridge=bridge)
            exec(compile(ast.Module(body=chosen,type_ignores=[]),'isolated_routing_timeout','exec'),env)
            for timeout in (0,-1,3601,True,False,1.0,'120',None):
                with self.subTest(timeout=timeout):
                    result=json.loads(asyncio.run(env['set_pcb_routing'](None,PCB,[track()],timeout_s=timeout)))
                    self.assertEqual(result,{'success':False,'error':'timeout_s must be an integer in 1..3600'})
                    bridge.execute_command.assert_not_awaited()
                    self.assertEqual({p.name:p.read_text(encoding='utf-8') for p in exchange.iterdir()},
                                     {name:'previous operation' for name in ('pcb_routing_spec.txt','pcb_routing_spec.trace.txt')})

    def test_custom_timeout_reaches_dispatch_once_and_does_not_replay_failure(self):
        tree=ast.parse((ROOT/'main.py').read_text(encoding='utf-8'))
        chosen=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))
                and n.name in {'_script_result','set_pcb_routing'}]
        for n in chosen:n.decorator_list=[]
        for timeout in (1,600,3600):
            with self.subTest(timeout=timeout),tempfile.TemporaryDirectory() as directory:
                bridge=type('Bridge',(),{'execute_command':AsyncMock(return_value={'success':False,'error':'timeout'})})()
                env=dict(json=json,Context=object,EXCHANGE_DIR=Path(directory),SPEC_ENCODING='utf-8',
                         build_routing_spec=build_routing_spec,altium_bridge=bridge)
                exec(compile(ast.Module(body=chosen,type_ignores=[]),'isolated_routing_timeout','exec'),env)
                result=json.loads(asyncio.run(env['set_pcb_routing'](None,PCB,[track()],[via()],timeout_s=timeout)))
                bridge.execute_command.assert_awaited_once_with('set_pcb_routing',{},timeout=timeout)
                self.assertFalse(result['success'])
                self.assertEqual(result['error'],'timeout')
                self.assertEqual(result['last_phase'],'dispatch_pending')
                self.assertEqual((Path(directory)/'pcb_routing_spec.txt').read_text(),build_routing_spec(PCB,[track()],[via()]))

if __name__=='__main__': unittest.main()
