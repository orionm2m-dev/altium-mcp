"""Offline combined endpoint integration; all process/UI primitives are fakes."""
import asyncio,copy,importlib.util,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import emergency_stop as em
import script_recovery as r
import pending_stop_wizard_resume as m
Q=Path(__file__).resolve().parent
EXE=r'C:\Altium\X2.EXE';MAIN=1234;STOP=5678

class Process:
 def __init__(self,pid,events):
  self.pid=pid;self.events=events;self.closed=False;self.exit=None
  self.data={'pid':pid,'creation_filetime':134369493464722166+pid,'exe':em._canonical(EXE),'user_sid':'owner','running':True}
 def __enter__(self):self.events.append(('open',self.pid));return self
 def __exit__(self,*args):self.closed=True;self.events.append(('close',self.pid))
 def identity(self):self.events.append(('identity',self.pid));return dict(self.data)
 def wait(self,ms):
  assert ms==0 and not self.closed;self.events.append(('wait',self.pid))
  return {'wait_status':258 if self.exit is None else 0,'exit_code':self.exit,'terminated_confirmed':self.exit is not None}

class CombinedTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Q,prefix='.pending_stop_tests_');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.events=[];self.clock=0;self.main=Process(MAIN,self.events);self.stop=Process(STOP,self.events)
  self.state={'token':'2dfc8d269e634141b858b927f135c077','phase':'stop_pending','environment':{'target_pid':MAIN},'launches':[{'phase':'stop','pid':STOP,'exit_code':None}]}
  r._write_state(self.root,self.state);self.original=m._read_latch(self.root)
  self.env={'ready':True,'configured_process_ids':[MAIN],'target_pid':MAIN,'foreground_confirmed':True,'process_windows':{MAIN:[{'class':'TDocumentForm','hwnd':1}]}}
  self.command='"'+EXE+'" '+m.STOP_ARGUMENT;self.cancel_result={'wizard_closed_verified':True,'recovery_latch_unchanged':True,'after':{'same_process_alive':True}}
  self.cancel_hook=lambda:None;self.probe_exit=0;self.probe_wrong=False
  self.patches=[patch.object(em,'_current_sid',return_value='owner'),patch.object(r,'_launches',{}),patch.object(r,'inspect_environment',side_effect=lambda *a:self.env),patch.object(r,'_launch',side_effect=self.launch),patch.object(m.time,'monotonic',side_effect=lambda:self.clock),patch.object(m.asyncio,'sleep',side_effect=self.sleep)]
  for p in self.patches:p.start();self.addCleanup(p.stop)
 def factory(self,pid,**kw):
  self.assertEqual(kw,{'synchronize_access':True});return {MAIN:self.main,STOP:self.stop}[pid]
 async def sleep(self,n):self.clock+=n
 def cancel(self,*args):
  self.assertEqual(args,(MAIN,'98765','observed-token',True));self.events.append(('cancel',MAIN));self.stop.exit=0;self.cancel_hook();return self.cancel_result
 def launch(self,exe,arg):
  self.events.append(('launch',arg));self.assertNotIn('EditScript:Stop',arg)
  state=json.loads((self.root/'script_recovery_state.json').read_text())
  result=Path(state['probe_result_path']);result.write_text(json.dumps({'probe':'executor ok','token':'wrong' if self.probe_wrong else state['token']}))
  return SimpleNamespace(pid=90000,poll=lambda:self.probe_exit)
 async def run_call(self,**kw):
  return await m.cancel_and_resume_locked(EXE,self.root,MAIN,98765,'observed-token',kw.pop('ack',True),1,1,1,process_factory=self.factory,command_reader=lambda pid:self.command,wizard=SimpleNamespace(cancel=self.cancel),**kw)
 def launches(self):return [e for e in self.events if e[0]=='launch']
 def assert_blocked_untouched(self,res,cancelled=False):
  self.assertTrue(res['blocked'],res);self.assertFalse(res['recovered']);self.assertEqual(self.launches(),[])
  if not cancelled:self.assertNotIn(('cancel',MAIN),self.events)
  self.assertEqual(m._read_latch(self.root),self.original)
 async def test_success_captures_before_cancel_and_one_probe_same_handle(self):
  res=await self.run_call();self.assertTrue(res['recovered'],res);self.assertEqual(len(self.launches()),1)
  self.assertFalse(res['state']['manual_stop_confirmed']);self.assertFalse(res['state']['verified_prior_stop_exit']['legacy_historical_identity_verified'])
  self.assertTrue(res['state']['verified_prior_stop_exit']['same_handle_wait']);self.assertTrue(self.main.closed and self.stop.closed)
  firstcancel=self.events.index(('cancel',MAIN));self.assertIn(('identity',STOP),self.events[:firstcancel])
  self.assertIn(('wait',STOP),self.events[firstcancel:]);self.assertIsNone(r.blocked_state(self.root))
 async def test_missing_ack_does_not_capture_or_cancel(self):self.assert_blocked_untouched(await self.run_call(ack=False))
 async def test_wrong_command_or_added_switch_never_cancel(self):
  for cmd in ['',EXE+' '+m.STOP_ARGUMENT,'"'+EXE+'" '+m.STOP_ARGUMENT+' /other','"C:\\Other\\X2.EXE" '+m.STOP_ARGUMENT]:
   self.events=[];self.command=cmd;self.assert_blocked_untouched(await self.run_call())
 async def test_wrong_executable_sid_or_exited_stop(self):
  for changes in [{'exe':'other'},{'user_sid':'other'},{'running':False}]:
   self.stop=Process(STOP,self.events);self.main=Process(MAIN,self.events);self.stop.data.update(changes)
   self.assert_blocked_untouched(await self.run_call())
 async def test_capture_identity_change_no_cancel(self):
  original=self.stop.identity;count=0
  def changed():
   nonlocal count
   value=original();count+=1
   if count>1:value['creation_filetime']+=1
   return value
  self.stop.identity=changed;self.assert_blocked_untouched(await self.run_call())
 async def test_malformed_or_wrong_pending_latch(self):
  for change in [{'phase':'probe_pending'},{'launches':[{'phase':'stop','pid':MAIN,'exit_code':None}]},{'token':'../bad'}]:
   s=dict(self.state,**change);r._write_state(self.root,s);self.original=m._read_latch(self.root)
   self.assert_blocked_untouched(await self.run_call())
 async def test_cancel_refusal_no_probe(self):
  self.cancel_result['wizard_closed_verified']=False;self.assert_blocked_untouched(await self.run_call(),True)
 async def test_cancel_unknown_process_no_probe(self):
  self.cancel_result['after']['same_process_alive']=False;self.assert_blocked_untouched(await self.run_call(),True)
 async def test_latch_changed_during_cancel_no_probe_or_overwrite(self):
  self.cancel_hook=lambda:(self.root/'script_recovery_state.json').write_text('{"phase":"other"}')
  res=await self.run_call();self.assertTrue(res['blocked']);self.assertEqual(self.launches(),[])
  self.assertEqual((self.root/'script_recovery_state.json').read_text(),'{"phase":"other"}')
 async def test_pending_stop_timeout_no_probe(self):
  self.cancel_hook=lambda:setattr(self.stop,'exit',None)
  self.assert_blocked_untouched(await self.run_call(),True);self.assertGreaterEqual(self.clock,1)
 async def test_nonzero_exit_no_probe(self):
  self.cancel_hook=lambda:setattr(self.stop,'exit',9);self.assert_blocked_untouched(await self.run_call(),True)
 async def test_main_changed_after_cancel_no_probe(self):
  self.cancel_hook=lambda:self.main.data.update(creation_filetime=self.main.data['creation_filetime']+1)
  self.assert_blocked_untouched(await self.run_call(),True)
 async def test_wizard_still_visible_or_modal_no_probe(self):
  for cls in ['TPCBImportWizard','TShowException','#32770']:
   self.main=Process(MAIN,self.events);self.stop=Process(STOP,self.events)
   self.env['process_windows'][MAIN]=[{'class':'TDocumentForm'},{'class':cls}]
   self.assert_blocked_untouched(await self.run_call(),True)
 async def test_wrong_foreground_or_extra_instance_no_probe(self):
  for patchvalue in [{'foreground_confirmed':False},{'configured_process_ids':[MAIN,66]}]:
   self.main=Process(MAIN,self.events);self.stop=Process(STOP,self.events);saved=dict(self.env);self.env.update(patchvalue)
   self.assert_blocked_untouched(await self.run_call(),True);self.env=saved
 async def test_wrong_probe_token_is_not_recovered(self):
  self.probe_wrong=True;res=await self.run_call();self.assertTrue(res['blocked']);self.assertEqual(len(self.launches()),1)
  self.assertEqual(r.blocked_state(self.root)['phase'],'probe_token_mismatch')
 async def test_probe_launcher_pending_is_not_recovered(self):
  self.probe_exit=None;res=await self.run_call();self.assertTrue(res['blocked']);self.assertEqual(len(self.launches()),1)
  self.assertEqual(r.blocked_state(self.root)['phase'],'probe_pending')
 async def test_replay_after_success_never_second_probe(self):
  self.assertTrue((await self.run_call())['recovered']);self.assertTrue((await self.run_call())['blocked']);self.assertEqual(len(self.launches()),1)
 async def test_cancelled_operation_preserves_latch_and_closes_handles(self):
  def cancel(*a):raise asyncio.CancelledError()
  self.cancel=cancel
  with self.assertRaises(asyncio.CancelledError):await self.run_call()
  self.assertEqual(m._read_latch(self.root),self.original);self.assertTrue(self.main.closed and self.stop.closed);self.assertEqual(self.launches(),[])
 async def test_default_recovery_still_refuses_old_latch(self):
  res=await r._recover_emergency_serialized(EXE,self.root,1,1,False);self.assertTrue(res['blocked']);self.assertEqual(self.launches(),[])
 async def test_public_dict_cannot_be_internal_proof(self):
  res=await r._recover_emergency_serialized(EXE,self.root,1,1,False,verified_stop={'exit_code':0});self.assertTrue(res['blocked']);self.assertEqual(self.launches(),[])
 async def test_proof_reuse_rejected(self):
  self.stop.exit=0
  proof=m._VerifiedPendingStop(EXE,self.root,self.original,self.stop,self.main,self.stop.identity(),self.main.identity(),self.command,True)
  proof.consume(EXE,self.root,self.state,self.env)
  with self.assertRaisesRegex(ValueError,'consumed'):proof.consume(EXE,self.root,self.state,self.env)

