"""One qualified wizard cancel, then resume the pinned pending Stop. No retry.

Public wrapper must hold bridge._command_lock and emergency_stop.emergency_claim.
Imports perform no native calls. Legacy evidence describes a CURRENT exact Stop
command; it does not claim retrospective proof of a PID-only launch receipt.
"""
import asyncio
import hashlib
import json
import re
import time
from contextlib import ExitStack
from pathlib import Path

STOP_ARGUMENT = '-REditScript:Stop'
BLOCKING_CLASSES = {'#32770', 'TMessageForm', 'TShowException', 'TPCBImportWizard'}


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _read_latch(exchange):
    return (Path(exchange) / 'script_recovery_state.json').read_bytes()


def _pending_state(raw, target_pid):
    state = json.loads(raw)
    launches = state.get('launches', [])
    if (state.get('phase') != 'stop_pending'
            or not isinstance(state.get('token'), str)
            or re.fullmatch(r'[0-9a-f]{32}', state['token']) is None
            or len(launches) != 1 or launches[0].get('phase') != 'stop'
            or type(launches[0].get('pid')) is not int
            or launches[0]['pid'] <= 0 or launches[0]['pid'] == target_pid
            or launches[0].get('exit_code') is not None
            or state.get('environment', {}).get('target_pid') != target_pid):
        raise ValueError('Exactly one recorded pending Stop for this main session is required')
    return state, launches[0]['pid']


def _command_line(pid):
    # Read only this exact WMI process object. No shell or child process is used.
    import win32com.client
    item = win32com.client.GetObject('winmgmts:root/cimv2').Get(
        "Win32_Process.Handle='%d'" % pid)
    if int(item.ProcessId) != pid:
        raise ValueError('WMI process PID mismatch')
    return str(item.CommandLine or '')


def _exact_stop_command(command, exe):
    # This is the exact format emitted by script_recovery._launch. Reject extra
    # switches or another executable; do not accept a substring match.
    return command == '"' + str(exe) + '" ' + STOP_ARGUMENT


def _ready_main(environment, identity):
    pid = identity['pid']
    windows = environment.get('process_windows', {})
    rows = windows.get(pid, windows.get(str(pid), []))
    return (environment.get('ready') is True
            and environment.get('target_pid') == pid
            and environment.get('configured_process_ids') == [pid]
            and environment.get('foreground_confirmed') is True
            and any(w.get('class') == 'TDocumentForm' for w in rows)
            and not any(w.get('class') in BLOCKING_CLASSES for w in rows)
            and not environment.get('dialog_handles')
            and not environment.get('script_errors'))


class _VerifiedPendingStop:
    """Internal one-use capability; never accepted from public JSON arguments."""
    def __init__(self, exe, exchange, latch, stop, main, stop_identity,
                 main_identity, command_line, cancelled):
        self.exe, self.exchange, self.latch = exe, Path(exchange), latch
        self.stop, self.main = stop, main
        self.stop_identity, self.main_identity = stop_identity, main_identity
        self.command_line, self.cancelled, self.consumed = command_line, cancelled, False

    def consume(self, exe, exchange, old, environment):
        import emergency_stop
        if self.consumed:
            raise ValueError('Pending Stop proof was already consumed')
        self.consumed = True
        if (emergency_stop._canonical(exe) != emergency_stop._canonical(self.exe)
                or Path(exchange).resolve() != self.exchange.resolve()
                or _read_latch(exchange) != self.latch or old != json.loads(self.latch)):
            raise ValueError('Recovery latch or target changed')
        identity = self.main.identity()
        emergency_stop._valid_identity(identity, exe, self.main_identity['pid'])
        if not emergency_stop._same_identity(identity, self.main_identity):
            raise ValueError('Main process identity changed')
        result = self.stop.wait(0)
        if result.get('wait_status') != 0 or result.get('exit_code') != 0:
            raise ValueError('Pinned Stop has not exited successfully')
        if not self.cancelled or not _ready_main(environment, identity):
            raise ValueError('The same main session is not ready after wizard cancellation')
        return {'kind': 'current_exact_pending_stop_exit', 'legacy_historical_identity_verified': False,
                'stop_identity': dict(self.stop_identity,
                    creation_filetime_decimal=str(self.stop_identity['creation_filetime'])),
                'main_identity': dict(self.main_identity,
                    creation_filetime_decimal=str(self.main_identity['creation_filetime'])),
                'command_line': self.command_line, 'exit_code': 0,
                'same_handle_wait': True, 'prior_latch_sha256': _hash(self.latch),
                'manual_stop_confirmed': False, 'stop_redispatched': False}


