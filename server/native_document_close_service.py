"""One fixed native close of a clean, exact non-active PCB document.

The public wrapper supplies configured executable/exchange and the shared bridge
lock. No caller source, save, UI input, arbitrary process, retry or PID reopen.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import time
import uuid

import emergency_stop
import native_document_close as native
import script_lint
import script_recovery
from situs_native_settings_service import _identity, _environment, normalize_public_filetime


def _write(path, data):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _preflight(request, receipt_path):
    source = native.build_close_program(request, receipt_path)
    if source.count('procedure RunCleanDocumentClose;') != 1:
        raise ValueError('Fixed native document-close entry point changed')
    if script_lint.lint('', source).get('success') is not True:
        raise ValueError('Fixed native document-close program failed static preflight')
    return source


async def control(exe, exchange_dir, command_lock, *, active_pcb_path,
                target_document_path, target_pid, expected_creation_filetime,
                operation_id='', timeout_seconds=15):
    """Close at most one clean PCB through the fixed standalone native program.

    The active PCB must differ from the target and remain current. Only an exact
    correlated terminal native receipt plus owned launcher completion can clear
    the pending latch. Cancellation retains the shared lock, cross-process claim
    and pinned process HANDLE through bounded observation. A repeated ID returns
    historical evidence and never launches again.
    """
    result = dict(success=False, dispatch_attempted=False, outcome_unknown=False,
                  retry_allowed=False, replayed=False, current_state_rechecked=False,
                  save_requested=False, prompt_input_requested=False,
                  document_close_attempted=None, closed_verified=False,
                  current_pcb_preserved=False, other_documents_unchanged=False,
                  process_identity_verified=False, pid_verified_by_native=False)
    try:
        if type(target_pid) is not int or target_pid <= 0:
            raise ValueError('target_pid must be a positive integer')
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30:
            raise ValueError('timeout_seconds must be an integer from 1 through 30')
        created = normalize_public_filetime(expected_creation_filetime)
        operation_id = uuid.uuid4().hex if operation_id == '' else operation_id
        request = native.build_close_request(active_pcb_path, target_document_path, operation_id)
        exchange = Path(exchange_dir).resolve()
        if not exchange.is_dir():
            raise ValueError('Configured exchange directory does not exist')
        exe = str(exe)
        if not PureWindowsPath(exe).is_absolute():
            raise ValueError('Absolute configured executable required')
        folder = exchange / 'native_document_close' / operation_id
        receipt_path = folder / 'native.json'
        project = folder / 'CleanDocumentClose.PrjScr'
        if any(c in exe + str(project) for c in '\r\n\0"|'):
            raise ValueError('Unsafe configured executable or dedicated project path')
        source = _preflight(request, receipt_path)
        binding = dict(request=request, target_pid=target_pid,
                       creation_filetime_decimal=created,
                       executable=emergency_stop._canonical(exe))
        result.update(operation_id=operation_id,
                      active_pcb_path=request['active_pcb_path'],
                      target_document_path=request['target_document_path'],
                      target_pid=target_pid, creation_filetime_decimal=created,
                      receipt_directory=str(folder),
                      source_sha256=hashlib.sha256(source.encode('utf-8')).hexdigest())
        async with command_lock:
            with emergency_stop.emergency_claim(exchange):
                if folder.exists():
                    previous_request = json.loads((folder / 'request.json').read_text('utf-8'))
                    if previous_request != binding:
                        raise ValueError('operation_id is bound to a different request or process')
                    previous = folder / 'result.json'
                    if previous.exists():
                        return dict(json.loads(previous.read_text('utf-8')),
                                    replayed=True, current_state_rechecked=False)
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
                    with (folder / 'CleanDocumentClose.pas').open('x', encoding='utf-8', newline='') as stream:
                        stream.write(source)
                    with project.open('x', encoding='utf-8', newline='') as stream:
                        stream.write('[Design]\nVersion=1.0\n\n[Document1]\nDocumentPath=CleanDocumentClose.pas\n')
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
        state = dict(phase='native_document_close_pending', token=request['operation_id'],
                     native_document_close_receipt=str(receipt_path),
                     process_identity=before, launches=[])
        script_recovery._write_state(exchange, state)
        result.update(dispatch_attempted=True, outcome_unknown=True)
        _write(folder / 'result.json', dict(result, error='Fixed native close dispatch pending'))
        argument = ('-RScriptingSystem:RunScript(ProjectName="' + str(project)
                    + '"|ProcName="CleanDocumentClose>RunCleanDocumentClose")')
        launcher = script_recovery._launch(exe, argument)
        launch = dict(phase='native_document_close', pid=launcher.pid, exit_code=None)
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
                receipt = native.read_close_receipt(receipt_path, request)
                if receipt.get('stage') == 'not_observed':
                    receipt = None
            except (OSError, ValueError, UnicodeError):
                receipt = None
            terminal = receipt is not None and receipt['stage'] in native.TERMINAL_STAGES
            if (code is not None and terminal) or time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.1)
        after = _identity(process, exe, result['target_pid'], created)
        if not emergency_stop._same_identity(before, after):
            raise ValueError('Process identity changed during native document close')
        result['readiness_after'] = _environment(exe, result['target_pid'])
        result.update(native_receipt=receipt, launcher_exit_code=code,
                      process_identity_verified=True)
        verified = code == 0 and terminal and receipt['outcome_unknown'] is False
        result.update(success=bool(verified and receipt['success']),
                      outcome_unknown=not verified, service_completion_verified=bool(verified),
                      current_state_rechecked=bool(verified and receipt['success']))
        if receipt is not None:
            result.update(document_close_attempted=receipt['close_attempted'],
                          closed_verified=receipt['closed_verified'],
                          current_pcb_preserved=receipt['current_pcb_preserved'],
                          other_documents_unchanged=receipt['other_documents_unchanged'])
        if verified:
            result['error'] = receipt.get('error', '')
            state.update(phase='recovered', recovery_basis='fixed_clean_document_close_returned',
                         executor_recovery_performed=False)
        else:
            result['error'] = 'Native close completion, readback or launcher exit was not verified'
    except Exception as error:
        result.update(success=False, error=str(error), outcome_unknown=result['dispatch_attempted'])
    # Keep the pre-dispatch latch authoritative until a final service receipt is
    # durable. Receipt failures do not authorize another native attempt.
    try:
        result['service_receipt_persisted'] = True
        _write(folder / 'result.json', result)
        if state is not None:
            script_recovery._write_state(exchange, state)
    except (OSError, ValueError, TypeError) as error:
        result.update(success=False, outcome_unknown=result['dispatch_attempted'],
                      service_receipt_persisted=False,
                      error='Service receipt finalization failed: ' + str(error))
        if state is not None:
            state.update(phase='native_document_close_pending')
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
