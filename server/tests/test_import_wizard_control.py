"""Offline policy/store/native-adapter-shim tests; no native APIs are invoked."""
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import import_wizard_control as m

EXE=r"C:\Altium\X2.EXE"
PID=1234
HWND="54321"
FILETIME="134369493464722166"

def fixture():
    return {"identity":{"pid":PID,"creation_filetime":FILETIME,"exe":EXE,"running":True,"same_user":True},
            "window":{"hwnd":HWND,"pid":PID,"class":m.WIZARD_CLASS,"title":m.WIZARD_TITLE,"visible":True,"enabled":True,"owner_hwnd":"22","rect":[0,0,500,400]},
            "buttons":[{"hwnd":"44","pid":PID,"class":"TXPBitBtn","caption":"&Cancel","visible":True,"enabled":True,"descendant":True,"client_rect":[0,0,80,22]}],
            "blocking_dialogs":[]}

class Adapter:
    def __init__(self):
        self.current=fixture(); self.calls=[]; self.error=None
        self.after={"same_process_alive":True,"wizard_visible":False}
    def snapshot(self,pid,hwnd):
        self.calls.append("snapshot"); return copy.deepcopy(self.current)
    def cancel_once(self,snapshot,button):
        self.calls.append("cancel")
        if self.error: raise self.error
    def observe_after(self,snapshot):
        self.calls.append("after"); return copy.deepcopy(self.after)

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent,prefix=".wizard_tests_")
        self.adapter=Adapter(); self.latch=b'{"phase":"stop_pending"}'; self.now=1000
        self.store=m.ObservationStore(self.tmp.name)
        self.control=m.WizardControl(self.adapter,EXE,lambda:self.latch,self.store,lambda:self.now)
    def tearDown(self): self.tmp.cleanup()
    def inspect(self):
        r=self.control.inspect(PID,HWND); self.assertTrue(r["success"],r); return r["observation_token"]
    def cancel(self,token,**kw):
        return self.control.cancel(PID,HWND,token,acknowledge_managed_wizard=kw.get("ack",True))
    def test_success_and_exact_filetime_and_no_latch_write(self):
        token=self.inspect(); r=self.cancel(token)
        self.assertTrue(r["success"]); self.assertTrue(r["recovery_latch_unchanged"])
        self.assertEqual(self.adapter.current["identity"]["creation_filetime"],FILETIME)
        for key in ("script_dispatched","executor_recovered","latch_cleared","save_performed","process_termination_requested","retry_performed"):
            self.assertIs(r[key],False)
        self.assertEqual(r["wizard_source"],"unknown"); self.assertEqual(self.adapter.calls.count("cancel"),1)
    def test_persistent_cross_service(self):
        token=self.inspect()
        other=m.WizardControl(self.adapter,EXE,lambda:self.latch,m.ObservationStore(self.tmp.name),lambda:self.now)
        self.assertTrue(other.cancel(PID,HWND,token,True)["success"])
    def test_token_not_persisted_plaintext(self):
        token=self.inspect(); raw=self.store.path.read_text()
        self.assertNotIn(token,raw); self.assertIn(FILETIME,raw)
    def test_replay(self):
        token=self.inspect(); self.cancel(token); r=self.cancel(token)
        self.assertTrue(r["blocked"]); self.assertEqual(self.adapter.calls.count("cancel"),1)
    def test_new_inspect_supersedes_old(self):
        old=self.inspect(); new=self.inspect()
        self.assertTrue(self.cancel(old)["blocked"]); self.assertTrue(self.cancel(new)["success"])
    def test_expiry_and_clock_reversal(self):
        for now in (1121,999):
            self.now=1000; token=self.inspect(); self.now=now
            self.assertTrue(self.cancel(token)["blocked"])
        self.assertNotIn("cancel",self.adapter.calls)
    def test_missing_ack_consumes(self):
        token=self.inspect(); self.assertTrue(self.cancel(token,ack=False)["blocked"])
        self.assertTrue(self.cancel(token)["blocked"]); self.assertNotIn("cancel",self.adapter.calls)
    def test_latch_changed(self):
        token=self.inspect(); self.latch=b'changed'; self.assertTrue(self.cancel(token)["blocked"])
        self.assertNotIn("cancel",self.adapter.calls)
    def test_latch_changed_immediately_before_action(self):
        token=self.inspect(); original=self.adapter.snapshot
        def changed(*args):
            result=original(*args); self.latch=b'changed'; return result
        self.adapter.snapshot=changed
        self.assertTrue(self.cancel(token)["blocked"]); self.assertNotIn("cancel",self.adapter.calls)
    def test_tampered_payload(self):
        token=self.inspect(); envelope=json.loads(self.store.path.read_text())
        envelope["plan"]["expires"]+=9999; self.store.path.write_text(json.dumps(envelope))
        self.assertTrue(self.cancel(token)["blocked"]); self.assertFalse(self.store.path.exists())
        self.assertNotIn("cancel",self.adapter.calls)
    def test_pid_reuse_and_other_snapshot_change(self):
        changes=[("identity","creation_filetime","134369493464722167"),("window","rect",[1,0,500,400]),("buttons",None,None)]
        for group,key,value in changes:
            self.adapter.current=fixture(); token=self.inspect()
            if group=="buttons": self.adapter.current["buttons"][0]["client_rect"]=[0,0,90,22]
            else:self.adapter.current[group][key]=value
            self.assertTrue(self.cancel(token)["blocked"])
        self.assertNotIn("cancel",self.adapter.calls)
    def test_wrong_target_parameters(self):
        token=self.inspect(); r=self.control.cancel(PID+1,HWND,token,True)
        self.assertTrue(r["blocked"]); self.assertNotIn("cancel",self.adapter.calls)
    def test_inspect_rejects_invalid_identity_window_button(self):
        edits=[("identity","exe",r"C:\Other\X2.EXE"),("identity","pid",1),("identity","running",False),
               ("identity","same_user",False),("identity","creation_filetime",134369493464722166),
               ("window","class","#32770"),("window","title","Save"),("window","visible",False),
               ("window","enabled",False),("window","hwnd","12"),("window","pid",2)]
        for group,key,value in edits:
            self.adapter.current=fixture(); self.adapter.current[group][key]=value
            self.assertFalse(self.control.inspect(PID,HWND)["cancel_available"],(group,key))
        for key,value in [("enabled",False),("visible",False),("pid",2),("descendant",False),("class","Edit"),("caption","Next"),("client_rect",[0,0,0,22])]:
            self.adapter.current=fixture(); self.adapter.current["buttons"][0][key]=value
            self.assertFalse(self.control.inspect(PID,HWND)["cancel_available"],key)
        self.assertNotIn("cancel",self.adapter.calls)
    def test_duplicate_button_and_other_modal(self):
        self.adapter.current["buttons"]*=2; self.assertFalse(self.control.inspect(PID,HWND)["success"])
        self.adapter.current=fixture();self.adapter.current["blocking_dialogs"]=[{"hwnd":"55","class":"#32770"}]
        self.assertFalse(self.control.inspect(PID,HWND)["success"])
    def test_timeout_unknown_once(self):
        token=self.inspect(); self.adapter.error=TimeoutError("timeout")
        r=self.cancel(token);self.assertTrue(r["outcome_unknown"]);self.assertFalse(r["wizard_closed_verified"])
        self.cancel(token);self.assertEqual(self.adapter.calls.count("cancel"),1)
    def test_closed_with_process_exit_not_success(self):
        token=self.inspect();self.adapter.after={"same_process_alive":False,"wizard_visible":False}
        self.assertFalse(self.cancel(token)["success"])
    def test_still_visible_not_success(self):
        token=self.inspect();self.adapter.after["wizard_visible"]=True
        self.assertFalse(self.cancel(token)["success"]);self.assertEqual(self.adapter.calls.count("cancel"),1)
    def test_after_latch_change_reported_not_recovered(self):
        token=self.inspect(); original=self.adapter.observe_after
        def changed(*args):self.latch=b'changed';return original(*args)
        self.adapter.observe_after=changed;r=self.cancel(token)
        self.assertFalse(r["recovery_latch_unchanged"]);self.assertFalse(r["executor_recovered"])

