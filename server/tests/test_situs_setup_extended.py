"""Semantic Situs UIA policy tests. No native UI Automation is dispatched."""
import copy
import ctypes
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import situs_setup_extended as service

TITLE = 'Qualification.PcbDoc - Altium Designer'


def element(number=2, name='Rip-up Violations After Routing', kind='CheckBox', **states):
    patterns = []
    for key, pattern in [('toggle', 'Toggle'), ('value', 'Value'), ('range', 'RangeValue'), ('selected', 'SelectionItem'), ('expand', 'ExpandCollapse')]:
        if key in states:
            patterns.append(pattern + 'PatternIdentifiers.Pattern')
    return dict(runtime_id=[42, number], parent_runtime_id='42,1', hwnd=100 + number, pid=123,
                name=name, label='', control_type='ControlType.' + kind, enabled=True,
                offscreen=False, password=False, patterns=patterns, states=states, **{'class': 'NativeControl'})


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.identity = dict(pid=123, creation_filetime=456, exe='configured.exe')
        self.main = dict(hwnd=1, **{'class': 'TDocumentForm'}, title=TITLE)
        self.target = Mock(hwnd=42, identity=self.identity,
            row=dict(**{'class': 'TSitusSetup'}, title='Situs Routing Strategies', enabled=True))
        self.target.api.windows.side_effect = lambda pid: [copy.deepcopy(self.main)]
        self.target.api.matches.return_value = True
        self.target.api.title.side_effect = lambda hwnd: self.main['title']
        self.nodes = [element(toggle=0)]
        self.inputs = []
        self.worker = Mock(side_effect=self.simulate)
        p = patch.object(service, 'CaptureTarget', return_value=self.target)
        p.start(); self.addCleanup(p.stop)
        p = patch.object(service, '_run_worker', self.worker)
        p.start(); self.addCleanup(p.stop)

    def simulate(self, request):
        if request['operation'] != 'inspect':
            self.inputs.append(copy.deepcopy(request))
            node = next(n for n in self.nodes if n['runtime_id'] == request['runtime_id'])
            operation = request['operation']
            if operation == 'toggle':
                node['states']['toggle'] = 1 - node['states']['toggle']
            elif operation == 'value':
                node['states']['value'] = request['value']
            elif operation == 'range':
                node['states']['range']['value'] = request['value']
            elif operation == 'select':
                node['states']['selected'] = True
            elif operation == 'expand':
                node['states']['expand'] = 1
        return dict(success=True, input_attempted=request['operation'] != 'inspect', operation_returned=True,
                    nodes=copy.deepcopy(self.nodes), complete=True)

    def call(self, action='inspect', kind='setup', **kwargs):
        return service.control('configured.exe', action, 42, TITLE, kind, **kwargs)

    def inspect(self, kind='setup'):
        result = self.call(kind=kind)
        self.assertTrue(result['success'], result)
        return result['snapshot']

    def mutate(self, action='set_checkbox', kind='setup', **kwargs):
        return self.call(action, kind, expected_snapshot=self.inspect(kind), runtime_id=self.nodes[0]['runtime_id'], **kwargs)

    def editor(self):
        self.target.row.update(title='Situs Strategy Editor', **{'class': 'ObservedStrategyClass'})

    def directions(self):
        self.target.row.update(title='Layer Directions', **{'class': 'ObservedDirectionClass'})

    def test_inspect_returns_hash_and_no_input(self):
        result = self.call()
        self.assertTrue(result['success'], result)
        self.assertTrue(result['no_input'])
        self.assertEqual(len(result['snapshot']['snapshot_sha256']), 64)
        self.assertEqual(self.inputs, [])
        self.target.close.assert_called_once()

    def test_one_custom_checkbox_toggle_and_observable_readback(self):
        self.nodes[0]['class'] = 'TXPCheckBox'
        result = self.mutate(value=True, setting='ripup')
        self.assertTrue(result['success'], result)
        self.assertTrue(result['setting_readback_verified'])
        self.assertFalse(result['completion_verified'])
        self.assertEqual(len(self.inputs), 1)
        self.assertEqual(self.inputs[0]['operation'], 'toggle')
        self.assertEqual(self.inputs[0]['expected_nodes'][0]['states']['toggle'], 0)

    def test_already_correct_is_no_input(self):
        self.nodes[0]['states']['toggle'] = 1
        result = self.mutate(value=True, setting='ripup')
        self.assertTrue(result['success'], result)
        self.assertTrue(result['already_satisfied'])
        self.assertEqual(self.inputs, [])

    def test_both_setup_checkboxes_and_orthogonal_are_semantic(self):
        self.nodes[0]['name'] = 'Lock All Pre-routes'
        self.assertTrue(self.mutate(value=True, setting='lock_preroutes')['success'])
        self.editor(); self.nodes[0].update(name='Orthogonal'); self.nodes[0]['states']['toggle'] = 0
        self.assertTrue(self.mutate(kind='strategy', value=True, setting='orthogonal')['success'])

    def test_unknown_custom_state_and_indeterminate_state_refuse(self):
        for state in ({}, {'toggle': 2}, {'toggle': False}):
            self.nodes[0]['states'] = state
            result = self.mutate(value=True, setting='ripup')
            self.assertFalse(result['success'], result)
            self.assertTrue(result['blocked'])
        self.assertEqual(self.inputs, [])

    def test_wrong_semantic_checkbox_cannot_toggle(self):
        self.nodes[0]['name'] = 'Lock All Pre-routes'
        result = self.mutate(value=True, setting='ripup')
        self.assertTrue(result['blocked'], result)
        self.assertEqual(self.inputs, [])

    def test_any_stale_snapshot_field_blocks(self):
        receipt = self.inspect()
        for mutation in [lambda: self.nodes[0].update(enabled=False), lambda: self.nodes[0].update(name='Changed'),
                         lambda: self.nodes[0]['states'].update(toggle=1), lambda: self.nodes.append(element(3, 'Other', toggle=0))]:
            self.nodes = copy.deepcopy(receipt['nodes'])
            mutation()
            result = self.call('set_checkbox', expected_snapshot=receipt, runtime_id=[42, 2], value=True, setting='ripup')
            self.assertTrue(result['blocked'], result)
        self.assertEqual(self.inputs, [])

    def test_fabricated_or_incomplete_receipt_cannot_authorize(self):
        receipt = self.inspect()
        for key, value in [('snapshot_sha256', 'a' * 64), ('complete', False), ('identity', {'pid': 999})]:
            altered = copy.deepcopy(receipt); altered[key] = value
            result = self.call('set_checkbox', expected_snapshot=altered, runtime_id=[42, 2], value=True, setting='ripup')
            self.assertTrue(result['blocked'], result)
        self.assertEqual(self.inputs, [])

    def test_foreign_offscreen_disabled_password_duplicate_nodes_block(self):
        original = copy.deepcopy(self.nodes)
        for change in [dict(pid=999), dict(offscreen=True), dict(enabled=False), dict(password=True)]:
            self.nodes = copy.deepcopy(original); self.nodes[0].update(change)
            self.assertTrue(self.mutate(value=True, setting='ripup')['blocked'])
        self.nodes = original * 2
        self.assertTrue(self.mutate(value=True, setting='ripup')['blocked'])
        self.assertEqual(self.inputs, [])

    def test_no_pattern_means_no_fallback(self):
        self.nodes[0]['patterns'] = []
        result = self.mutate(value=True, setting='ripup')
        self.assertTrue(result['blocked'], result)
        self.assertIn('no fallback', result['error'])
        self.assertEqual(self.inputs, [])

    def test_worker_refuses_after_inspection_with_zero_input(self):
        receipt = self.inspect()
        self.worker.side_effect = [self.simulate(dict(operation='inspect')),
                                  dict(success=False, input_attempted=False, error='Fresh snapshot changed')]
        result = self.call('set_checkbox', expected_snapshot=receipt, runtime_id=[42, 2], value=True, setting='ripup')
        self.assertTrue(result['blocked'], result)
        self.assertFalse(result['outcome_unknown'])

    def test_mutation_timeout_is_unknown_and_not_retried(self):
        receipt = self.inspect()
        self.worker.side_effect = [self.simulate(dict(operation='inspect')),
                                  dict(success=False, input_attempted=True, error='UIA timeout')]
        calls = self.worker.call_count
        result = self.call('set_checkbox', expected_snapshot=receipt, runtime_id=[42, 2], value=True, setting='ripup')
        self.assertTrue(result['outcome_unknown'], result)
        self.assertFalse(result['blocked'])
        self.assertEqual(self.worker.call_count - calls, 2)

    def test_failed_readback_does_not_retry_or_claim_success(self):
        receipt = self.inspect()
        self.worker.side_effect = [self.simulate(dict(operation='inspect')),
                                  dict(success=True, input_attempted=True, operation_returned=True, complete=True, nodes=copy.deepcopy(self.nodes))]
        result = self.call('set_checkbox', expected_snapshot=receipt, runtime_id=[42, 2], value=True, setting='ripup')
        self.assertFalse(result['success'], result)
        self.assertTrue(result['outcome_unknown'])
        self.assertFalse(result['setting_readback_verified'])

    def test_unrelated_checkbox_change_is_not_accepted(self):
        self.nodes.append(element(3, 'Lock All Pre-routes', toggle=0))
        receipt = self.inspect()
        def changed(request):
            result = self.simulate(request)
            if request['operation'] != 'inspect':
                result['nodes'][1]['states']['toggle'] = 1
            return result
        self.worker.side_effect = changed
        result = self.call('set_checkbox', expected_snapshot=receipt, runtime_id=[42, 2], value=True, setting='ripup')
        self.assertFalse(result['success'], result)
        self.assertTrue(result['outcome_unknown'])
        self.assertEqual(len(self.inputs), 1)

    def test_strategy_text_requires_actual_label_and_writable_value(self):
        self.editor(); self.nodes = [element(name='Name:', kind='Edit', value='Custom', value_readonly=False)]
        result = self.mutate('set_text', 'strategy', value='Custom 2', setting='name')
        self.assertTrue(result['success'], result)
        self.nodes[0]['name'] = ''
        self.assertTrue(self.mutate('set_text', 'strategy', value='Custom 3', setting='name')['blocked'])
        self.nodes[0]['label'] = 'Strategy Name'; self.nodes[0]['states']['value_readonly'] = True
        self.assertTrue(self.mutate('set_text', 'strategy', value='Custom 3', setting='name')['blocked'])

    def test_builtin_strategy_name_and_empty_name_rejected(self):
        self.editor(); self.nodes = [element(name='Name', kind='Edit', value='Custom', value_readonly=False)]
        for value in ['', '  ', 'Cleanup', '\x00', 'x' * 2049]:
            self.assertTrue(self.mutate('set_text', 'strategy', value=value, setting='name')['blocked'])
        self.assertEqual(self.inputs, [])

    def test_via_effort_uses_observed_range_not_invented_slider_values(self):
        self.editor(); self.nodes = [element(name='', kind='Slider', range={'value': 3, 'minimum': 0, 'maximum': 5, 'readonly': False})]
        result = self.mutate('set_via_effort', 'strategy', value=4)
        self.assertTrue(result['success'], result)
        for value in [-1, 6, float('nan'), True]:
            self.assertTrue(self.mutate('set_via_effort', 'strategy', value=value)['blocked'])
        self.nodes.append(element(3, '', 'Slider', range={'value': 1, 'minimum': 0, 'maximum': 5, 'readonly': False}))
        self.assertTrue(self.mutate('set_via_effort', 'strategy', value=4)['blocked'])

    def test_select_strategy_and_pass_use_exact_observed_item(self):
        self.nodes = [element(name='Custom Strategy', kind='ListItem', selected=False, selection_container='42,7')]
        self.assertTrue(self.mutate('select_strategy')['success'])
        self.editor(); self.nodes = [element(name='Completion', kind='ListItem', selected=False, selection_container='42,7')]
        self.assertTrue(self.mutate('select_pass', 'strategy')['success'])
        self.nodes[0]['name'] = 'Unrecognized pass'; self.nodes[0]['states']['selected'] = False
        self.assertTrue(self.mutate('select_pass', 'strategy')['blocked'])

    def test_direction_combo_expand_and_selection(self):
        self.directions(); self.nodes = [element(name='Current Setting', kind='ComboBox', expand=0)]
        self.assertTrue(self.mutate('expand_direction', 'directions')['success'])
        self.nodes = [element(name='Horizontal', kind='ListItem', selected=False, selection_container='42,7')]
        self.assertTrue(self.mutate('select_direction', 'directions')['success'])
        self.nodes[0]['name'] = 'Invalid Direction'; self.nodes[0]['states']['selected'] = False
        self.assertTrue(self.mutate('select_direction', 'directions')['blocked'])

    def test_named_button_dispatch_does_not_claim_completion(self):
        self.nodes = [element(name='&Add', kind='Button')]
        self.nodes[0]['patterns'] = ['InvokePatternIdentifiers.Pattern']
        result = self.mutate('add_strategy')
        self.assertTrue(result['success'], result)
        self.assertFalse(result['completion_verified'])
        self.assertFalse(result['setting_readback_verified'])
        self.assertEqual(self.inputs[0]['operation'], 'invoke')
        self.assertTrue(self.mutate('remove_strategy')['blocked'])

    def test_edit_and_delete_builtin_strategy_block(self):
        self.nodes = [element(name='Edit', kind='Button'), element(3, 'Cleanup', 'ListItem', selected=True, selection_container='42,7')]
        self.nodes[0]['patterns'] = ['InvokePatternIdentifiers.Pattern']
        self.assertTrue(self.mutate('edit_strategy')['blocked'])
        self.nodes[0]['name'] = 'Remove'
        self.assertTrue(self.mutate('remove_strategy')['blocked'])
        self.nodes[0]['name'] = 'Duplicate'
        self.assertTrue(self.mutate('duplicate_strategy')['success'])

    def test_report_is_complete_accessibility_text_not_html(self):
        self.nodes = [element(name='Routing Setup Report', kind='Document', text='Routing Setup Report\nRules: Width', text_truncated=False)]
        result = self.call('read_report')
        self.assertTrue(result['success'], result)
        self.assertEqual(result['report_format'], 'accessibility_text')
        self.assertTrue(result['report_complete'])
        self.assertEqual(self.inputs, [])
        self.nodes[0]['states']['text_truncated'] = True
        self.assertFalse(self.call('read_report')['success'])

    def test_ambiguous_report_does_not_choose_first(self):
        self.nodes = [element(2, 'Report', 'Document', text='one', text_truncated=False), element(3, 'Other', 'Document', text='two', text_truncated=False)]
        self.assertFalse(self.call('read_report')['success'])

    def test_main_inspection_is_read_only(self):
        self.target.hwnd = 1
        self.target.row.update(title=TITLE, **{'class': 'TDocumentForm'})
        snapshot = self.inspect('main')
        result = self.call('set_checkbox', 'main', expected_snapshot=snapshot, runtime_id=[42, 2], value=True, setting='ripup')
        self.assertTrue(result['blocked'], result)
        self.assertEqual(self.inputs, [])

    def test_wrong_main_identity_stops_before_worker(self):
        self.target.api.matches.return_value = False
        self.assertTrue(self.call()['blocked'])
        self.worker.assert_not_called()

    def test_unsupported_dialog_and_readonly_mutation_arguments_refuse(self):
        self.assertTrue(self.call(kind='arbitrary')['blocked'])
        self.assertTrue(self.call(value=True)['blocked'])
        self.worker.assert_not_called()