class PublicTransportTests(unittest.IsolatedAsyncioTestCase):
 @unittest.skipUnless(importlib.util.find_spec("mcp"), "MCP SDK is not installed in this interpreter")
 async def test_public_schema_locks_and_mock_dispatch(self):
  import ast
  from contextlib import contextmanager
  from mcp.server.fastmcp import FastMCP, Context
  lock=asyncio.Lock();events=[];claimed=False
  @contextmanager
  def claim(root):
   nonlocal claimed
   self.assertTrue(lock.locked());claimed=True;events.append('claim')
   try:yield
   finally:claimed=False;events.append('release')
  async def fake(*args):
   self.assertTrue(lock.locked());self.assertTrue(claimed);events.append('body')
   self.assertEqual(args[2:5],(MAIN,98765,'observation-token'))
   return {'recovered':False,'blocked':True,'mock_only':True}
  tree=ast.parse((ROOT/'main.py').read_text(encoding='utf-8'))
  node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='cancel_import_wizard_and_resume_pending_stop')
  node.decorator_list=[]
  namespace={'Context':Context,'json':json,'EXCHANGE_DIR':Path('mock-exchange'),
    'altium_bridge':SimpleNamespace(_command_lock=lock,config=SimpleNamespace(altium_exe_path=EXE))}
  exec(compile(ast.Module(body=[node],type_ignores=[]),'combined_public_test','exec'),namespace)
  server=FastMCP('mock-pending-stop-transport')
  server.add_tool(namespace['cancel_import_wizard_and_resume_pending_stop'])
  tools=await server.list_tools();properties=tools[0].inputSchema['properties']
  self.assertEqual(properties['wizard_hwnd']['type'],'integer');self.assertNotIn('verified_stop',properties)
  with patch.object(em,'emergency_claim',side_effect=claim),patch.object(m,'cancel_and_resume_locked',side_effect=fake):
   result=await server.call_tool('cancel_import_wizard_and_resume_pending_stop',
      {'target_pid':MAIN,'wizard_hwnd':98765,'observation_token':'observation-token','acknowledge_managed_wizard':True})
  self.assertTrue(json.loads(result[0].text)['mock_only']);self.assertEqual(events,['claim','body','release'])

if __name__=='__main__':unittest.main()
