"""Offline sandbox failure behavior through the real conservative classifier."""
import ast
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import script_guard

ROOT = Path(__file__).resolve().parents[1]


class SandboxGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()

    @classmethod
    def tearDownClass(cls):
        cls.loop.close()

    def run_case(self, text, *, title="Error", buttons=("OK",), ambiguous=False,
                 window_class="TMessageForm", crash_after_launch=False):
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        method = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                      and n.name == "_run_altium_script_locked")
        clock = SimpleNamespace(now=0)
        async def sleep(seconds): clock.now += seconds
        exe = os.path.abspath("X2.EXE")
        windows = [1, 2] if ambiguous else [1]
        user32 = SimpleNamespace(PostMessageW=Mock())
        native_dialogs = SimpleNamespace(_user32=user32, is_visible=lambda hwnd: True,
            top_windows=lambda: [] if crash_after_launch and not popen.call_count else windows,
            window_text=lambda hwnd: title if hwnd == 1 else "Altium",
            window_class=lambda hwnd: window_class if hwnd == 1 else "TMainForm",
            children=lambda hwnd: [(11, "TStaticText", text)] +
                [(20 + i, "TButton", button) for i, button in enumerate(buttons)])
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            pas, project = folder / "Sandbox.pas", folder / "Sandbox.PrjScr"
            pas.write_text("placeholder source", encoding="utf-8")
            project.write_text("placeholder project", encoding="utf-8")
            log = folder / "log.txt"
            def launch(*args, **kwargs):
                if "Runtime error" in text:
                    log.write_text("sandbox start\nbefore risky statement\n", encoding="utf-8")
            popen = Mock(side_effect=launch)
            broad = Mock(side_effect=AssertionError("broad dismissal must never run"))
            environment = dict(json=json, logger=Mock(), Path=Path,
                SANDBOX_PAS=pas, SANDBOX_PRJ=project, SANDBOX_DIR=folder,
                SANDBOX_LOG=log, SANDBOX_RESULT=folder / "result.json",
                SANDBOX_DECL_BEGIN="begin_decl", SANDBOX_DECL_END="end_decl",
                SANDBOX_BEGIN="begin", SANDBOX_END="end",
                _inject_between=lambda source, *args: source,
                _dismiss_altium_dialogs=broad, script_guard=script_guard,
                subprocess=SimpleNamespace(Popen=popen, DEVNULL=-3),
                time=SimpleNamespace(time=lambda: clock.now),
                asyncio=SimpleNamespace(sleep=sleep),
                altium_bridge=SimpleNamespace(config=SimpleNamespace(altium_exe_path=exe)))
            exec(compile(ast.Module(body=[method], type_ignores=[]), "sandbox_guard_test", "exec"), environment)
            with patch.dict(sys.modules, {"dialogs": native_dialogs}), \
                 patch.object(script_guard, "_window_process", side_effect=lambda hwnd: (hwnd, exe)), \
                 patch.object(script_guard, "stop_script_executor") as stop:
                result = json.loads(self.loop.run_until_complete(environment["_run_altium_script_locked"](
                    None, "anything", 12, "", "")))
        broad.assert_not_called()
        stop.assert_not_called()
        self.assertEqual(popen.call_count, 0 if window_class == "TShowException" and not crash_after_launch else 1)
        return result, user32.PostMessageW, clock.now

    def test_save_confirm_warning_and_general_error_remain_open(self):
        cases = [
            ("Save changes?", "Confirm", ("Yes", "No", "Cancel")),
            ("Undeclared identifier: X", "Error", ("OK", "Cancel")),
            ("Undeclared identifier: X", "Warning", ("OK",)),
            ("File Error - Failed to load Library.PcbLib", "Error", ("OK",)),
        ]
        for text, title, buttons in cases:
            with self.subTest(text=text, title=title):
                result, close, elapsed = self.run_case(text, title=title, buttons=buttons)
                close.assert_not_called()
                self.assertFalse(result["success"])
                self.assertEqual(elapsed, 12)
                self.assertEqual(result["dialogs_dismissed"], 0)

    def test_preexisting_native_crash_blocks_launch_and_is_not_closed(self):
        text = "Access violation at address 00000001D8F79C54 in module ScriptingSystem.DLL"
        result, close, elapsed = self.run_case(text, window_class="TShowException",
                                              buttons=("Send Report", "Close"))
        self.assertEqual(result["script_errors"][0]["text"], text)
        self.assertEqual(result["script_errors"][0]["kind"], "native_crash")
        self.assertEqual(result["mutation_state"], "not_dispatched")
        self.assertEqual(elapsed, 0)
        close.assert_not_called()

    def test_native_crash_after_launch_is_captured_without_closing_reporter(self):
        text = "Access violation at address 00000001D8F79C54. Read of address 0000000000000078"
        result, close, elapsed = self.run_case(text, window_class="TShowException",
            buttons=("Send Report", "Close"), crash_after_launch=True)
        self.assertEqual(result["script_errors"][0]["text"], text)
        self.assertEqual(result["script_errors"][0]["kind"], "native_crash")
        self.assertEqual(result["dialogs_dismissed"], 0)
        self.assertEqual(elapsed, 2)
        close.assert_not_called()
        self.assertFalse(result["retry_performed"])

    def test_compile_error_captured_and_only_exact_dialog_closed_without_stop(self):
        result, close, elapsed = self.run_case("Undeclared identifier: SomeFunction")
        self.assertEqual(elapsed, 2)
        self.assertEqual(result["script_errors"][0]["kind"], "compile")
        self.assertEqual(result["dialogs_dismissed"], 1)
        close.assert_called_once_with(1, 0x0010, 0, 0)
        self.assertFalse(result["executor_stop_dispatched"])
        self.assertFalse(result["retry_performed"])
        self.assertTrue(result["requires_recovery_probe"])
        self.assertEqual(result["mutation_state"], "unknown")

    def test_runtime_script_error_retains_last_step(self):
        result, close, elapsed = self.run_case("Runtime error in script: invalid variant operation")
        self.assertEqual(elapsed, 2)
        self.assertEqual(result["script_errors"][0]["kind"], "runtime")
        self.assertEqual(result["last_step_reached"], "before risky statement")
        close.assert_called_once()
        self.assertFalse(result["executor_recovered"])

    def test_ambiguous_instances_capture_error_without_closing_dialog(self):
        result, close, elapsed = self.run_case("Undeclared identifier: X", ambiguous=True)
        self.assertEqual(elapsed, 2)
        self.assertFalse(result["success"])
        self.assertFalse(result["script_errors"][0]["safe_to_stop"])
        close.assert_not_called()
