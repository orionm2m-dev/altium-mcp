"""DRC setup inventory policy and public tool registration; no native calls."""
import ast
import copy
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
        for p in (patch.object(control,"CaptureTarget",return_value=self.target),patch.object(control,"win32gui",self.gui),patch.object(control,"win32process",self.process),patch.object(control,"_message",return_value=1)):
            p.start();self.addCleanup(p.stop)
    def call(self,action="inspect",title=TITLE):
        return control.control("configured.exe",action,42,title)
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

class PublicTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_public_read_only_inventory(self):
        tree=ast.parse((Path(__file__).resolve().parents[1]/"main.py").read_text("utf-8"))
        node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=="control_drc_setup")
        self.assertEqual(ast.unparse(node.decorator_list[0]),"mcp.tool()")
        self.assertEqual([a.arg for a in node.args.args],["ctx","action","target_hwnd","expected_main_title"])
        node.decorator_list=[]
        env=dict(Context=object,json=json,altium_bridge=SimpleNamespace(config=SimpleNamespace(altium_exe_path="configured.exe")))
        exec(compile(ast.Module(body=[node],type_ignores=[]),"public_drc","exec"),env)
        with patch.object(control,"control",return_value={"success":True,"no_input":True}) as call:
            self.assertTrue(json.loads(await env["control_drc_setup"](None,"inspect",42,TITLE))["no_input"])
            call.assert_called_once_with("configured.exe","inspect",42,TITLE)

if __name__=="__main__":unittest.main()
