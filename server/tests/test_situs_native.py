import asyncio
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from situs_lifecycle import build_start_request
from situs_native import build_native_program, read_native_receipt, process_plan, SitusNativeAdapter

BINDING = dict(pid=123, creation_time=987654321, exe_path=r'C:\Altium\X2.EXE', pcb_path=r"C:\boards\O'Brien.PcbDoc")


def make_job(scope='all', targets=None):
    return dict(job_id='a'*32, start_operation_id='b'*32,
                request=build_start_request(BINDING, hashlib.sha256(b'pcb').hexdigest(), scope, targets))


def receipt(job, phase='returned_identity_verified'):
    return dict(schema_version=1, job_id=job['job_id'], operation_id=job['start_operation_id'],
                pcb_path=job['request']['binding']['pcb_path'], no_save=True,
                pid_verified_by_native=False, completion_qualified=False, phase=phase, error='')


class NativeProgramTests(unittest.TestCase):
    def test_whole_board_exact_guards_no_save_and_no_replay(self):
        source = build_native_program(make_job(), 'b'*32, r'C:\jobs\receipt.json')
        self.assertIn("o''brien.pcbdoc", source)
        self.assertIn('if FileExists(', source)
        self.assertIn('Doc.FileName, ExpectedPath', source)
        self.assertIn('Board.I_ObjectAddress <> CurrentBoard.I_ObjectAddress', source)
        self.assertIn('Module.ProcessControl.ProcessDepth <> 0', source)
        self.assertEqual(source.count("RunProcess('PCB:Autoroute')"), 1)
        self.assertNotIn('SaveDocument', source)
        self.assertNotIn('DM_Save', source)
        self.assertNotIn('ScriptingSystem:Stop', source)
        self.assertIn('completion_qualified', source)
        self.assertIn('pid_verified_by_native', source)
        self.assertIn("'returned_identity_verified'", source)

    def test_exact_selected_set_verified_without_selection_mutation(self):
        source = build_native_program(make_job('selected_components', ['U1', "TP'2"]), 'b'*32, r'C:\jobs\receipt.json')
        self.assertIn("Wanted.Add('TP''2')", source)
        self.assertIn('SelectedCount <> Wanted.Count', source)
        self.assertIn('Wanted.IndexOf(Component.Name.Text)', source)
        self.assertNotIn('Selected :=', source)
        self.assertIn("'OnSelectedComponents'", source)

    def test_interactive_scopes_are_explicit_not_guessed_name_parameters(self):
        plan = process_plan('net_class')
        self.assertTrue(plan['interaction_required'])
        self.assertEqual(plan['parameters'], {'Action': 'NetClass'})
        with self.assertRaisesRegex(ValueError, 'interactive target driver'):
            build_native_program(make_job('net', ['SCK']), 'b'*32, r'C:\jobs\receipt.json')

    def test_malicious_paths_and_operation_mismatch_rejected(self):
        for value in [r'..\receipt.json', 'C:\\jobs\\evil\nRunProcess.json']:
            with self.assertRaises(ValueError):
                build_native_program(make_job(), 'b'*32, value)
        with self.assertRaises(ValueError):
            build_native_program(make_job(), 'c'*32, r'C:\jobs\receipt.json')

    def test_receipt_missing_partial_mismatch_and_no_completion_promotion(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'receipt.json'
            job = make_job()
            self.assertFalse(read_native_receipt(path, job, 'b'*32)['router_returned'])
            path.write_text('{', encoding='utf-8')
            with self.assertRaises(ValueError):
                read_native_receipt(path, job, 'b'*32)
            for patch_value in ({'job_id': 'c'*32}, {'completion_qualified': True}, {'schema_version': True}):
                path.write_text(json.dumps(dict(receipt(job), **patch_value)), encoding='utf-8')
                with self.assertRaises(ValueError):
                    read_native_receipt(path, job, 'b'*32)
            path.write_text(json.dumps(receipt(job)), encoding='utf-8')
            value = read_native_receipt(path, job, 'b'*32)
            self.assertTrue(value['router_returned'])
            self.assertFalse(value['completion_qualified'])


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.calls = []
        self.binding_patch = {}
        async def identity(binding):
            self.calls.append('identity')
            return dict(binding, **self.binding_patch)
        async def execute(project, procedure, binding):
            self.calls.append('execute')
            self.assertEqual(procedure, 'SitusOperation>Run')
            self.assertTrue(Path(project).exists())
            self.assertTrue(Path(project).with_suffix('.pas').exists())
            return {'returned': True}
        async def observe(job):
            self.calls.append('observe')
            return {'router_state': 'running', 'state_verified': True}
        async def control(job, action, operation_id):
            self.calls.append('control:' + action)
            return {'input_attempted': True}
        async def preflight(source):
            self.calls.append('preflight')
            return {'success': True}
        self.adapter = SitusNativeAdapter(self.temp.name, identity=identity,
            execute_project=execute, observe_ui=observe, control_ui=control, preflight=preflight)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_identity_change_never_dispatches(self):
        self.binding_patch = {'creation_time': 1}
        with self.assertRaisesRegex(ValueError, 'identity'):
            await self.adapter.dispatch(make_job(), 'start', 'b'*32)
        self.assertNotIn('execute', self.calls)

    async def test_control_independent_and_operation_not_replayed(self):
        job = make_job()
        result = await self.adapter.dispatch(job, 'pause', 'c'*32)
        self.assertEqual(self.calls, ['identity', 'control:pause', 'identity'])
        self.assertFalse(result['completion_qualified'])
        with self.assertRaises(FileExistsError):
            await self.adapter.dispatch(job, 'pause', 'c'*32)
        self.assertEqual(self.calls.count('control:pause'), 1)

    async def test_observe_reads_receipt_but_does_not_invent_completion(self):
        job = make_job()
        directory = self.adapter.operation_directory(job, 'b'*32)
        directory.mkdir(parents=True)
        (directory / 'native.json').write_text(json.dumps(receipt(job)), encoding='utf-8')
        result = await self.adapter.observe(job)
        self.assertEqual(result['router_state'], 'running')
        self.assertTrue(result['router_returned'])
        self.assertNotIn('execute', self.calls)

    async def test_start_uses_dedicated_project_and_preflight(self):
        # A portable test emulates only the saved PCB bytes. The native receipt
        # path is a real Windows absolute path when executed on Windows.
        job = make_job()
        original = Path.read_bytes
        def read_bytes(path):
            if str(path).casefold() == job['request']['binding']['pcb_path']:
                return b'pcb'
            return original(path)
        with patch.object(Path, 'read_bytes', read_bytes):
            result = await self.adapter.dispatch(job, 'start', 'b'*32)
        self.assertEqual(self.calls, ['identity', 'preflight', 'identity', 'execute', 'identity'])
        self.assertTrue(result['no_save'])
        self.assertFalse(result['completion_qualified'])


if __name__ == '__main__':
    unittest.main()
