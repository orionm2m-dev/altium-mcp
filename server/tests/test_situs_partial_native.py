"""Pure candidate/parser tests; no Altium, UI, or native dispatch."""
import json
import hashlib
import re
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import situs_partial_native as native
import script_lint

class NativeTests(unittest.TestCase):
    def setUp(self):
        self.request=native.build_partial_request(r'C:\work\candidate.PcbDoc','a'*32,'TP01','INT1_U1')
        self.value=dict(self.request,**{k:True for k in native.BOOLS},stage='returned',error='',
            before_copper_records=['track','track'],after_copper_records=['track','track','new'],
            before_copper_count=2,after_copper_count=3,before_selected_components=['U1'],after_selected_components=['U1'])
    def parse(self,**changes):
        return native.parse_partial_receipt(json.dumps(dict(self.value,**changes)),self.request)
    def test_return_is_not_drc_or_complete(self):
        r=self.parse()
        self.assertTrue(r['success'] and r['old_copper_preserved'])
        for k in ('drc_verified','connectivity_verified','routing_complete','completion_verified','retry_allowed'):
            self.assertFalse(r[k])
    def test_duplicate_copper_multiplicity_required(self):
        with self.assertRaises(ValueError):self.parse(after_copper_records=['track','new'],after_copper_count=2)
    def test_selection_restoration_required(self):
        with self.assertRaises(ValueError):self.parse(after_selected_components=['U2'])
    def test_each_return_guard_must_be_true(self):
        for field in native.BOOLS:
            with self.subTest(field=field), self.assertRaises(ValueError):self.parse(**{field:False})
    def test_identity_drift_rejected(self):
        for field,value in [('operation_id','b'*32),('pcb_path',r'C:\other.PcbDoc'),('expected_net','OTHER'),('component_designator','TP02')]:
            with self.subTest(field=field),self.assertRaises(ValueError):self.parse(**{field:value})
    def test_target_request_is_exact_not_injection(self):
        for name in ('U1','TP1\n','TP1;Reset','TP','TP1/2'):
            with self.subTest(name=name),self.assertRaises(ValueError):native.build_partial_request(self.request['pcb_path'],'a'*32,name,'SIGNAL')
        for net in ('','x\n',None):
            with self.subTest(net=net),self.assertRaises(ValueError):native.build_partial_request(self.request['pcb_path'],'a'*32,'TP01',net)
    def test_generated_paths_are_bound_arguments(self):
        source=native.build_partial_program(self.request,r'C:\receipts\native.json')
        self.assertIn(self.request['pcb_path'],source)
        self.assertIn('C:\\receipts\\native.json',source)
        self.assertEqual(set(re.findall(r"'([A-Za-z]:\\[^']*)'",source)),{self.request['pcb_path'],'C:\\receipts\\native.json'})
        self.assertIn("Module := Client.ServerModuleByName('PCB');",source)
        self.assertIn('(not P.InComponent) and not P.InPolygon',source)
        self.assertEqual(source.count("RunProcess('PCB:Autoroute');"),1)
        self.assertEqual(source.count("AddStringParameter('Action','OnSelectedComponents');"),1)
        for prohibited in ('OnAll','Action=Start','UpdateCaption','SetForegroundWindow','PostMessage','SendMessage','SaveDocument','SetCursorPos'):
            self.assertNotIn(prohibited,source)
        result=script_lint.lint('',source)
        self.assertTrue(result['success'],result)
    def test_receipts_are_write_once_and_mark_before_action(self):
        source=native.build_partial_program(self.request,r'C:\receipts\native.json')
        self.assertIn("StagePath := 'C:\\receipts\\native.json' + '.' + Stage + '.json';",source)
        self.assertIn('if FileExists(StagePath) then Exit;',source)
        self.assertLess(source.index("PReceipt('dispatch_attempted'"),source.index("RunProcess('PCB:Autoroute');"))
    def test_immutable_reader_filename_matches_payload(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'native.json'
            staged=Path(str(path)+'.returned.json')
            staged.write_text(json.dumps(self.value),encoding='utf-8')
            self.assertTrue(native.read_partial_receipt(path,self.request)['success'])
            staged.rename(Path(str(path)+'.guard_failed.json'))
            with self.assertRaises(ValueError):native.read_partial_receipt(path,self.request)
    def test_multiple_terminal_files_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'native.json'
            for stage in ('returned','guard_failed'):
                Path(str(path)+'.'+stage+'.json').write_text(json.dumps(self.value),encoding='utf-8')
            with self.assertRaises(ValueError):native.read_partial_receipt(path,self.request)
    def test_partial_file_is_pending_not_success(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'native.json'
            Path(str(path)+'.returned.json').write_text('{',encoding='utf-8')
            result=native.read_partial_receipt(path,self.request)
            self.assertFalse(result['success'])
            self.assertTrue(result['outcome_unknown'])
    def test_duplicate_json_keys_rejected(self):
        text=json.dumps(self.value).replace('{','{"success":true,',1)
        with self.assertRaises(ValueError):native.parse_partial_receipt(text,self.request)


class PairNativeTests(unittest.TestCase):
    def setUp(self):
        self.request=native.build_pair_request(r'C:\work\candidate.PcbDoc','a'*32,'TP01','J1','INT1_U1')
        self.receipt=dict(self.request,**{k:True for k in native.BOOLS},stage='returned',error='',
            before_copper_records=['track'],after_copper_records=['track','new'],
            before_copper_count=1,after_copper_count=2,
            before_selected_components=['U1'],after_selected_components=['U1'])
        self.source=native.build_partial_program(self.request,r'C:\receipts\native.json')

    def test_single_source_and_generated_bytes_unchanged(self):
        self.assertEqual(hashlib.sha256(native.SOURCE.encode()).hexdigest(),
            '6616e557e1f79433a9a05612ece5394a7841cad27ab5ed433c585ee5762c962a')
        request=native.build_partial_request(r'C:\work\candidate.PcbDoc','a'*32,'TP01','INT1_U1')
        source=native.build_partial_program(request,r'C:\receipts\native.json')
        self.assertEqual(hashlib.sha256(source.encode()).hexdigest(),
            '159d003fb7ddab7ee69308d1c603155ffdd23b31a45ab53c4572c7fa8a08b606')

    def test_pair_source_derivation_fails_closed_if_old_template_changes(self):
        with self.assertRaises(ValueError):
            native._build_pair_source(native.SOURCE+'\n')

    def test_partner_validation_rejects_same_or_injected_designator(self):
        for partner in ('TP01','tp01','','J','J1;Run','J1\n','J1/2',None,123,'J'+'1'*31):
            with self.subTest(partner=partner),self.assertRaises(ValueError):
                native.build_pair_request(self.request['pcb_path'],'a'*32,'TP01',partner,'INT1_U1')

    def test_pair_request_is_fixed_and_cannot_change_scope(self):
        for field,value in (('action','OnSelectedComponents'),('action','OnAll'),
                ('process','PCB:Other'),('routing_strategy','Completion'),
                ('requires_lock_preroutes',False),('document_save_requested',True)):
            with self.subTest(field=field),self.assertRaises(ValueError):
                native.build_partial_program(dict(self.request,**{field:value}),r'C:\receipts\native.json')
        missing=dict(self.request)
        del missing['partner_designator']
        with self.assertRaises(ValueError):
            native.build_partial_program(missing,r'C:\receipts\native.json')

    def test_pair_receipt_binds_partner_scope_and_net(self):
        parsed=native.parse_partial_receipt(json.dumps(self.receipt),self.request)
        self.assertTrue(parsed['success'])
        self.assertEqual(parsed['partner_designator'],'J1')
        self.assertEqual(parsed['action'],'BetweenSelectedComponents')
        for field,value in (('partner_designator','J2'),('action','OnSelectedComponents'),
                ('expected_net','GND'),('component_designator','TP02')):
            with self.subTest(field=field),self.assertRaises(ValueError):
                native.parse_partial_receipt(json.dumps(dict(self.receipt,**{field:value})),self.request)
        self.assertFalse(parsed['drc_verified'] or parsed['routing_complete'] or parsed['completion_verified'])

    def test_pair_failed_copper_readback_stays_failed_and_unknown(self):
        value=dict(self.receipt,stage='readback_failed',
            error='old_free_copper_changed_or_snapshot_incomplete',
            success=False,return_verified=False,old_copper_preserved=False,
            after_copper_records=['changed'],after_copper_count=1)
        parsed=native.parse_partial_receipt(json.dumps(value),self.request)
        self.assertFalse(parsed['success'])
        self.assertTrue(parsed['outcome_unknown'])
        self.assertFalse(parsed['retry_allowed'])

    def test_exact_pair_selection_and_shared_net_guards_precede_dispatch(self):
        source=self.source
        dispatch=source.index("RunProcess('PCB:Autoroute');")
        self.assertIn("Wanted.Add('TP01'); Wanted.Add('J1'); Wanted.Sort;",source[:dispatch])
        self.assertIn('(Wanted.Text <> CheckSelected.Text)',source[:dispatch])
        self.assertIn('(Found <> 1) or (FoundPartner <> 1)',source[:dispatch])
        self.assertIn('Count := Count+1; if Count > 1 then Exit;',source[:dispatch])
        self.assertIn('Matched := Matched+1; if Matched > 1 then Exit;',source[:dispatch])
        self.assertIn("if Pad.Net.Name = 'INT1_U1' then",source[:dispatch])
        self.assertIn('(Matched <> 1) or (PartnerPad = nil)',source[:dispatch])
        self.assertIn('(TargetPad.I_ObjectAddress <> CheckTargetPad.I_ObjectAddress)',source[:dispatch])
        self.assertIn('(PartnerPad.I_ObjectAddress <> CheckPartnerPad.I_ObjectAddress)',source[:dispatch])
        self.assertIn('(Partner.I_ObjectAddress <> CheckPartner.I_ObjectAddress)',source[:dispatch])
        self.assertLess(source.index("PReceipt('dispatch_attempted'"),dispatch)

    def test_return_and_restoration_recheck_pair_identity(self):
        after=self.source.split("RunProcess('PCB:Autoroute');",1)[1]
        self.assertIn('(Partner.I_ObjectAddress <> CheckPartner.I_ObjectAddress)',after)
        self.assertIn('(PartnerPad.I_ObjectAddress <> CheckPartnerPad.I_ObjectAddress)',after)
        self.assertIn('(Wanted.Text <> CheckSelected.Text)',after)
        self.assertIn('CopperPreserved := PContainsOld(BeforeCopper,AfterCopper)',after)
        restoration=after.split('// Restore only',1)[1]
        self.assertIn('if SelectionCaptured and PGuard(Board) then',restoration)
        self.assertIn('if PPairPads(CheckTarget,CheckPartner,CheckTargetPad,CheckPartnerPad) then',restoration)
        self.assertIn('(Partner.I_ObjectAddress = CheckPartner.I_ObjectAddress)',restoration)
        self.assertIn('(PartnerPad.I_ObjectAddress = CheckPartnerPad.I_ObjectAddress)',restoration)
        self.assertIn('(BeforeSelected.Text = AfterSelected.Text)',restoration)

    def test_pair_program_uses_only_fixed_native_action_and_lints(self):
        source=self.source
        self.assertEqual(source.count("RunProcess('PCB:Autoroute');"),1)
        self.assertEqual(source.count("AddStringParameter('Action','BetweenSelectedComponents');"),1)
        self.assertNotIn('OnSelectedComponents',source)
        for marker in ('__PCB__','__TP__','__PARTNER__','__NET__','__METADATA__','__RECEIPT__'):
            self.assertNotIn(marker,source)
        for prohibited in ('OnAll','Action=Start','SetForegroundWindow','PostMessage',
                'SendMessage','SaveDocument','SetCursorPos','LockPreRoutes :=','Board.AddPCBObject'):
            self.assertNotIn(prohibited,source)
        result=script_lint.lint('',source)
        self.assertTrue(result['success'],result)

if __name__=='__main__':unittest.main()
