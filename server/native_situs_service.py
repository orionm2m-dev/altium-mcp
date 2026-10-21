"""Fixed JSON requests to the registered Situs module; no scripts or UI input."""
from __future__ import annotations
import asyncio
import json
import ntpath
import os
from pathlib import Path
import re
import time
import uuid

import emergency_stop
import native_situs_protocol as protocol
import script_recovery
from situs_native_settings_service import _identity, _environment, normalize_public_filetime


def _write(path, data):
    pending = path.with_suffix('.tmp')
    with pending.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(path)


def _payload(action, payload):
    if not isinstance(payload, dict):
        raise ValueError('payload must be an object')
    if action == 'read_settings':
        if payload:
            raise ValueError('read_settings payload must be empty')
        return
    if action != 'set_settings' or set(payload) != {'expected_snapshot_hash', 'changes'}:
        raise ValueError('set_settings requires expected_snapshot_hash and changes')
    if not isinstance(payload['expected_snapshot_hash'], str) or not re.fullmatch('[0-9a-f]{64}', payload['expected_snapshot_hash']):
        raise ValueError('Expected opaque native snapshot hash required')
    changes = payload['changes']
    if not isinstance(changes, dict) or not changes or not set(changes) <= {'name', 'description', 'prefer_orthogonal', 'layer_directions'}:
        raise ValueError('Only supported nonempty changes are accepted')
    for field, minimum, maximum in (('name', 1, 256), ('description', 0, 4096)):
        if field in changes:
            value = changes[field]
            # Native System.String.Length counts UTF-16 code units, not Python
            # Unicode scalar values. Reject unsupported text before any launch.
            if (not isinstance(value, str) or '\0' in value
                    or (field == 'name' and not value.strip())):
                raise ValueError('Invalid ' + field)
            if not minimum <= len(value.encode('utf-16-le')) // 2 <= maximum:
                raise ValueError('Invalid ' + field)
    if 'prefer_orthogonal' in changes and type(changes['prefer_orthogonal']) is not bool:
        raise ValueError('prefer_orthogonal must be boolean')
    if 'layer_directions' in changes:
        layers = changes['layer_directions']
        if not isinstance(layers, list) or not 1 <= len(layers) <= 256:
            raise ValueError('layer_directions must contain 1 through 256 entries')
        seen = set()
        for item in layers:
            if (not isinstance(item, dict) or set(item) != {'layer_id', 'direction'}
                    or type(item['layer_id']) is not int or not 0 <= item['layer_id'] <= 4294967295
                    or type(item['direction']) is not int or not 0 <= item['direction'] <= 11
                    or item['layer_id'] in seen):
                raise ValueError('Invalid or duplicate layer direction')
            seen.add(item['layer_id'])


async def control(exe, exchange_dir, command_lock, *, action, pcb_path, target_pid,
                  expected_creation_filetime, payload=None, operation_id='', timeout_seconds=15):
    """One native dispatch with a durable receipt; repeated IDs return history.

    Caller passes the shared bridge lock. Cancellation retains both locks until
    the bounded native observation finishes. Neither unknown outcomes nor stale
    state are retried. A completed action does not establish persistence.
    """
    result = dict(success=False, dispatch_attempted=False, outcome_unknown=False,
                  retry_allowed=False, document_saved=False, replayed=False,
                  current_state_rechecked=False)
    try:
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30:
            raise ValueError('timeout_seconds must be an integer from 1 through 30')
        payload = {} if payload is None else payload
        _payload(action, payload)
        created = normalize_public_filetime(expected_creation_filetime)
        operation_id = uuid.uuid4().hex if operation_id == '' else operation_id
        request = dict(schema_version=1, request_id=operation_id, action=action,
                       pcb_path=ntpath.normpath(pcb_path), pid=target_pid,
                       creation_filetime=created, exe=ntpath.normpath(str(exe)), payload=payload)
        protocol.validate_request(request)
        exchange = Path(exchange_dir).resolve()
        if not exchange.is_dir():
            raise ValueError('Configured exchange directory does not exist')
        folder = exchange / 'native_situs_control' / operation_id
        built = protocol.build_request(request, str(folder / 'request.json'))
        result.update(operation_id=operation_id, action=action, receipt_directory=str(folder))
        async with command_lock:
            with emergency_stop.emergency_claim(exchange):
                if folder.exists():
                    if (folder / 'request.json').read_bytes() != built['request_bytes']:
                        raise ValueError('operation_id is bound to a different request')
                    previous = folder / 'result.json'
                    if previous.exists():
                        return dict(json.loads(previous.read_text(encoding='utf-8')),
                                    replayed=True, current_state_rechecked=False)
                    return dict(result, replayed=True, outcome_unknown=True,
                                error='Earlier operation lacks final receipt; no replay')
                blocked = script_recovery.blocked_state(exchange)
                if blocked:
                    return dict(result, blocked=True, error='Executor recovery is unresolved',
                                recovery_phase=blocked.get('phase'))
                with emergency_stop.WinProcess(target_pid) as process:
                    before = _identity(process, exe, target_pid, int(created))
                    _environment(exe, target_pid)
                    folder.mkdir(parents=True, exist_ok=False)
                    with (folder / 'request.json').open('xb') as stream:
                        stream.write(built['request_bytes'])
                        stream.flush()
                        os.fsync(stream.fileno())
                    worker = asyncio.create_task(_execute(exe, exchange, folder, process, before,
                                                          request, built, result, timeout_seconds))
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
        return dict(result, error=str(error))