class WorkerProtocolTests(unittest.TestCase):
    def test_hidden_bounded_worker_gets_json_stdin_not_command_interpolation(self):
        request = dict(operation='inspect', arbitrary='$(unsafe); "quotes"')
        process = SimpleNamespace(stdout='{"success":true,"nodes":[],"complete":true}\n', stderr='', returncode=0)
        with patch.object(service.subprocess, 'run', return_value=process) as run:
            result = service._run_worker(request)
        self.assertTrue(result['success'])
        args, kwargs = run.call_args
        self.assertNotIn(request['arbitrary'], args[0])
        self.assertEqual(json.loads(kwargs['input']), request)
        self.assertFalse(kwargs['shell'])
        self.assertEqual(kwargs['timeout'], 15)
        self.assertNotIn('-Command', args[0])

    def test_worker_timeout_marks_mutation_unknown_not_readonly(self):
        with patch.object(service.subprocess, 'run', side_effect=subprocess.TimeoutExpired(['worker'], 15)):
            result = service._run_worker(dict(operation='toggle'))
            read = service._run_worker(dict(operation='inspect'))
        self.assertTrue(result['input_attempted'])
        self.assertTrue(result['outcome_unknown'])
        self.assertFalse(read['input_attempted'])
        self.assertFalse(read['outcome_unknown'])

    def test_malformed_worker_after_action_is_not_safe_to_retry(self):
        with patch.object(service.subprocess, 'run', return_value=SimpleNamespace(stdout='invalid', returncode=1)):
            result = service._run_worker(dict(operation='toggle'))
        self.assertFalse(result['success'])
        self.assertTrue(result['input_attempted'])
        self.assertTrue(result['outcome_unknown'])


if __name__ == '__main__':
    unittest.main()
