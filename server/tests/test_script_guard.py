"""No GUI or process starts: classifier, process contract and bridge fail-fast."""
import ast
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import script_guard

ROOT=Path(__file__).resolve().parents[1]


def dialog(text, buttons=("OK",), title="Error", cls="TMessageForm"):
    return title, cls, [(1,"TStaticText",text)]+[(i+2,"TButton",b) for i,b in enumerate(buttons)]


class ScriptDialogTests(unittest.TestCase):
    def test_precise_compiler_error_is_captured(self):
        item=script_guard.classify_script_dialog(*dialog("Undeclared identifier: SetPcbRoutingReport"))
        self.assertEqual(item["kind"],"compile")
        self.assertEqual(item["text"],"Undeclared identifier: SetPcbRoutingReport")

    def test_explicit_script_runtime_error_is_captured(self):
        item=script_guard.classify_script_dialog(*dialog("Runtime error in script: invalid variant operation"))
        self.assertEqual(item["kind"],"runtime")

    def test_general_errors_and_confirmations_are_never_dismissed(self):
        for args in (
            dialog("Do you want to save?",("Yes","No")),
            dialog("Undeclared identifier: X",("OK","Cancel")),
            dialog("File Error - Failed to load Library.PcbLib"),
            dialog("A command is currently active and save cannot be completed",("Yes","No")),
            dialog("There is another instance running, but it is busy at the moment"),
            dialog("Access violation at address 0123"),
            dialog("Undeclared identifier: X",title="Confirm"),
            dialog("Undeclared identifier: X",cls="TForm"),
            dialog("Undeclared identifier: X",buttons=()),
        ):
            with self.subTest(args=args):
                self.assertIsNone(script_guard.classify_script_dialog(*args))

    def test_native_exception_text_is_captured_but_never_dismissible(self):
        text = ("Access violation at address 00000001D8F79C54 in module "
                "'ScriptingSystem.DLL'. Read of address 0000000000000078")
        item = script_guard.classify_script_dialog(*dialog(
            text, buttons=("Send Report", "Close"), cls="TShowException"))
        self.assertEqual(item["kind"], "native_crash")
        self.assertEqual(item["text"], text)
        self.assertTrue(item["text_available"])
        self.assertFalse(item["auto_dismiss"])
        self.assertIn("send report", item["buttons"])
        failure = dict(item, hwnd=12, pid=3, safe_to_stop=True)
        user32 = SimpleNamespace(PostMessageW=Mock())
        with patch.dict(sys.modules, {"dialogs": SimpleNamespace(_user32=user32)}), \
                patch.object(script_guard, "find_script_errors", return_value=[failure]):
            self.assertEqual(script_guard.dismiss_script_errors("X2.EXE", [failure]), [])
            blocked = script_guard.blocking_crash_report("X2.EXE")
        user32.PostMessageW.assert_not_called()
        self.assertEqual(blocked["mutation_state"], "not_dispatched")
        self.assertEqual(blocked["script_errors"][0]["text"], text)

    def test_exception_reporter_with_no_readable_text_still_blocks(self):
        item = script_guard.classify_script_dialog("Error", "TShowException", [])
        self.assertEqual(item["kind"], "native_crash")
        self.assertFalse(item["text_available"])
        self.assertFalse(item["auto_dismiss"])

    def test_information_busy_instance_is_not_an_answerable_script_error(self):
        self.assertIsNone(script_guard.classify_script_dialog(*dialog(
            "There is another instance running, but it is busy at the moment",
            title="Information")))

    def test_exception_hidden_during_capture_is_not_reported_or_dismissed(self):
        native_dialogs = SimpleNamespace(top_windows=lambda: [12],
            is_visible=Mock(side_effect=[True, False]),
            window_text=lambda h: "Error", window_class=lambda h: "TShowException",
            children=lambda h: [(20, "TStaticText", "Access violation in ScriptingSystem.DLL")])
        with patch.dict(sys.modules, {"dialogs": native_dialogs}), \
                patch.object(script_guard, "_window_process", return_value=(3, str(ROOT / "X2.EXE"))):
            self.assertEqual(script_guard.find_script_errors(str(ROOT / "X2.EXE")), [])

    def test_top_window_enumeration_filters_hidden_forms(self):
        tree = ast.parse((ROOT / "dialogs.py").read_text(encoding="utf-8"))
        methods = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name in {"is_visible", "top_windows"}]
        def enumerate_windows(callback, parameter):
            for hwnd in (10, 11, 12):
                callback(hwnd, parameter)
        native = SimpleNamespace(EnumWindows=enumerate_windows,
                                 IsWindowVisible=lambda hwnd: hwnd != 11)
        environment = {"_user32": native, "_EnumWindowsProc": lambda callback: callback}
        exec(compile(ast.Module(body=methods, type_ignores=[]), "visibility_test", "exec"), environment)
        self.assertEqual(environment["top_windows"](), [10, 12])

    def test_hidden_stop_has_no_shell_no_inherited_stdio(self):
        with patch.object(script_guard.subprocess,"Popen") as popen:
            script_guard.stop_script_executor(r"C:\Altium\X2.EXE")
        args,kw=popen.call_args
        self.assertEqual(args[0],[r"C:\Altium\X2.EXE","-REditScript:Stop"])
        self.assertFalse(kw["shell"])
        for key in ("stdin","stdout","stderr"):
            self.assertEqual(kw[key],script_guard.subprocess.DEVNULL)
        self.assertIn("creationflags",kw)

    def test_revalidation_does_not_close_changed_or_ambiguous_dialog(self):
        old=dict(hwnd=12,pid=3,text="Undeclared identifier: X",safe_to_stop=True)
        fresh=[dict(old,text="Do you want to save?"),dict(old,hwnd=13,safe_to_stop=False)]
        user32=SimpleNamespace(PostMessageW=Mock())
        with patch.dict(sys.modules,{"dialogs":SimpleNamespace(_user32=user32)}):
            with patch.object(script_guard,"find_script_errors",return_value=fresh):
                self.assertEqual(script_guard.dismiss_script_errors("X2.EXE",[old]),[])
        user32.PostMessageW.assert_not_called()


