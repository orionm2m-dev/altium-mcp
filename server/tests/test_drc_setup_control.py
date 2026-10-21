"""DRC setup inventory policy and public tool registration; no native calls."""
import ast
import copy
import asyncio
import ctypes
from contextlib import contextmanager
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import drc_setup_control as control
TITLE = "ScratchQualification.PcbDoc - Altium Designer"

class DrcInspectTests(unittest.TestCase):
    def setUp(self):
        self.main={"hwnd":100,"class":"TDocumentForm","title":TITLE,"enabled":False}
        self.target=Mock(hwnd=42,identity={"pid":123,"creation_filetime":456},
            row={"class":"TDesignRuleCheckForm","title":"Design Rule Checker [mil]","enabled":True},
            window={"handle":42,"title":"Design Rule Checker [mil]"})
        self.target.api.c=ctypes
        self.value="500"
        self.target.api.windows.side_effect=lambda pid:[copy.deepcopy(self.main)]
        self.target.api.matches.return_value=True
        self.target.api.title.side_effect=lambda hwnd:self.main["title"]
        self.rows={11:{"pid":123,"thread":7,"class":"TXPSpinEdit","text":"","visible":True,"enabled":True,"style":0},
                   12:{"pid":123,"thread":7,"class":"TCheckBox","text":"Create Report File","visible":True,"enabled":True,"style":3},
                   13:{"pid":999,"thread":8,"class":"WebView","text":"","visible":True,"enabled":True,"style":0}}
        self.gui=Mock()
        self.gui.EnumChildWindows.side_effect=lambda hwnd,callback,data:[callback(h,data) for h in self.rows]
        self.gui.IsChild.return_value=True
        self.gui.GetClassName.side_effect=lambda hwnd:self.rows[hwnd]["class"]
        self.gui.GetWindowText.side_effect=lambda hwnd:self.rows[hwnd]["text"]
        self.gui.IsWindowVisible.side_effect=lambda hwnd:self.rows[hwnd]["visible"]
        self.gui.IsWindowEnabled.side_effect=lambda hwnd:self.rows[hwnd]["enabled"]
        self.gui.GetWindowLong.side_effect=lambda hwnd,index:self.rows[hwnd]["style"]
        self.gui.GetParent.return_value=42
        self.gui.GetWindowRect.return_value=(0,0,100,20)
        self.process=SimpleNamespace(GetWindowThreadProcessId=lambda hwnd:(self.rows[hwnd]["thread"],self.rows[hwnd]["pid"]))
        for p in (patch.object(control,"CaptureTarget",return_value=self.target),patch.object(control,"win32gui",self.gui),patch.object(control,"win32process",self.process),patch.object(control,"_message",side_effect=self.message),patch.object(control,"_numeric_text",side_effect=lambda target,hwnd:self.value)):
            p.start();self.addCleanup(p.stop)
    def message(self, target, hwnd, msg, wp=0, lp=0):
        if msg==0xC:self.value=ctypes.wstring_at(lp)
        return 1
    def call(self,action="inspect",title=TITLE,**kwargs):
        return control.control("configured.exe",action,42,title,**kwargs)
    def set_limit(self,expected=500,new=5000,hwnd=11):
        return self.call("set_limit",edit_hwnd=hwnd,expected_limit=expected,new_limit=new)
    def add_button(self,caption="OK"):
        self.rows[14]={"pid":123,"thread":7,"class":"TXPBitBtn","text":caption,"visible":True,"enabled":True,"style":0}
        self.gui.GetClientRect.return_value=(0,0,100,20)

    def test_inventory_checks_supported_checkbox_and_skips_foreign(self):
        r=self.call();self.assertTrue(r["success"],r)
        self.assertEqual([c["hwnd"] for c in r["controls"]],[11,12]);self.assertEqual(r["controls"][1]["check_state"],1)
        self.assertEqual(r["foreign_descendants_skipped"],1);self.assertTrue(r["no_input"])
        self.gui.PostMessage.assert_not_called();self.target.close.assert_called_once()
    def test_mutation_action_refused(self):
        self.assertTrue(self.call("run")["blocked"]);control.CaptureTarget.assert_not_called()
    def test_wrong_dialog_class_refused(self):
        self.target.row["class"]="TMessageForm";self.assertTrue(self.call()["blocked"])
    def test_wrong_dialog_title_refused(self):
        self.target.row["title"]="Save";self.assertTrue(self.call()["blocked"])
    def test_wrong_main_title_refused(self):
        self.assertTrue(self.call(title="Other.PcbDoc - Altium Designer")["blocked"])
    def test_disabled_dialog_refused(self):
        self.target.row["enabled"]=False;self.assertTrue(self.call()["blocked"])
    def test_child_identity_change_refused(self):
        self.gui.IsChild.return_value=False;self.assertTrue(self.call()["blocked"])
    def test_checkbox_timeout_recorded_without_retry(self):
        control._message.side_effect=OSError("timeout")
        r=self.call();self.assertTrue(r["success"],r);self.assertEqual(r["controls"][1]["check_state_error"],"timeout")
        control._message.assert_called_once()
    def test_non_pcb_title_refused_before_capture(self):
        self.assertTrue(self.call(title="Editor")["blocked"]);control.CaptureTarget.assert_not_called()

    def test_set_reads_expected_writes_once_and_verifies_other_controls(self):
        r=self.set_limit();self.assertTrue(r["success"],r);self.assertTrue(r["limit_readback_verified"])
        self.assertEqual(self.value,"5000");self.assertEqual(r["messages_completed"],1)
        self.assertEqual([c.args[2] for c in control._message.call_args_list].count(0xC),1)
        self.gui.PostMessage.assert_not_called()
    def test_same_value_no_input(self):
        r=self.set_limit(new=500);self.assertTrue(r["success"],r);self.assertTrue(r["no_input"])
    def test_stale_expected_wrong_hwnd_and_duplicate_edit_refuse(self):
        self.assertTrue(self.set_limit(expected=400)["blocked"])
        self.assertTrue(self.set_limit(hwnd=17)["blocked"])
        self.rows[15]=dict(self.rows[11]);self.assertTrue(self.set_limit()["blocked"])
        self.assertFalse(any(c.args[2]==0xC for c in control._message.call_args_list))
    def test_decrease_boolean_overlarge_and_missing_values_refuse(self):
        for args in ({"new":400},{"new":100001},{"new":True},{"expected":False}):
            self.assertTrue(self.set_limit(**args)["blocked"])
        self.assertTrue(self.call("set_limit")["blocked"]);control.CaptureTarget.assert_not_called()
    def test_native_rejection_not_retried(self):
        control._message.side_effect=lambda target,hwnd,msg,*args:0 if msg==0xC else 1
        r=self.set_limit();self.assertFalse(r["success"]);self.assertTrue(r["outcome_unknown"])
        self.assertEqual([c.args[2] for c in control._message.call_args_list].count(0xC),1)
    def test_unchanged_numeric_readback_rejected(self):
        control._message.side_effect=lambda *args:1
        r=self.set_limit();self.assertFalse(r["success"]);self.assertFalse(r["limit_readback_verified"])
    def test_other_control_change_rejected_after_write(self):
        def change(*args):
            out=self.message(*args)
            if args[2]==0xC:self.rows[12]["text"]="changed"
            return out
        control._message.side_effect=change
        r=self.set_limit();self.assertFalse(r["success"]);self.assertTrue(r["outcome_unknown"])
    def test_ok_exact_button_and_expected_limit(self):
        self.add_button();r=self.call("ok",edit_hwnd=11,expected_limit=500,button_hwnd=14)
        self.assertTrue(r["success"],r);self.assertEqual(self.gui.PostMessage.call_count,2)
        self.assertEqual([c.args[1] for c in self.gui.PostMessage.call_args_list],[0x201,0x202])
        self.assertFalse(r["completion_verified"]);self.assertFalse(r["drc_run_requested"])
    def test_ok_wrong_limit_refuses_before_input(self):
        self.add_button();self.assertTrue(self.call("ok",edit_hwnd=11,expected_limit=5000,button_hwnd=14)["blocked"])
        self.gui.PostMessage.assert_not_called()
    def test_cancel_does_not_require_edit_and_rejects_run_button(self):
        self.add_button("Run Design Rule Check");self.assertTrue(self.call("cancel",button_hwnd=14)["blocked"])
        self.rows[14]["text"]="Cancel";self.assertTrue(self.call("cancel",button_hwnd=14)["success"])
    def test_change_after_button_down_refuses_up(self):
        self.add_button();self.gui.PostMessage.side_effect=lambda *args:self.rows[14].update(text="Changed")
        r=self.call("ok",edit_hwnd=11,expected_limit=500,button_hwnd=14)
        self.assertFalse(r["success"]);self.assertTrue(r["partial_dispatch"]);self.gui.PostMessage.assert_called_once()
    def test_numeric_text_is_bounded_read_only_and_validates_content(self):
        # Load a second module to exercise the real reader, not the fixture mock.
        import importlib.util
        spec=importlib.util.spec_from_file_location("drc_numeric_test",Path(control.__file__))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        def read(target,hwnd,msg,wp,lp):
            self.assertEqual((hwnd,msg,wp),(11,0xD,64))
            ctypes.memmove(lp,ctypes.create_unicode_buffer("500"),4*ctypes.sizeof(ctypes.c_wchar));return 3
        with patch.object(module,"_message",side_effect=read) as call:
            self.assertEqual(module._numeric_text(self.target,11),"500");call.assert_called_once()

class PublicTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_public_inventory_and_mutation_locks(self):
        tree=ast.parse((Path(__file__).resolve().parents[1]/"main.py").read_text("utf-8"))
        node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=="control_drc_setup")
        self.assertEqual(ast.unparse(node.decorator_list[0]),"mcp.tool()")
        self.assertEqual([a.arg for a in node.args.args],["ctx","action","target_hwnd","expected_main_title","edit_hwnd","expected_limit","new_limit","button_hwnd"])
        node.decorator_list=[];lock=asyncio.Lock();flags={"claimed":False}
        @contextmanager
        def claim(exchange):
            self.assertTrue(lock.locked());flags["claimed"]=True
            try:yield
            finally:flags["claimed"]=False
        env=dict(Context=object,json=json,EXCHANGE_DIR="exchange",emergency_stop=SimpleNamespace(emergency_claim=claim),altium_bridge=SimpleNamespace(_command_lock=lock,config=SimpleNamespace(altium_exe_path="configured.exe")))
        exec(compile(ast.Module(body=[node],type_ignores=[]),"public_drc","exec"),env)
        def service(*args):
            self.assertEqual(lock.locked(),args[1]!="inspect");self.assertEqual(flags["claimed"],args[1]!="inspect")
            return {"success":True,"no_input":args[1]=="inspect"}
        with patch.object(control,"control",side_effect=service) as call:
            self.assertTrue(json.loads(await env["control_drc_setup"](None,"inspect",42,TITLE))["no_input"])
            await env["control_drc_setup"](None,"set_limit",42,TITLE,11,500,5000)
            call.assert_called_with("configured.exe","set_limit",42,TITLE,11,500,5000,None)

if __name__=="__main__":unittest.main()