async def cancel_and_resume_locked(exe, exchange, target_pid, wizard_hwnd,
        observation_token, acknowledge_managed_wizard=False, wait_seconds=8,
        timeout_seconds=30, dispatch_timeout_seconds=8, *, process_factory=None,
        command_reader=None, wizard=None, recover_private=None):
    """Called only inside both locks. Never launch Stop, close documents or kill."""
    import emergency_stop
    import script_recovery
    import import_wizard_control
    result = {'recovered': False, 'blocked': True, 'manual_stop_confirmed': False,
              'stop_redispatched': False, 'save_performed': False,
              'process_termination_requested': False, 'retry_performed': False}
    try:
        if acknowledge_managed_wizard is not True:
            raise ValueError('Explicit managed-wizard cancellation acknowledgement is required')
        if (type(target_pid) is not int or target_pid <= 0
                or type(wizard_hwnd) is not int or wizard_hwnd <= 0):
            raise ValueError('A positive exact main PID is required')
        if (type(wait_seconds) is not int or not 1 <= wait_seconds <= 15
                or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60
                or type(dispatch_timeout_seconds) is not int
                or not 1 <= dispatch_timeout_seconds <= 15):
            raise ValueError('Invalid bounded wait/probe timeout')
        raw = _read_latch(exchange)
        state, stop_pid = _pending_state(raw, target_pid)
        factory = process_factory or emergency_stop.WinProcess
        read_command = command_reader or _command_line
        with ExitStack() as stack:
            main = stack.enter_context(factory(target_pid, synchronize_access=True))
            stop = stack.enter_context(factory(stop_pid, synchronize_access=True))
            main_identity, stop_identity = main.identity(), stop.identity()
            emergency_stop._valid_identity(main_identity, exe, target_pid)
            emergency_stop._valid_identity(stop_identity, exe, stop_pid)
            command = read_command(stop_pid)
            if not _exact_stop_command(command, exe):
                raise ValueError('Current launcher is not the exact configured Stop command')
            for handle, previous in [(main, main_identity), (stop, stop_identity)]:
                current = handle.identity()
                emergency_stop._valid_identity(current, exe, previous['pid'])
                if not emergency_stop._same_identity(current, previous):
                    raise ValueError('Pinned process identity changed during capture')
            if _read_latch(exchange) != raw:
                raise ValueError('Recovery latch changed before cancellation')
            result['capture'] = {'stop_pid': stop_pid,
                'stop_creation_filetime_decimal': str(stop_identity['creation_filetime']),
                'main_pid': target_pid,
                'main_creation_filetime_decimal': str(main_identity['creation_filetime']),
                'exact_stop_command_verified': True, 'command_line': command,
                'stop_identity': dict(stop_identity, creation_filetime_decimal=str(stop_identity['creation_filetime'])),
                'main_identity': dict(main_identity, creation_filetime_decimal=str(main_identity['creation_filetime'])),
                'prior_latch_sha256': _hash(raw),
                'legacy_historical_identity_verified': False}
            # This file is evidence, not authorization; latch stays blocked.
            capture_path = Path(exchange) / ('pending_stop_capture_' + str(state['token']) + '.json')
            capture_path.write_text(json.dumps(result['capture'], indent=2), encoding='utf-8')
            service = wizard or import_wizard_control.WizardControl(
                import_wizard_control.NativeAdapter(), exe, lambda: _read_latch(exchange),
                import_wizard_control.ObservationStore(exchange))
            cancel = service.cancel(target_pid, str(wizard_hwnd), observation_token, True)
            result['wizard_cancel'] = cancel
            if (cancel.get('wizard_closed_verified') is not True
                    or cancel.get('recovery_latch_unchanged') is not True
                    or cancel.get('after', {}).get('same_process_alive') is not True):
                raise ValueError('Wizard cancellation or main identity was not verified; no probe')
            if _read_latch(exchange) != raw:
                raise ValueError('Recovery latch changed after cancellation')
            deadline = time.monotonic() + wait_seconds
            while True:
                stopped = stop.wait(0)
                if stopped.get('wait_status') == 0:
                    break
                if stopped.get('wait_status') != 258:
                    raise ValueError('Unexpected Stop wait status')
                if time.monotonic() >= deadline:
                    raise TimeoutError('Same-handle Stop exit remains pending; no probe')
                await asyncio.sleep(.1)
            result['stop_exit'] = stopped
            if stopped.get('exit_code') != 0:
                raise ValueError('Pinned Stop exited nonzero; no probe')
            proof = _VerifiedPendingStop(exe, exchange, raw, stop, main,
                stop_identity, main_identity, command, True)
            recover = recover_private or script_recovery._recover_emergency_serialized
            recovery_result = await recover(exe, exchange, timeout_seconds,
                dispatch_timeout_seconds, False, verified_stop=proof)
            result.update(recovery_result)
            return result
    except asyncio.CancelledError:
        # Existing stop_pending latch remains blocked unless the private probe
        # already wrote its own cancelled_unresolved latch.
        raise
    except Exception as exc:
        result['error'] = str(exc)
        return result
