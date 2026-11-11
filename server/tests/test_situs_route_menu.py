"""File-only route-menu policy tests; no native actions."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import situs_route_menu as menu
from server.tests.test_situs_setup_extended import element, TITLE


def receipt(scope='route'):
    root = element(2, 'Route' if scope == 'route' else 'Auto Route', 'MenuItem', expand=0)
    root['parent_runtime_id'] = ''
    root['patterns'].append('InvokePatternIdentifiers.Pattern')
    child = element(3, 'Auto Route' if scope == 'route' else 'Stop', 'MenuItem', expand=0) if scope == 'route' else element(3, 'Stop', 'MenuItem')
    child['parent_runtime_id'] = '42,2'
    child['patterns'].append('InvokePatternIdentifiers.Pattern')
    return {'kind': 'main', 'menu_scope': scope, 'complete': True, 'identity': {'pid': 123}, 'nodes': [root, child]}


class MenuPolicyTests(unittest.TestCase):
    def test_route_and_auto_route_expansion(self):
        r = receipt()
        self.assertEqual(menu._plan(r, 'expand_route', [42, 2]), ('expand', False))
        self.assertEqual(menu._plan(r, 'expand_auto_route', [42, 3]), ('expand', False))
        r['nodes'][0]['states']['expand'] = 1
        self.assertEqual(menu._plan(r, 'expand_route', [42, 2]), ('expand', True))

    def test_only_six_named_commands_inside_auto_route(self):
        for action, caption in menu.COMMANDS.items():
            r = receipt('autoroute'); r['nodes'][1]['name'] = caption.title()
            self.assertEqual(menu._plan(r, action, [42, 3]), ('invoke', False))
        with self.assertRaises(ValueError):
            menu._plan(receipt('autoroute'), 'unroute_all', [42, 3])

    def test_arbitrary_stop_or_wrong_ancestor_cannot_invoke(self):
        r = receipt(); r['nodes'][1]['name'] = 'Stop'
        with self.assertRaisesRegex(ValueError, 'Auto Route'):
            menu._plan(r, 'stop', [42, 3])
        r = receipt('autoroute'); r['nodes'][1]['parent_runtime_id'] = '42,999'
        with self.assertRaisesRegex(ValueError, 'immediate'):
            menu._plan(r, 'stop', [42, 3])

    def test_disabled_hidden_foreign_command_cannot_invoke(self):
        for key, value in [('enabled', False), ('offscreen', True), ('pid', 999)]:
            r = receipt('autoroute'); r['nodes'][1][key] = value
            with self.assertRaises(ValueError):
                menu._plan(r, 'stop', [42, 3])

    def test_duplicate_auto_route_does_not_pick_first(self):
        r = receipt(); r['nodes'].append(copy.deepcopy(r['nodes'][1])); r['nodes'][-1]['runtime_id'] = [42, 4]
        with self.assertRaisesRegex(ValueError, 'One observed'):
            menu._plan(r, 'expand_auto_route', [42, 3])

    def test_missing_expand_and_invoke_patterns_fail_closed(self):
        r = receipt(); r['nodes'][0]['patterns'] = ['InvokePatternIdentifiers.Pattern']
        with self.assertRaisesRegex(ValueError, 'no fallback'):
            menu._plan(r, 'expand_route', [42, 2])
        r = receipt('autoroute'); r['nodes'][1]['patterns'] = []
        with self.assertRaisesRegex(ValueError, 'no fallback'):
            menu._plan(r, 'stop', [42, 3])


class MenuControllerTests(unittest.TestCase):
    def setUp(self):
        self.main = {'hwnd': 1, 'title': TITLE, 'class': 'TDocumentForm'}
        self.target = Mock(hwnd=1, identity={'pid': 123, 'creation_filetime': 456, 'exe': 'configured.exe'},
                           row={'class': 'TDocumentForm', 'title': TITLE, 'enabled': True})
        self.target.api.windows.side_effect = lambda pid: [copy.deepcopy(self.main)]
        self.target.api.matches.return_value = True
        self.target.api.title.return_value = TITLE
        self.nodes = receipt()['nodes']
        self.calls = []
        def worker(request):
            self.calls.append(copy.deepcopy(request))
            if request['operation'] == 'expand':
                self.nodes[0]['states']['expand'] = 1
            return dict(success=True, input_attempted=request['operation'] != 'inspect', operation_returned=True, complete=True, nodes=copy.deepcopy(self.nodes))
        for p in (patch.object(menu.setup, 'CaptureTarget', return_value=self.target), patch.object(menu.setup, '_run_worker', side_effect=worker)):
            p.start(); self.addCleanup(p.stop)

    def test_scoped_inspect_does_not_request_full_main_tree(self):
        result = menu.control('configured.exe', 'inspect', 1, TITLE)
        self.assertTrue(result['success'], result)
        self.assertEqual(self.calls[0]['scope_path'], ['Route'])
        self.assertFalse(self.calls[0]['allow_partial'])
        self.assertFalse(result['input_attempted'])

    def test_scoped_single_expand_matches_receipt_then_reads_back(self):
        snapshot = menu.control('configured.exe', 'inspect', 1, TITLE)['snapshot']
        result = menu.control('configured.exe', 'expand_route', 1, TITLE, expected_snapshot=snapshot, runtime_id=[42, 2])
        self.assertTrue(result['success'], result)
        self.assertTrue(result['menu_expanded_verified'])
        self.assertFalse(result['routing_state_verified'])
        self.assertEqual([r['operation'] for r in self.calls], ['inspect', 'inspect', 'expand'])

    def test_stale_menu_blocks_without_invocation(self):
        snapshot = menu.control('configured.exe', 'inspect', 1, TITLE)['snapshot']
        self.nodes[0]['states']['expand'] = 1
        result = menu.control('configured.exe', 'expand_route', 1, TITLE, expected_snapshot=snapshot, runtime_id=[42, 2])
        self.assertTrue(result['blocked'], result)
        self.assertTrue(all(r['operation'] == 'inspect' for r in self.calls))

    def test_only_known_scope_and_no_mutation_args_for_inspect(self):
        self.assertTrue(menu.control('configured.exe', 'inspect', 1, TITLE, menu_scope='Run')['blocked'])
        self.assertTrue(menu.control('configured.exe', 'inspect', 1, TITLE, runtime_id=[42, 2])['blocked'])
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
