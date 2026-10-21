import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import native_situs_protocol as p

class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.req=dict(schema_version=1,request_id='a'*32,action='read_settings',
            pcb_path=r'C:\boards\Тест board.PcbDoc',pid=123,creation_filetime='134370710410181153',
            exe=r'C:\Program Files\Altium\X2.EXE',payload={})
        self.path='C:\\exchange\\' + self.req['request_id'] + '\\request.json'
    def build(self): return p.build_request(self.req,self.path)
    def receipt(self):
        plan=self.build()
        return dict(self.req,request_sha256=plan['request_sha256'],success=True,
            native_handler_completed=True,operation_invoked=True,outcome_unknown=False,
            retry_allowed=False,document_saved=False,data=dict(success=True,outcome_unknown=False))
    def check(self,response): return p.classify_response(self.req,self.build()['request_sha256'],response,0)
    def test_one_safe_argv_and_exact_utf8_hash(self):
        plan=self.build()
        self.assertEqual(json.loads(plan['request_bytes']),self.req)
        self.assertEqual(hashlib.sha256(plan['request_bytes']).hexdigest(),plan['request_sha256'])
        self.assertRegex(plan['argument'],r'^-RNativeSitusControl:ExecuteRequest\(Request=[A-Za-z0-9_-]+\|Sha256=[0-9a-f]{64}\)$')
        self.assertNotIn('Тест',plan['argument']);self.assertNotIn(' ',plan['argument'])
    def test_payload_change_changes_bound_hash(self):
        first=self.build()['request_sha256'];self.req['payload']={'name':'changed'}
        self.assertNotEqual(first,self.build()['request_sha256'])
    def test_different_nonce_directory_refused(self):
        self.path=self.path.replace('a'*32,'b'*32)
        with self.assertRaises(ValueError):self.build()
    def test_unknown_action_and_envelope_refused(self):
        for changed in ({'action':'Client:QuitFromEDAClient'},{'source':'RunProcess()'},{'pid':True},{'creation_filetime':134370710410181153}):
            with self.subTest(changed=changed):
                req=dict(self.req,**changed)
                with self.assertRaises(ValueError):p.build_request(req,self.path)
    def test_bad_path_forms_refused(self):
        for path in ('relative.PcbDoc',r'\\server\share\board.PcbDoc',r'C:\x\board.PcbDoc:stream','C:\\x\nfoo.PcbDoc'):
            with self.subTest(path=path),self.assertRaises(ValueError):
                p.build_request(dict(self.req,pcb_path=path),self.path)
    def test_nan_and_oversize_refused(self):
        for payload in ({'x':float('nan')},{'x':'x'*65536}):
            with self.assertRaises(ValueError):p.build_request(dict(self.req,payload=payload),self.path)
    def test_launcher_zero_never_proves_completion(self):
        result=self.check(None);self.assertFalse(result['completed']);self.assertTrue(result['outcome_unknown']);self.assertFalse(result['retry_allowed'])
    def test_exact_complete_native_receipt(self):
        result=self.check(self.receipt());self.assertTrue(result['completed']);self.assertTrue(result['action_success'])
    def test_receipt_identity_hash_tampering_stays_unknown(self):
        for changed in ({'request_id':'b'*32},{'request_sha256':'0'*64},{'pid':True},{'creation_filetime':'134370710410181154'},{'pcb_path':r'C:\boards\other.PcbDoc'}):
            with self.subTest(changed=changed):
                result=self.check(dict(self.receipt(),**changed));self.assertTrue(result['outcome_unknown']);self.assertFalse(result['completed'])
    def test_nested_partial_setter_is_not_promoted(self):
        receipt=self.receipt();receipt['data']={'success':False,'outcome_unknown':True,'mutation_attempted':True}
        result=self.check(receipt);self.assertTrue(result['outcome_unknown']);self.assertFalse(result['action_success'])
    def test_known_native_refusal_never_replays(self):
        receipt=self.receipt();receipt.update(success=False,native_handler_completed=False,operation_invoked=False)
        result=self.check(receipt);self.assertTrue(result['completed']);self.assertFalse(result['outcome_unknown']);self.assertFalse(result['retry_allowed'])
    def test_noncanonical_envelope_rejected(self):
        self.req['pcb_path']=r'C:\boards\sub\..\test.PcbDoc'
        with self.assertRaises(ValueError):self.build()
    def test_missing_action_result_or_entry_does_not_complete(self):
        for changed in ({'data':None},{'data':{}},{'operation_invoked':False}):
            with self.subTest(changed=changed):
                result=self.check(dict(self.receipt(),**changed))
                self.assertFalse(result['completed']);self.assertTrue(result['outcome_unknown'])
    def test_routing_actions_not_in_this_bundle(self):
        for action in ('start','pause','resume','stop','reset','read_command_state'):
            with self.subTest(action=action),self.assertRaises(ValueError):
                p.build_request(dict(self.req,action=action),self.path)

    def test_native_exception_after_entry_stays_unknown(self):
        receipt=self.receipt();receipt.update(success=False,native_handler_completed=False,outcome_unknown=True)
        result=self.check(receipt);self.assertFalse(result['completed']);self.assertTrue(result['outcome_unknown'])

if __name__=='__main__':unittest.main()