async def _execute(exe, exchange, folder, process, before, request, built, result, timeout):
    state = None
    try:
        _identity(process, exe, request['pid'], int(request['creation_filetime']))
        _environment(exe, request['pid'])
        state = dict(phase='native_module_pending', token=request['request_id'],
                     native_module_receipt=built['response_path'], process_identity=before, launches=[])
        script_recovery._write_state(exchange, state)
        result.update(dispatch_attempted=True, outcome_unknown=True)
        _write(folder / 'result.json', dict(result, error='Native dispatch pending'))
        launcher = script_recovery._launch(str(exe), built['argument'])
        launch = dict(phase='native_module', pid=launcher.pid, exit_code=None)
        state['launches'].append(launch)
        script_recovery._write_state(exchange, state)
        deadline = time.monotonic() + timeout
        response = None
        while True:
            code = launcher.poll()
            if code is not None:
                launch['exit_code'] = code
                script_recovery._launches.pop(launcher.pid, None)
            try:
                response = json.loads(Path(built['response_path']).read_text(encoding='utf-8'))
            except (OSError, ValueError, UnicodeError):
                response = None
            classified = protocol.classify_response(request, built['request_sha256'], response, code)
            if (code is not None and classified['completed']) or time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.1)
        after = _identity(process, exe, request['pid'], int(request['creation_filetime']))
        if not emergency_stop._same_identity(before, after):
            raise ValueError('Process identity changed during dispatch')
        result['readiness_after'] = _environment(exe, request['pid'])
        result.update(classified, launcher_exit_code=code, native_response=response,
                      process_identity_verified=True)
        verified = code == 0 and classified['completed'] and not classified['outcome_unknown']
        result.update(success=bool(verified and classified.get('action_success')),
                      outcome_unknown=not verified, completion_verified=verified,
                      current_state_rechecked=bool(verified and classified.get('action_success')))
        if verified:
            state.update(phase='recovered', recovery_basis='native_module_receipt_verified',
                         executor_recovery_performed=False)
        else:
            result['error'] = 'Native completion, outcome or launcher exit was not verified'
    except Exception as error:
        result.update(success=False, error=str(error), outcome_unknown=result['dispatch_attempted'])
    # Publish the durable result before clearing the recovery latch. A failed
    # receipt write must never leave an operation apparently recovered.
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
            state.update(phase='native_module_pending')
            state.pop('recovery_basis', None)
            state.pop('executor_recovery_performed', None)
            try:
                script_recovery._write_state(exchange, state)
            except (OSError, ValueError, TypeError):
                pass  # The pre-dispatch pending checkpoint remains authoritative.
        try:
            _write(folder / 'result.json', result)
        except (OSError, ValueError, TypeError):
            pass  # Return failure without converting a receipt error to success.
    return result