class CallbackTests(unittest.TestCase):
    def adapter(self,child_error=False,top_error=False):
        # Bypass __init__: every native surface is a pure local test double.
        a=object.__new__(m.NativeAdapter)
        class Process:
            def __enter__(self):return self
            def __exit__(self,*args):pass
        a.process=lambda pid:Process();a._identity=lambda process:fixture()["identity"]
        a.enum_type=lambda fn:fn;a._pid=lambda hwnd:PID
        a._class=lambda hwnd:m.WIZARD_CLASS if int(hwnd)==int(HWND) else "TXPBitBtn"
        a._text=lambda hwnd:m.WIZARD_TITLE if int(hwnd)==int(HWND) else "Cancel"
        def rect(hwnd,client=False):
            if child_error and client:raise OSError("test callback failure")
            return [0,0,80,22]
        a._rect=rect
        def enum_children(hwnd,callback,data):callback(44,0);return 1
        def enum_top(callback,data):
            if top_error:
                a._class=lambda hwnd:(_ for _ in ()).throw(OSError("test top callback failure"))
                callback(55,0)
            return 1
        a.u=SimpleNamespace(IsWindow=lambda h:True,IsWindowVisible=lambda h:True,IsWindowEnabled=lambda h:True,
                            IsChild=lambda h,c:True,GetWindow=lambda h,f:22,EnumChildWindows=enum_children,EnumWindows=enum_top)
        return a
    def test_child_callback_failure_does_not_issue_partial_snapshot(self):
        with self.assertRaisesRegex(OSError,"Incomplete child"):
            self.adapter(child_error=True).snapshot(PID,HWND)
    def test_top_callback_failure_does_not_issue_partial_snapshot(self):
        with self.assertRaisesRegex(OSError,"Incomplete top"):
            self.adapter(top_error=True).snapshot(PID,HWND)
    def test_mocked_snapshot_without_native(self):
        self.assertEqual(self.adapter().snapshot(PID,HWND)["identity"]["creation_filetime"],FILETIME)


class PublicWrapperTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import ast
        import asyncio
        from contextlib import contextmanager
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent,prefix=".wizard_wrapper_tests_")
        self.addCleanup(self.tmp.cleanup)
        self.lock=asyncio.Lock();self.claimed=False;self.claim_busy=False;self.adapter=Adapter()
        self.events=[]
        @contextmanager
        def claim(root):
            if self.claim_busy:raise RuntimeError("claim busy")
            if not self.lock.locked():raise AssertionError("bridge lock missing")
            self.claimed=True;self.events.append("claim")
            try:yield
            finally:self.claimed=False;self.events.append("release")
        def native_adapter():
            if not self.claimed:raise AssertionError("native access outside claim")
            self.events.append("adapter");return self.adapter
        surface=SimpleNamespace(NativeAdapter=native_adapter,WizardControl=m.WizardControl,
                                ObservationStore=m.ObservationStore,latch_hash=m.latch_hash)
        source=Path(__file__).resolve().parents[1]/"main.py"
        tree=ast.parse(source.read_text(encoding="utf-8"))
        node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=="control_import_wizard")
        node.decorator_list=[]
        namespace={"Context":object,"Path":Path,"json":json,"EXCHANGE_DIR":self.tmp.name,
                   "altium_bridge":SimpleNamespace(_command_lock=self.lock,config=SimpleNamespace(altium_exe_path=EXE)),
                   "emergency_stop":SimpleNamespace(emergency_claim=claim),"import_wizard_control":surface}
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),"exec"),namespace)
        self.call=namespace["control_import_wizard"]
    async def test_public_inspect_cancel_locked_and_persistent(self):
        result=json.loads(await self.call(None,"inspect",PID,int(HWND)))
        self.assertTrue(result["success"]);self.assertEqual(self.events,["claim","adapter","release"])
        result=json.loads(await self.call(None,"cancel",PID,int(HWND),result["observation_token"],True))
        self.assertTrue(result["wizard_closed_verified"]);self.assertTrue(result["recovery_latch_unchanged"])
        self.assertFalse(Path(self.tmp.name,"script_recovery_state.json").exists())
    async def test_public_invalid_action_does_not_construct_adapter(self):
        result=json.loads(await self.call(None,"save",PID,int(HWND)))
        self.assertTrue(result["blocked"]);self.assertNotIn("adapter",self.events)
    async def test_public_claim_busy_does_not_construct_adapter(self):
        self.claim_busy=True
        result=json.loads(await self.call(None,"inspect",PID,int(HWND)))
        self.assertTrue(result["blocked"]);self.assertEqual(self.events,[])

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "MCP SDK is not installed in this interpreter")
    async def test_fastmcp_integer_handle_schema_and_call(self):
        from mcp.server.fastmcp import FastMCP, Context
        self.call.__annotations__["ctx"] = Context
        server = FastMCP("mock-wizard-transport")
        server.add_tool(self.call)
        tools = await server.list_tools()
        self.assertEqual(tools[0].inputSchema["properties"]["target_hwnd"]["type"], "integer")
        result = await server.call_tool("control_import_wizard", {
            "action": "inspect", "target_pid": PID, "target_hwnd": int(HWND)})
        outcome = json.loads(result[0].text)
        self.assertTrue(outcome["success"], outcome)
        self.assertEqual(outcome["snapshot"]["window"]["hwnd"], HWND)
        self.assertNotIn("cancel", self.adapter.calls)

if __name__=="__main__": unittest.main(verbosity=2)
