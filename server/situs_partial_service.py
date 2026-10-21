"""Serialized, scratch-only service for one selected-TP native Situs request.

Only the fixed builder is accepted. The public wrapper supplies configuration,
qualification root and shared bridge lock; callers supply no source or process.
Native return is not DRC, connectivity, whole-board completion or persistence.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import ntpath
import os
import re
from pathlib import Path, PureWindowsPath
import stat
import time
import uuid

import emergency_stop
import script_lint
import script_recovery
import situs_partial_native as native
from situs_native_settings_service import _identity, _environment


def _write(path, data):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _reject_reparse(path):
    for item in (path, *path.parents):
        attributes = getattr(item.lstat(), 'st_file_attributes', 0)
        if item.is_symlink() or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('Reparse points are not accepted in the qualification scope')


def _candidate_scope(pcb_path, qualification_root):
    """Require a saved local candidate inside the private configured root."""
    root_text = str(qualification_root)
    for text in (pcb_path, root_text):
        path = PureWindowsPath(text)
        if (not path.is_absolute() or len(path.drive) != 2 or path.drive[1] != ':'
                or '..' in path.parts or any(c in text for c in '\0\r\n')):
            raise ValueError('Absolute local candidate and qualification root required')
    root = Path(root_text)
    board = Path(pcb_path)
    if not root.is_dir() or not board.is_file():
        raise ValueError('Existing qualification directory and saved candidate PCB required')
    _reject_reparse(root)
    _reject_reparse(board)
    resolved_root = root.resolve()
    resolved_board = board.resolve()
    if (ntpath.normcase(ntpath.commonpath((str(resolved_root), str(resolved_board))))
            != ntpath.normcase(str(resolved_root)) or resolved_board == resolved_root):
        raise ValueError('PCB is outside the private qualification root')
    # The native program receives the same canonical path that scope checking used.
    if ntpath.normcase(ntpath.normpath(pcb_path)) != ntpath.normcase(str(resolved_board)):
        raise ValueError('Use the canonical candidate PCB path, not a filesystem alias')
    return str(resolved_root)


def _preflight(request, receipt_path):
    source = native.build_partial_program(request, receipt_path)
    if source.count('procedure RunSelectedTPRoute;') != 1:
        raise ValueError('Fixed selected-TP entry point changed')
    if script_lint.lint('', source).get('success') is not True:
        raise ValueError('Fixed selected-TP program failed static preflight')
    return source


async def control(exe, exchange_dir, command_lock, *, qualification_root, pcb_path,
                  component_designator, expected_net, target_pid,
                  expected_creation_filetime, operation_id='', timeout_seconds=15,
                  partner_designator=None):
    """Dispatch the fixed selected single-pad TP action once on a saved candidate.

    expected_creation_filetime is the exact decimal string normalized by the
    public wrapper. qualification_root is private configuration, never a public input.
    Native guards require exact PCB/TP/net and LockPreRoutes true. An explicit
    partner selects the fixed BetweenSelectedComponents builder; the
    default remains OnSelectedComponents. Callers cannot choose a process/action.
    The module does not save,
    select a strategy, change rules, toggle LockPreRoutes, invoke DRC or use UI.
    Locks and the pinned HANDLE survive caller cancellation through bounded
    observation. Unknown/partial outcomes retain the pending latch; no replay.
    """
    result = dict(success=False, dispatch_attempted=False, outcome_unknown=False,
                  retry_allowed=False, replayed=False, current_state_rechecked=False,
                  routing_attempted=None, routing_return_verified=False,
                  drc_completed=False, connectivity_verified=False,
                  route_completeness_verified=False, board_saved=False,
                  process_identity_verified=False, pid_verified_by_native=False)
    try:
        if type(target_pid) is not int or target_pid <= 0:
            raise ValueError('target_pid must be a positive integer')
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30:
            raise ValueError('timeout_seconds must be an integer from 1 through 30')
        if (not isinstance(expected_creation_filetime, str)
                or re.fullmatch(r'[1-9][0-9]{0,19}', expected_creation_filetime) is None
                or int(expected_creation_filetime) >= 2 ** 64):
            raise ValueError('expected_creation_filetime must be a positive decimal FILETIME string')
        created = expected_creation_filetime
        operation_id = uuid.uuid4().hex if operation_id == '' else operation_id
        if partner_designator is None:
            request = native.build_partial_request(pcb_path, operation_id, component_designator, expected_net)
        else:
            request = native.build_pair_request(pcb_path, operation_id, component_designator,
                                                partner_designator, expected_net)
        approved_root = _candidate_scope(request['pcb_path'], qualification_root)
        exchange = Path(exchange_dir).resolve()
        if not exchange.is_dir():
            raise ValueError('Configured exchange directory does not exist')
        exe = str(exe)
        if not PureWindowsPath(exe).is_absolute():
            raise ValueError('Absolute configured executable required')
        folder = exchange / 'situs_partial' / operation_id
        receipt_path = folder / 'native.json'
        project = folder / 'SelectedTPRoute.PrjScr'
        if any(c in exe + str(project) for c in '\r\n\0"|'):
            raise ValueError('Unsafe configured executable or dedicated project path')
        source = _preflight(request, receipt_path)
        binding = dict(request=request, target_pid=target_pid,
                       creation_filetime_decimal=created,
                       executable=emergency_stop._canonical(exe),
                       qualification_root=approved_root)
        result.update(operation_id=operation_id, pcb_path=request['pcb_path'],
                      component_designator=component_designator, expected_net=expected_net,
                      target_pid=target_pid, creation_filetime_decimal=created,
                      qualification_root=approved_root, receipt_directory=str(folder),
                      source_sha256=hashlib.sha256(source.encode('utf-8')).hexdigest())
        if partner_designator is not None:
            result['partner_designator'] = request['partner_designator']
        async with command_lock:
            with emergency_stop.emergency_claim(exchange):
                if folder.exists():
                    saved = json.loads((folder / 'request.json').read_text('utf-8'))
                    if saved != binding:
                        raise ValueError('operation_id is bound to a different request, process or scope')
                    previous = folder / 'result.json'
                    if previous.exists():
                        historical = dict(json.loads(previous.read_text('utf-8')),
                                          replayed=True, current_state_rechecked=False)
                        pending = script_recovery.blocked_state(exchange)
                        if pending and pending.get('token') == operation_id:
                            # A durable result can precede a failed recovery-state
                            # write. Never promote that unfinished finalization.
                            historical.update(success=False, outcome_unknown=True,
                                service_completion_verified=False, routing_return_verified=False,
                                error='Earlier operation still has a pending recovery latch; no replay')
                        return historical
                    return dict(result, replayed=True, outcome_unknown=True,
                                error='Earlier operation lacks final receipt; no replay')
                blocked = script_recovery.blocked_state(exchange)
                if blocked:
                    return dict(result, blocked=True, recovery_phase=blocked.get('phase'),
                                error='Executor recovery is unresolved')
                with emergency_stop.WinProcess(target_pid) as process:
                    before = _identity(process, exe, target_pid, int(created))
                    _environment(exe, target_pid)
                    folder.mkdir(parents=True, exist_ok=False)
                    _write(folder / 'request.json', binding)
                    with (folder / 'SelectedTPRoute.pas').open('x', encoding='utf-8', newline='') as stream:
                        stream.write(source)
                    with project.open('x', encoding='utf-8', newline='') as stream:
                        stream.write('[Design]\nVersion=1.0\n\n[Document1]\nDocumentPath=SelectedTPRoute.pas\n')
                    worker = asyncio.create_task(_execute(exe, exchange, folder, project,
                        receipt_path, process, before, request, result, timeout_seconds))
                    cancelled = False
                    while True:
                        try:
                            outcome = await asyncio.shield(worker)
                            break
                        except asyncio.CancelledError:
                            if worker.cancelled():
                                raise
                            cancelled = True
                    if cancelled:
                        raise asyncio.CancelledError
                    return outcome
    except (OSError, ValueError, TypeError) as error:
        return dict(result, success=False, error=str(error))


async def _execute(exe, exchange, folder, project, receipt_path, process,
                   before, request, result, timeout):
    state = None
    try:
        created = int(result['creation_filetime_decimal'])
        _identity(process, exe, result['target_pid'], created)
        _environment(exe, result['target_pid'])
        _candidate_scope(request['pcb_path'], result['qualification_root'])
        state = dict(phase='situs_partial_pending', token=request['operation_id'],
                     situs_partial_receipt=str(receipt_path),
                     process_identity=before, launches=[])
        script_recovery._write_state(exchange, state)
        result.update(dispatch_attempted=True, outcome_unknown=True)
        _write(folder / 'result.json', dict(result, error='Fixed selected-TP dispatch pending'))
        argument = ('-RScriptingSystem:RunScript(ProjectName="' + str(project)
                    + '"|ProcName="SelectedTPRoute>RunSelectedTPRoute")')
        launcher = script_recovery._launch(exe, argument)
        launch = dict(phase='situs_partial', pid=launcher.pid, exit_code=None)
        state['launches'].append(launch)
        script_recovery._write_state(exchange, state)
        deadline = time.monotonic() + timeout
        receipt = None
        while True:
            code = launcher.poll()
            if code is not None:
                launch['exit_code'] = code
                script_recovery._launches.pop(launcher.pid, None)
            try:
                receipt = native.read_partial_receipt(receipt_path, request)
                if receipt is not None and receipt.get('stage') == 'not_observed':
                    receipt = None
            except (OSError, ValueError, UnicodeError):
                receipt = None
            terminal = receipt is not None and receipt['stage'] in native.TERMINAL_STAGES
            if (code is not None and terminal) or time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.1)
        after = _identity(process, exe, result['target_pid'], created)
        if not emergency_stop._same_identity(before, after):
            raise ValueError('Process identity changed during selected-TP request')
        result['readiness_after'] = _environment(exe, result['target_pid'])
        result.update(native_receipt=receipt, launcher_exit_code=code,
                      process_identity_verified=True)
        verified = code == 0 and terminal and receipt['outcome_unknown'] is False
        result.update(success=bool(verified and receipt['success']),
                      outcome_unknown=not verified, service_completion_verified=bool(verified),
                      current_state_rechecked=bool(verified and receipt['success']))
        if receipt is not None:
            result.update(routing_attempted=receipt.get('route_attempted'),
                          routing_return_verified=bool(verified and receipt['return_verified']))
        if verified:
            result['error'] = receipt.get('error', '')
            state.update(phase='recovered', recovery_basis='fixed_selected_tp_request_returned',
                         executor_recovery_performed=False)
        else:
            result['error'] = 'Native selected-TP return, readback or launcher exit was not verified'
    except Exception as error:
        result.update(success=False, error=str(error), outcome_unknown=result['dispatch_attempted'])
    try:
        result['service_receipt_persisted'] = True
        _write(folder / 'result.json', result)
        if state is not None:
            script_recovery._write_state(exchange, state)
    except (OSError, ValueError, TypeError) as error:
        result.update(success=False, outcome_unknown=result['dispatch_attempted'],
                      service_completion_verified=False, routing_return_verified=False,
                      current_state_rechecked=False, service_receipt_persisted=False,
                      error='Service receipt finalization failed: ' + str(error))
        if state is not None:
            state.update(phase='situs_partial_pending')
            state.pop('recovery_basis', None)
            state.pop('executor_recovery_performed', None)
            try:
                script_recovery._write_state(exchange, state)
            except (OSError, ValueError, TypeError):
                pass
        try:
            _write(folder / 'result.json', result)
        except (OSError, ValueError, TypeError):
            pass
    return result


def _read_saved_json(path):
    """Bounded, duplicate-key rejecting journal input."""
    with path.open('rb') as stream:
        content = stream.read(native.MAX_RECEIPT + 16385)
    if len(content) > native.MAX_RECEIPT + 16384:
        raise ValueError('Saved operation evidence exceeds its size limit')
    value = json.loads(content.decode('utf-8'), object_pairs_hook=native._pairs)
    if not isinstance(value, dict):
        raise ValueError('Saved operation evidence must be an object')
    return value


async def reconcile_known_return(exe, exchange_dir, command_lock, *, qualification_root,
                                 operation_id, target_pid, expected_creation_filetime):
    """Clear only a known returned invocation whose old-copper validation failed.

    This is journal reconciliation, with read-only process/window diagnostics.
    No native dispatch, PCB read/write, rollback or retry is performed. The
    original route result stays failed and its copper change remains unresolved.
    The immutable native terminal receipt plus the exact fixed source establish
    that routing returned, selection was restored and native process depth was
    idle at that time. Current responsiveness is not a new native idle proof.
    """
    result = dict(success=False, reconciled=False, executor_return_known=False, recovery_latch_cleared=False,
                  dispatch_attempted=False, routing_success=False, routing_return_verified=False,
                  copper_validation_passed=False, drc_completed=False, connectivity_verified=False,
                  board_saved=False, retry_allowed=False, native_idle_currently_verified=False)
    try:
        if not isinstance(operation_id, str) or re.fullmatch('[0-9a-f]{32}', operation_id) is None:
            raise ValueError('Exact existing 32 lowercase hexadecimal operation_id required')
        if type(target_pid) is not int or target_pid <= 0:
            raise ValueError('target_pid must be a positive integer')
        if (not isinstance(expected_creation_filetime, str)
                or re.fullmatch('[1-9][0-9]{0,19}', expected_creation_filetime) is None
                or int(expected_creation_filetime) >= 2 ** 64):
            raise ValueError('Exact decimal FILETIME string required')
        exe = str(exe)
        if not PureWindowsPath(exe).is_absolute():
            raise ValueError('Absolute configured executable required')
        exchange = Path(exchange_dir).resolve()
        folder = exchange / 'situs_partial' / operation_id
        receipt_path = folder / 'native.json'
        result.update(operation_id=operation_id, target_pid=target_pid,
                      creation_filetime_decimal=expected_creation_filetime)
        async with command_lock:
            with emergency_stop.emergency_claim(exchange):
                pending = script_recovery.blocked_state(exchange)
                if (not pending or pending.get('phase') != 'situs_partial_pending'
                        or pending.get('token') != operation_id
                        or pending.get('situs_partial_receipt') != str(receipt_path)):
                    raise ValueError('No matching selected-TP pending operation to reconcile')
                _reject_reparse(folder)
                binding = _read_saved_json(folder / 'request.json')
                request = native._request(binding.get('request'))
                if request['operation_id'] != operation_id:
                    raise ValueError('Saved request operation identity mismatch')
                approved_root = _candidate_scope(request['pcb_path'], qualification_root)
                expected_binding = dict(request=request, target_pid=target_pid,
                    creation_filetime_decimal=expected_creation_filetime,
                    executable=emergency_stop._canonical(exe), qualification_root=approved_root)
                if binding != expected_binding:
                    raise ValueError('Saved request is bound to another process or qualification scope')
                source = _preflight(request, receipt_path).encode('utf-8')
                if (folder / 'SelectedTPRoute.pas').read_bytes() != source:
                    raise ValueError('Saved source differs from the fixed reviewed native builder')
                source_hash = hashlib.sha256(source).hexdigest()
                original = _read_saved_json(folder / 'result.json')
                if (original.get('source_sha256') != source_hash
                        or original.get('operation_id') != operation_id
                        or original.get('target_pid') != target_pid
                        or original.get('creation_filetime_decimal') != expected_creation_filetime
                        or original.get('pcb_path') != request['pcb_path']
                        or original.get('qualification_root') != approved_root
                        or original.get('receipt_directory') != str(folder)
                        or original.get('component_designator') != request['component_designator']
                        or original.get('expected_net') != request['expected_net']
                        or original.get('partner_designator') != request.get('partner_designator')):
                    raise ValueError('Saved service evidence identity or source mismatch')
                if (original.get('success') is not False
                        or original.get('dispatch_attempted') is not True
                        or original.get('process_identity_verified') is not True
                        or type(original.get('launcher_exit_code')) is not int
                        or original['launcher_exit_code'] != 0
                        or original.get('service_receipt_persisted') is not True):
                    raise ValueError('Saved service evidence does not establish a returned failed request')
                receipt = native.read_partial_receipt(receipt_path, request)
                if (receipt.get('stage') != 'readback_failed'
                        or receipt.get('error') != 'old_free_copper_changed_or_snapshot_incomplete'
                        or receipt.get('success') is not False
                        or receipt.get('return_verified') is not False
                        or receipt.get('old_copper_preserved') is not False
                        or any(receipt.get(key) is not True for key in (
                            'route_attempted', 'selection_attempted', 'selection_restored',
                            'lock_preroutes_verified', 'before_snapshot_complete', 'after_snapshot_complete'))
                        or receipt != original.get('native_receipt')):
                    raise ValueError('Exact known copper-validation failure and complete native return evidence required')
                launches = pending.get('launches')
                if (not isinstance(launches, list) or len(launches) != 1
                        or launches[0].get('phase') != 'situs_partial'
                        or type(launches[0].get('pid')) is not int or launches[0]['pid'] <= 0
                        or type(launches[0].get('exit_code')) is not int or launches[0]['exit_code'] != 0):
                    raise ValueError('Pending journal lacks the exact finished launcher')
                with emergency_stop.WinProcess(target_pid) as process:
                    before = _identity(process, exe, target_pid, int(expected_creation_filetime))
                    if not emergency_stop._same_identity(before, pending.get('process_identity', {})):
                        raise ValueError('Current process differs from the original pending invocation')
                    readiness = _environment(exe, target_pid)
                    after = _identity(process, exe, target_pid, int(expected_creation_filetime))
                    if not emergency_stop._same_identity(before, after):
                        raise ValueError('Process changed during read-only reconciliation')
                    if script_recovery.blocked_state(exchange) != pending:
                        raise ValueError('Pending recovery state changed during reconciliation')
                    proof_path = folder / 'known_return_reconciliation.json'
                    proof = dict(schema_version=1, operation_id=operation_id,
                        kind='returned_native_request_with_copper_validation_failure',
                        source_sha256=source_hash, process_identity=after, readiness=readiness,
                        native_stage=receipt['stage'], native_error=receipt['error'],
                        before_copper_sha256=receipt['before_copper_sha256'],
                        after_copper_sha256=receipt['after_copper_sha256'],
                        executor_return_known=True, native_idle_verified_at_original_return=True,
                        native_idle_currently_verified=False, routing_success=False,
                        copper_validation_passed=False, diagnostics_required=True,
                        original_result_preserved=True, native_dispatch_performed=False)
                    _write(proof_path, proof)
                    recovered = dict(pending, phase='recovered',
                        recovery_basis='selected_tp_known_return_with_copper_validation_failure',
                        reconciliation_receipt=str(proof_path),
                        executor_recovery_performed=False, routing_validation_passed=False)
                    script_recovery._write_state(exchange, recovered)
                    result.update(reconciled=True, executor_return_known=True,
                        recovery_latch_cleared=True, original_result_preserved=True,
                        diagnostics_required=True, reconciliation_receipt=str(proof_path),
                        original_route_error=receipt['error'])
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        return dict(result, success=False, error=str(error))
