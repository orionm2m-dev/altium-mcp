"""Validated requests for narrow native mechanical-object inspection/deletion."""
from pathlib import PureWindowsPath
from pcb_primitives import _pcb_path, _text, _unknown

def build_mechanical_control_spec(pcb_path, action, targets=None):
    path = _pcb_path(pcb_path)
    if not PureWindowsPath(path).is_absolute():
        raise ValueError('pcb_path must be absolute')
    if action not in {'inspect', 'delete'}:
        raise ValueError('action must be inspect or delete')
    if targets is None:
        targets = []
    if not isinstance(targets, list) or len(targets) > 64:
        raise ValueError('targets must be a list of at most 64 objects')
    if (action == 'inspect' and targets) or (action == 'delete' and not targets):
        raise ValueError('inspect takes no targets; delete requires targets')
    lines = ['PCB|' + path, 'ACTION|' + action]
    seen = set()
    for item in targets:
        _unknown(item, {'kind', 'name', 'expected_key'})
        kind = item.get('kind')
        if kind not in {'free_npth', 'keepout_region'}:
            raise ValueError('unsupported mechanical kind')
        name = _text(item.get('name'), 'name')
        key = _text(item.get('expected_key'), 'expected_key')
        if len(key) > 200000:
            raise ValueError('expected_key is too long')
        identity = (kind, name.casefold())
        if identity in seen:
            raise ValueError('duplicate target')
        seen.add(identity)
        lines.append('|'.join([kind, name, key]))
    return '\n'.join(lines) + '\n'
