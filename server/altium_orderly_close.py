"""Guarded ordinary Altium exit through its registered Client process.

No force termination, prompt input, UI Automation, save, restart, or retry.
A dedicated fixed native inventory program runs under the existing bridge lock.
The optional private callback is for integration/testing only; it must dispatch
that fixed program with the supplied ID. Public callers cannot supply snapshots.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import time
import uuid

import altium_focus
import altium_lifecycle
import emergency_stop
import script_recovery
import script_lint

QUIT_ARGUMENT = '-RClient:QuitFromEDAClient'
INVENTORY_TEMPLATE = Path(__file__).with_name('native_templates') / 'open_documents.pas.in'
BLOCKING_CLASSES = {'#32770', 'TMessageForm', 'TShowException', 'TPCBImportWizard'}


def _write(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
    temporary.replace(path)


def _identity(handle, exe, pid, created):
    result = handle.identity()
    emergency_stop._valid_identity(result, exe, pid)
    if type(result.get('creation_filetime')) is not int or result['creation_filetime'] != created:
        raise ValueError('Exact process creation time changed')
    return result


def _ready(exe, pid):
    inventory = altium_lifecycle.configured_instances(exe)
    if inventory.get('uncertain') or [row['pid'] for row in inventory.get('configured', [])] != [pid]:
        raise ValueError('Exactly one configured process is required')
    return altium_focus._select_main(altium_focus.WindowAPI(), pid)


def _clean_inventory(value, request_id, pcb_path):
    if (not isinstance(value, dict) or value.get('request_id') != request_id
            or value.get('success') is not True or value.get('complete') is not True
            or value.get('current_pcb_preserved') is not True
            or value.get('scope') != 'loaded_server_document_containers'
            or value.get('document_save_requested') is not False
            or value.get('document_close_requested') is not False
            or value.get('pcb_path', '').casefold() != pcb_path.casefold()
            or value.get('error') != '' or not isinstance(value.get('modules'), list)):
        raise ValueError('Fresh complete exact-PCB inventory is required')
    count = 0
    for module in value['modules']:
        if not isinstance(module, dict) or not isinstance(module.get('documents'), list):
            raise ValueError('Invalid native document inventory')
        for document in module['documents']:
            if (not isinstance(document, dict) or document.get('modified') is not False
                    or not isinstance(document.get('path'), str) or not isinstance(document.get('kind'), str)):
                raise ValueError('Unsaved or unclassified native document blocks normal exit')
            count += 1
    # The exact current PCB must appear in the same inventory. Empty data cannot
    # authorize a close when the guarded inventory began on a PCB document.
    if not any(d['path'].casefold() == pcb_path.casefold() and d['kind'].casefold() == 'pcb'
               for m in value['modules'] for d in m['documents']):
        raise ValueError('Current PCB is absent from the document inventory')
    return count


async def _native_inventory(exe, exchange, folder, request_id, pcb_path, main, created, result):
    """Run the previously qualified fixed read-only program, once, without Sandbox."""
    native = folder / (request_id + '.json')
    if native.exists(): raise ValueError('Inventory receipt already exists; no replay')
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    replacements = {'__PCB_PATH__':quote(pcb_path), '__REQUEST_ID__':quote(request_id), '__RECEIPT_PATH__':quote(native)}
    source = re.sub('__PCB_PATH__|__REQUEST_ID__|__RECEIPT_PATH__', lambda m: replacements[m.group()],
                    INVENTORY_TEMPLATE.read_text(encoding='utf-8-sig'))
    if script_lint.lint('', source).get('success') is not True:
        raise ValueError('Fixed native inventory failed preflight')
    pas = folder / 'OpenDocuments.pas'; project = folder / 'OpenDocuments.PrjScr'
    with pas.open('x', encoding='utf-8') as stream: stream.write(source)
    with project.open('x', encoding='utf-8') as stream:
        stream.write('[Design]\nVersion=1.0\n\n[Document1]\nDocumentPath=OpenDocuments.pas\n')
    before = _identity(main, exe, result['target_pid'], created)
    _ready(exe, result['target_pid'])
    if any(c in str(project) for c in '\r\n\0"|'):
        raise ValueError('Unsafe native inventory project path')
    state = dict(phase='orderly_close_inventory_pending', token=request_id, process_identity=before, launches=[])
    script_recovery._write_state(exchange, state)
    result['inventory_dispatch_attempted'] = True
    _write(folder/'result.json', dict(result, outcome_unknown=True))
    launcher = script_recovery._launch(exe, '-RScriptingSystem:RunScript(ProjectName="' + str(project) + '"|ProcName="OpenDocuments>RunGetOpenDocuments")')
    state['launches'].append(dict(phase='close_inventory', pid=launcher.pid, exit_code=None))
    script_recovery._write_state(exchange, state)
    deadline = time.monotonic() + 15
    value = None
    while True:
        code = launcher.poll()
        if code is not None:
            state['launches'][0]['exit_code'] = code
            script_recovery._launches.pop(launcher.pid, None)
        try:
            with native.open('rb') as stream: raw = stream.read(4*1024*1024+1)
            if len(raw) > 4*1024*1024: raise ValueError('Oversized native inventory')
            value = json.loads(raw.decode('utf-8-sig'))
        except (OSError, ValueError, UnicodeError): value = None
        valid = isinstance(value, dict) and value.get('request_id') == request_id and type(value.get('complete')) is bool
        if code is not None and valid: break
        if time.monotonic() >= deadline: break
        await asyncio.sleep(0.1)
    _identity(main, exe, result['target_pid'], created)
    _ready(exe, result['target_pid'])
    if code != 0 or not valid:
        raise ValueError('Fresh native inventory return was not verified; no close or retry')
    state.update(phase='recovered', recovery_basis='fixed_readonly_inventory_returned', executor_recovery_performed=False)
    script_recovery._write_state(exchange, state)
    return value


async def close(exe, exchange_dir, command_lock, read_inventory=None, *, pcb_path,
                target_pid, expected_creation_filetime, operation_id='', timeout_seconds=15):
    """Ordinary exit, once, after a newly dispatched clean inventory.

    By default, the fixed inventory template runs in a unique standalone project.
    Optional read_inventory(request_id) is a private async callback invoked while
    BOTH locks are held. It must not reacquire either lock and must return the decoded
    exact native inventory receipt, not a bridge envelope or an old file.
    expected_creation_filetime is a decimal string (public wrapper may decode ft:).
    Historical operation IDs return historical evidence without a fresh close.
    """
    result = dict(success=False, dispatch_attempted=False, outcome_unknown=False,
                  retry_allowed=False, force_termination_requested=False,
                  save_requested=False, prompt_input_requested=False,
                  exact_main_exited=False, orderly_exit_verified=False,
                  replayed=False, current_state_rechecked=False)
    try:
        if type(target_pid) is not int or target_pid <= 0:
            raise ValueError('Positive exact target_pid required')
        if (not isinstance(expected_creation_filetime, str)
                or re.fullmatch(r'[1-9][0-9]{0,19}', expected_creation_filetime) is None
                or int(expected_creation_filetime) >= 2**64):
            raise ValueError('Exact positive FILETIME decimal string required')
        if (not isinstance(pcb_path, str) or not pcb_path or any(c in pcb_path for c in '\r\n\0')
                or not PureWindowsPath(pcb_path).is_absolute() or PureWindowsPath(pcb_path).suffix.lower() != '.pcbdoc'
                or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30):
            raise ValueError('Exact PCB path and bounded integer timeout required')
        operation_id = uuid.uuid4().hex if operation_id == '' else operation_id
        if not isinstance(operation_id, str) or re.fullmatch('[0-9a-f]{32}', operation_id) is None:
            raise ValueError('operation_id must be 32 lowercase hex characters')
        if read_inventory is not None and not callable(read_inventory): raise ValueError('Invalid private inventory callback')
        exe = str(exe)
        if not PureWindowsPath(exe).is_absolute(): raise ValueError('Absolute configured executable required')
        exchange = Path(exchange_dir)
        if not exchange.is_absolute(): raise ValueError('Absolute configured exchange directory required')
        if not exchange.is_dir() or any(c in exe for c in '\r\n\0"|'):
            raise ValueError('Invalid configured executable/exchange directory')
        created = int(expected_creation_filetime)
        binding = dict(exe=exe, target_pid=target_pid, creation_filetime=expected_creation_filetime,
                       pcb_path=pcb_path, operation_id=operation_id)
        result.update(binding)
        async with command_lock:
            with emergency_stop.emergency_claim(exchange):
                folder = exchange / 'orderly_close' / operation_id
                if folder.exists():
                    if json.loads((folder/'request.json').read_text(encoding='utf-8')) != binding:
                        raise ValueError('Operation ID is already bound to a different request')
                    stored = folder/'result.json'
                    previous = json.loads(stored.read_text(encoding='utf-8')) if stored.exists() else dict(result, outcome_unknown=True)
                    return dict(previous, replayed=True, current_state_rechecked=False)
                if script_recovery.blocked_state(exchange):
                    raise ValueError('Unresolved recovery state blocks ordinary exit')
                with emergency_stop.WinProcess(target_pid, synchronize_access=True) as main:
                    before = _identity(main, exe, target_pid, created)
                    _ready(exe, target_pid)
                    folder.mkdir(parents=True, exist_ok=False)
                    _write(folder/'request.json', binding)
                    result['receipt_directory'] = str(folder)
                    try:
                        inventory_id = uuid.uuid4().hex
                        native = (await read_inventory(inventory_id) if read_inventory is not None else
                                  await _native_inventory(exe, exchange, folder, inventory_id, pcb_path, main, created, result))
                        observed = time.monotonic()
                        _identity(main, exe, target_pid, created)
                        result['document_count'] = _clean_inventory(native, inventory_id, pcb_path)
                        _write(folder/'inventory.json', native)
                        result.update(inventory_request_id=inventory_id, current_state_rechecked=True,
                                      inventory_sha256=hashlib.sha256((folder/'inventory.json').read_bytes()).hexdigest())
                        _ready(exe, target_pid)
                        if script_recovery.blocked_state(exchange):
                            raise ValueError('Inventory left an unresolved recovery state')
                        _identity(main, exe, target_pid, created)
                        if time.monotonic() - observed > 5:
                            raise ValueError('Inventory is stale; no close dispatched')
                        state = dict(phase='orderly_close_pending', token=operation_id,
                                     process_identity=before, launches=[], close_receipt=str(folder/'result.json'))
                        script_recovery._write_state(exchange, state)
                        result.update(dispatch_attempted=True, outcome_unknown=True)
                        _write(folder/'result.json', result)
                        launcher = script_recovery._launch(exe, QUIT_ARGUMENT)
                        state['launches'].append(dict(phase='ordinary_quit', pid=launcher.pid, exit_code=None))
                        script_recovery._write_state(exchange, state)
                        deadline = time.monotonic() + timeout_seconds
                        api = altium_focus.WindowAPI()
                        teardown_identity_unavailable = False
                        while True:
                            waited = main.wait(0)
                            code = launcher.poll()
                            state['launches'][0]['exit_code'] = code
                            result['launcher_exit_code'] = code
                            if code is not None: script_recovery._launches.pop(launcher.pid, None)
                            if waited.get('wait_status') == 0:
                                observation = main.exit_observation()
                                if observation.get('pid') != target_pid or observation.get('creation_filetime') != created:
                                    raise ValueError('Exited process identity changed')
                                result.update(exact_main_exited=True, main_exit_code=observation['exit_code'])
                                if code is not None:
                                    result.update(success=code == 0 and observation['exit_code'] == 0,
                                                  orderly_exit_verified=code == 0 and observation['exit_code'] == 0,
                                                  outcome_unknown=False)
                                    if result['success']:
                                        state.update(phase='recovered', recovery_basis='exact_main_and_quit_launcher_exited', executor_recovery_performed=False)
                                    else: result['error'] = 'Process exit observed but normal zero exit codes were not verified'
                                    break
                            elif waited.get('wait_status') != 258:
                                raise ValueError('Unexpected process wait state')
                            elif not teardown_identity_unavailable:
                                try:
                                    _identity(main, exe, target_pid, created)
                                except (OSError, ValueError) as error:
                                    # Quit was already dispatched after exact identity
                                    # and clean-inventory checks. During teardown,
                                    # token/image/running queries may fail before the
                                    # pinned HANDLE becomes signalled. From here use
                                    # only that HANDLE and the owned launcher until
                                    # the original deadline; no PID reopen, further
                                    # identity query, window observation or new input.
                                    teardown_identity_unavailable = True
                                    result.update(teardown_identity_query_unavailable=True,
                                                  teardown_identity_error=str(error))
                                else:
                                    rows = api.windows(target_pid)
                                    blockers = [r for r in rows if r.get('class') in BLOCKING_CLASSES
                                                or r.get('class') not in {'TDocumentForm', 'TApplication'}]
                                    if blockers:
                                        result.update(dialogs=blockers, error='Native dialog blocks exit; no prompt answered')
                                        break
                            if time.monotonic() >= deadline:
                                result['error'] = 'Normal exit was not verified within the bounded wait; no retry'
                                break
                            await asyncio.sleep(0.1)
                        script_recovery._write_state(exchange, state)
                        _write(folder/'result.json', result)
                        return result
                    except asyncio.CancelledError:
                        result.update(outcome_unknown=result['dispatch_attempted'] or bool(script_recovery.blocked_state(exchange)), error='Cancelled; no replay')
                        _write(folder/'result.json', result)
                        raise
                    except Exception as error:
                        result.update(outcome_unknown=result['dispatch_attempted'] or bool(script_recovery.blocked_state(exchange)), error=str(error))
                        _write(folder/'result.json', result)
                        return result
    except (OSError, ValueError, TypeError) as error:
        return dict(result, error=str(error))


def fresh_start_after_pending_close(exe, exchange_dir, old):
    """Verify evidence for one new read-only probe; never repeat Quit or Stop.

    The former process' historical exit code remains unknown. Current absence
    of that exact identity plus a newer owned normal-start receipt authorizes
    only a new executor probe, not retrospective completion of the close.
    Caller holds the bridge lock and cross-process recovery claim.
    """
    exchange = Path(exchange_dir)
    latch_path = exchange / 'script_recovery_state.json'
    raw = latch_path.read_bytes()
    token = old.get('token')
    if (json.loads(raw) != old or old.get('phase') != 'orderly_close_pending'
            or not isinstance(token, str) or re.fullmatch('[0-9a-f]{32}', token) is None):
        raise ValueError('Exact pending ordinary-close checkpoint required')
    folder = exchange / 'orderly_close' / token
    result_path = folder / 'result.json'
    if emergency_stop._canonical(old.get('close_receipt', '')) != emergency_stop._canonical(result_path):
        raise ValueError('Close receipt is outside the exact operation directory')
    request_path = folder / 'request.json'
    inventory_path = folder / 'inventory.json'
    startup_path = exchange / 'altium_startup.json'
    frozen = {path:path.read_bytes() for path in (request_path,result_path,inventory_path,startup_path)}
    request = json.loads(frozen[request_path]); result = json.loads(frozen[result_path])
    previous = old.get('process_identity')
    if (not isinstance(previous, dict) or previous.get('exe') != emergency_stop._canonical(exe)
            or previous.get('user_sid') != emergency_stop._current_sid()
            or type(previous.get('pid')) is not int or previous['pid'] <= 0
            or type(previous.get('creation_filetime')) is not int or previous['creation_filetime'] <= 0
            or not isinstance(request, dict) or not isinstance(result, dict)
            or request.get('operation_id') != token or request.get('target_pid') != previous['pid']
            or request.get('creation_filetime') != str(previous['creation_filetime'])
            or emergency_stop._canonical(request.get('exe', '')) != emergency_stop._canonical(exe)
            or any(result.get(k) != v for k,v in request.items())
            or result.get('dispatch_attempted') is not True
            or result.get('outcome_unknown') is not True
            or result.get('force_termination_requested') is not False
            or result.get('save_requested') is not False or result.get('prompt_input_requested') is not False
            or result.get('retry_allowed') is not False or result.get('replayed') is not False
            or result.get('launcher_exit_code') != 0):
        raise ValueError('Matching owned close intent/result with successful quit launcher required')
    inventory = json.loads(frozen[inventory_path])
    if hashlib.sha256(frozen[inventory_path]).hexdigest() != result.get('inventory_sha256'):
        raise ValueError('Recorded clean inventory hash changed')
    _clean_inventory(inventory, result.get('inventory_request_id'), request['pcb_path'])
    launches = old.get('launches', [])
    if (len(launches) != 1 or launches[0].get('phase') != 'ordinary_quit'
            or type(launches[0].get('pid')) is not int or launches[0]['pid'] <= 0
            or launches[0]['pid'] == previous['pid'] or launches[0].get('exit_code') not in (None,0)):
        raise ValueError('Exactly one recorded ordinary-quit launcher required')
    launcher_pid = launches[0]['pid']
    owned = script_recovery._launches.get(launcher_pid)
    if owned is not None:
        if owned.poll() != 0: raise ValueError('The owned quit launcher is not successfully exited')
    else:
        try: launcher = emergency_stop.WinProcess(launcher_pid)
        except OSError as error:
            if getattr(error,'winerror',None) != 87: raise
        else:
            with launcher: observed = launcher.exit_observation()
            if observed.get('pid') != launcher_pid or observed.get('exit_code') != 0:
                raise ValueError('Quit launcher is still present or successful exit is unconfirmed')
    if not emergency_stop._exact_target_has_exited(previous):
        raise ValueError('The previous exact main process has not exited')
    startup = json.loads(frozen[startup_path]); identity = startup.get('identity')
    if (not isinstance(identity, dict) or startup.get('success') is not True
            or startup.get('launched') is not True or startup.get('script_dispatched') is not False
            or startup.get('mutation_replayed') is not False or startup.get('exit_code') is not None
            or startup.get('status') != 'process_started_ui_unverified'
            or emergency_stop._canonical(startup.get('configured_exe','')) != emergency_stop._canonical(exe)
            or type(identity.get('pid')) is not int or identity['pid'] <= 0
            or startup.get('target_pid') != identity['pid']
            or type(identity.get('creation_filetime')) is not int
            or identity['creation_filetime'] <= previous['creation_filetime']
            or startup.get('creation_filetime_decimal') != str(identity['creation_filetime'])):
        raise ValueError('Newer exact successful public normal-start receipt required')
    with emergency_stop.WinProcess(identity['pid']) as main:
        current = main.identity()
        emergency_stop._valid_identity(current, exe, identity['pid'])
        if not emergency_stop._same_identity(current,identity):
            raise ValueError('Current main differs from recorded normal start')
        main_window = _ready(exe, identity['pid'])
        environment = dict(ready=True, target_pid=identity['pid'],
            configured_process_ids=[identity['pid']],
            process_windows={identity['pid']: [main_window]},
            foreground_required=False, native_idle_verified=False)
        if not emergency_stop._same_identity(main.identity(),current):
            raise ValueError('New main changed during verification')
        if latch_path.read_bytes() != raw or any(path.read_bytes()!=data for path,data in frozen.items()):
            raise ValueError('Close, inventory, recovery or startup evidence changed')
    return dict(identity=current,environment=environment,evidence={
        'previous_main_identity':previous,'previous_close_token':token,
        'previous_main_absence_verified':True,'previous_main_historical_exit_code':None,
        'previous_close_retroactively_completed':False,'quit_launcher_recorded_exit_code':0,
        'quit_launcher_historical_creation_verified':False,
        'normal_start_identity_verified':True,'new_probe_required':True,
        'quit_replayed':False,'stop_dispatched':False})


def verify_restart_after_probe(exe, expected_identity):
    """Bind a matching probe receipt to the same ready restarted process.

    This read-only readiness check neither activates nor restores a window.
    It must run before removing the recovery latch after the executor probe.
    """
    pid = expected_identity['pid']
    with emergency_stop.WinProcess(pid) as main:
        current = _identity(main, exe, pid, expected_identity['creation_filetime'])
        if not emergency_stop._same_identity(current, expected_identity):
            raise ValueError('Restarted process changed before probe verification')
        _ready(exe, pid)
        if not emergency_stop._same_identity(main.identity(), current):
            raise ValueError('Restarted process changed during probe verification')
    return dict(identity=current, exact_restart_identity_verified=True,
                readiness_verified=True, foreground_required=False,
                native_transaction_idle_verified=False)