class BridgeGuardTests(unittest.TestCase):
    def run_case(self, failures, closed, preexisting_crash=None):
        tree=ast.parse((ROOT/"main.py").read_text(encoding="utf-8"))
        bridge=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="AltiumBridge")
        method=next(n for n in bridge.body if isinstance(n,ast.AsyncFunctionDef) and n.name=="_execute_command_locked")
        method.decorator_list=[]
        clock=SimpleNamespace(now=0)
        async def sleep(_):clock.now+=3
        guard=SimpleNamespace(blocking_crash_report=Mock(return_value=preexisting_crash),
                              find_script_errors=Mock(return_value=failures),
                              dismiss_script_errors=Mock(side_effect=AssertionError("automatic UI dismissal forbidden")),
                              stop_script_executor=Mock())
        calls=[]
        async def run():calls.append("run");return True
        with tempfile.TemporaryDirectory() as tmp:
            env=dict(Dict=dict,Any=object,json=json,logger=Mock(),
                     RESPONSE_FILE=Path(tmp)/"response.json",REQUEST_FILE=Path(tmp)/"request.json",
                     time=SimpleNamespace(time=lambda:clock.now),asyncio=SimpleNamespace(sleep=sleep),
                     script_guard=guard)
            exec(compile(ast.Module(body=[method],type_ignores=[]),"bridge_guard_test","exec"),env)
            obj=SimpleNamespace(config=SimpleNamespace(altium_exe_path="X2.EXE"),run_altium_script=run)
            result=asyncio.run(env["_execute_command_locked"](obj,"set_pcb_routing",{},120))
        self.assertEqual(calls,[] if preexisting_crash else ["run"])
        guard.dismiss_script_errors.assert_not_called()
        return result,guard,clock.now

    def test_known_script_failure_returns_promptly_without_retry_or_success_claim(self):
        error=dict(text="Undeclared identifier: SetPcbRoutingReport",safe_to_stop=True)
        result,guard,elapsed=self.run_case([error],[error])
        self.assertLess(elapsed,120)
        self.assertEqual(result["script_errors"],[error])
        self.assertFalse(result["success"])
        self.assertFalse(result["retry_performed"])
        self.assertFalse(result["executor_recovered"])
        self.assertFalse(result["executor_stop_dispatched"])
        self.assertEqual(result["mutation_state"],"unknown")
        self.assertEqual(result["dialogs_closed"],0)
        self.assertEqual(result["dialogs_dismissed"],0)
        self.assertTrue(result["blocked"])
        self.assertTrue(result["recovery_needed"])
        self.assertTrue(result["native_error_acknowledgment_required"])
        self.assertFalse(result["automatic_ui_actions"])
        guard.stop_script_executor.assert_not_called()

    def test_native_crash_before_dispatch_never_runs_script(self):
        failure = {"success": False, "blocked": True, "mutation_state": "not_dispatched",
                   "script_errors": [{"kind": "native_crash", "text": "Access violation in ScriptingSystem.DLL"}]}
        result, guard, elapsed = self.run_case([], [], preexisting_crash=failure)
        self.assertEqual(result["script_errors"], failure["script_errors"])
        self.assertEqual(result["mutation_state"], "not_dispatched")
        self.assertEqual(elapsed, 0)
        guard.dismiss_script_errors.assert_not_called()
        guard.stop_script_executor.assert_not_called()

    def test_crash_during_dispatch_returns_exact_text_without_retry(self):
        failure = {"kind": "native_crash", "text": "Access violation at address 00000001D8F79C54 in ScriptingSystem.DLL",
                   "safe_to_stop": True, "auto_dismiss": False}
        result, guard, elapsed = self.run_case([failure], [])
        self.assertEqual(result["script_errors"], [failure])
        self.assertEqual(result["dialogs_closed"], 0)
        self.assertFalse(result["retry_performed"])
        guard.stop_script_executor.assert_not_called()

    def test_ambiguous_instances_return_error_without_stop(self):
        error=dict(text="Undeclared identifier: X",safe_to_stop=False)
        result,guard,_=self.run_case([error],[])
        self.assertFalse(result["executor_stop_dispatched"])
        guard.stop_script_executor.assert_not_called()

    def test_automatic_failure_paths_have_no_ui_dismiss_or_stop_calls(self):
        tree=ast.parse((ROOT/"main.py").read_text(encoding="utf-8"))
        bridge=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="AltiumBridge")
        methods=[next(n for n in bridge.body if isinstance(n,ast.AsyncFunctionDef) and n.name=="_execute_command_locked"),
                 next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=="_run_altium_script_locked")]
        forbidden={"dismiss_script_errors","_dismiss_altium_dialogs","PostMessageW","SendMessageW", "stop_script_executor"}
        for method in methods:
            for node in ast.walk(method):
                if isinstance(node,ast.Call):
                    name=node.func.attr if isinstance(node.func,ast.Attribute) else getattr(node.func,"id","")
                    self.assertNotIn(name,forbidden,method.name)

    def test_public_result_keeps_script_failure_evidence(self):
        tree=ast.parse((ROOT/"main.py").read_text(encoding="utf-8"))
        method=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="_script_result")
        env={"json":json}
        exec(compile(ast.Module(body=[method],type_ignores=[]),"result_guard_test","exec"),env)
        failure={"success":False,"error":"Altium script error",
                 "script_errors":[{"text":"Undeclared identifier: X"}],
                 "mutation_state":"unknown","retry_performed":False}
        result=json.loads(env["_script_result"](failure,{"last_phase":"dispatch_pending"}))
        for key,value in failure.items():self.assertEqual(result[key],value)
        self.assertEqual(result["last_phase"],"dispatch_pending")

    def test_public_recovery_uses_hidden_stop_and_probe(self):
        source=(ROOT/"main.py").read_text(encoding="utf-8")
        recovery=source.split("async def recover_script_executor(",1)[1].split("@mcp.tool()",1)[0]
        self.assertIn("async with altium_bridge._command_lock", recovery)
        self.assertIn("await script_recovery.recover", recovery)
        self.assertNotIn("await run_altium_script", recovery)
        self.assertNotIn("_dismiss_altium_dialogs", recovery)


if __name__=="__main__":unittest.main()