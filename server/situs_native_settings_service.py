"""Serialized public-service backend for the fixed native LockPreRoutes program.

No app access occurs at import. The registered tool supplies the existing bridge
lock. Only the fixed builder is accepted; there is no arbitrary source argument.
A native receipt verifies the setting, not preference persistence or routing.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

import emergency_stop
import script_guard
import script_lint
import script_recovery
import situs_native_settings as settings


TERMINAL_STAGES = {"completed", "guard_failed", "expected_state_mismatch", "readback_failed"}


def _write(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(path)


def _identity(process, exe, pid, created):
    value = process.identity()
    emergency_stop._valid_identity(value, exe, pid)
    if type(value.get("creation_filetime")) is not int or value["creation_filetime"] != created:
        raise ValueError("Expected process creation FILETIME does not match")
    return value


def _environment(exe, pid):
    value = script_recovery.inspect_environment(exe)
    if (value.get("ready") is not True or value.get("target_pid") != pid
            or value.get("configured_process_ids") != [pid]):
        raise ValueError(value.get("reason") or "Exactly one ready configured Altium instance is required")
    if script_guard.blocking_crash_report(exe):
        raise ValueError("A native crash report blocks dispatch")


def _base(request, pid, created):
    return dict(success=False, operation_id=request["operation_id"], action=request["action"],
                setting="lock_preroutes", scope="global_pcb_system_options",
                pcb_path=request["pcb_path"], target_pid=pid,
                process_creation_filetime_decimal=str(created),
                dispatch_attempted=False, completion_verified=False,
                setting_readback_verified=False, process_identity_verified=False,
                pid_verified_by_native=False, outcome_unknown=False,
                retry_allowed=False, document_save_requested=False,
                persistence_verified=False, replayed=False, current_state_rechecked=False)


def _preflight(source):
    # The fixed standalone entry point is renamed to avoid the sandbox-specific
    # Run collision in the common linter. No caller-supplied code is introduced.
    if source.count("procedure Run;") != 1:
        raise ValueError("Fixed native template entry point changed")
    source = source.replace("procedure Run;", "procedure RunSitusSettings;", 1)
    report = script_lint.lint("", source)
    if report.get("success") is not True:
        raise ValueError("Fixed native settings program failed static preflight")
    return source


async def control(exe, exchange_dir, command_lock, *, action, pcb_path, target_pid,
                  expected_creation_filetime, operation_id="", expected_checked=None,
                  checked=None, timeout_seconds=15):
    """Inspect/set one global option with an exact document and process guard.

    expected_creation_filetime is a decimal STRING to avoid JSON integer
    precision loss. operation_id is empty (allocate once) or 32 lowercase hex.
    Repeating an id returns historical evidence without dispatch or fresh read.
    The caller must pass altium_bridge._command_lock, not a newly created lock.
    Uses the existing hidden launcher and recovery latch, never shared Sandbox.
    Unknown outcomes stay blocked; this method never Stop/retries/terminates.
    """
    result = {"success": False, "dispatch_attempted": False, "outcome_unknown": False,
              "retry_allowed": False}
    try:
        if type(target_pid) is not int or target_pid <= 0:
            raise ValueError("target_pid must be a positive integer")
        if (not isinstance(expected_creation_filetime, str)
                or re.fullmatch(r"[1-9][0-9]{0,19}", expected_creation_filetime) is None
                or int(expected_creation_filetime) >= 2 ** 64):
            raise ValueError("expected_creation_filetime must be a positive decimal FILETIME string")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be an integer from 1 through 30")
        created = int(expected_creation_filetime)
        operation_id = uuid.uuid4().hex if operation_id == "" else operation_id
        request = settings.build_settings_request(pcb_path, operation_id, action, expected_checked, checked)
        result = _base(request, target_pid, created)
        exchange = Path(exchange_dir).resolve()
        if not exchange.is_dir():
            raise ValueError("Configured exchange directory does not exist")
        if any(c in str(exe) + str(exchange) for c in '\r\n\0"|'):
            raise ValueError("Unsafe configured executable or exchange path")
        binding = dict(request=request, target_pid=target_pid, creation_filetime_decimal=str(created),
                       executable=emergency_stop._canonical(exe))
        async with command_lock:
            with emergency_stop.emergency_claim(exchange):
                folder = exchange / "situs_native_settings" / operation_id
                if folder.exists():
                    saved = json.loads((folder / "request.json").read_text(encoding="utf-8"))
                    if saved != binding:
                        raise ValueError("operation_id is already bound to a different request or process")
                    summary = folder / "result.json"
                    if summary.exists():
                        previous = json.loads(summary.read_text(encoding="utf-8"))
                        return dict(previous, replayed=True, current_state_rechecked=False)
                    return dict(result, outcome_unknown=True, replayed=True,
                                error="Earlier operation has no final service receipt; no replay",
                                receipt_directory=str(folder))
                blocked = script_recovery.blocked_state(exchange)
                if blocked:
                    return dict(result, blocked=True, error="Script recovery is unresolved",
                                recovery_phase=blocked.get("phase"))
                with emergency_stop.WinProcess(target_pid) as process:
                    before = _identity(process, exe, target_pid, created)
                    _environment(exe, target_pid)
                    folder.mkdir(parents=True, exist_ok=False)
                    _write(folder / "request.json", binding)
                    result["receipt_directory"] = str(folder)
                    try:
                        return await _execute(exe, exchange, folder, process, before, request,
                                              result, created, timeout_seconds)
                    except asyncio.CancelledError:
                        result.update(error="Operation cancelled; no replay", outcome_unknown=result["dispatch_attempted"])
                        _write(folder / "result.json", result)
                        raise
                    except Exception as error:
                        result.update(error=str(error), outcome_unknown=result["dispatch_attempted"])
                        _write(folder / "result.json", result)
                        return result
    except (OSError, ValueError, TypeError) as error:
        return dict(result, error=str(error))


async def _execute(exe, exchange, folder, process, before, request, result, created, timeout):
    native_path = folder / "native.json"
    source = _preflight(settings.build_lock_preroutes_program(
        request["pcb_path"], request["operation_id"], native_path, request["action"],
        request["expected_checked"], request["requested_checked"]))
    pas = folder / "SitusSettings.pas"
    project = folder / "SitusSettings.PrjScr"
    pas.write_text(source, encoding="utf-8")
    project.write_text("[Design]\nVersion=1.0\n\n[Document1]\nDocumentPath=SitusSettings.pas\n", encoding="utf-8")
    result["source_sha256"] = hashlib.sha256(pas.read_bytes()).hexdigest()
    _identity(process, exe, result["target_pid"], created)
    _environment(exe, result["target_pid"])
    # Latch before Popen: crash/cancellation across dispatch cannot permit replay.
    state = dict(phase="native_settings_pending", token=request["operation_id"],
                 native_settings_receipt=str(native_path), launches=[], process_identity=before)
    script_recovery._write_state(exchange, state)
    result["dispatch_attempted"] = True
    _write(folder / "result.json", dict(result, outcome_unknown=True, error="Dispatch pending"))
    launcher = script_recovery._launch(str(exe), '-RScriptingSystem:RunScript(ProjectName="'
        + str(project) + '"|ProcName="SitusSettings>RunSitusSettings")')
    state["launches"].append(dict(phase="native_settings", pid=launcher.pid, exit_code=None))
    script_recovery._write_state(exchange, state)
    deadline = time.monotonic() + timeout
    native = None
    while True:
        exit_code = launcher.poll()
        if exit_code is not None:
            state["launches"][0]["exit_code"] = exit_code
            script_recovery._launches.pop(launcher.pid, None)
        try:
            native = settings.read_settings_receipt(native_path, request)
        except (OSError, ValueError, UnicodeError):
            native = None  # SaveToFile may still be writing; bounded observation only.
        if exit_code is not None and native and native.get("stage") in TERMINAL_STAGES:
            break
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(0.1)
    after = _identity(process, exe, result["target_pid"], created)
    if not emergency_stop._same_identity(before, after):
        raise ValueError("Process identity changed during native operation")
    _environment(exe, result["target_pid"])
    result.update(process_identity_verified=True, launcher_exit_code=exit_code,
                  native_receipt=native, current_state_rechecked=bool(native and native.get("setting_readback_verified")))
    safe_return = (exit_code == 0 and native and native.get("stage") in TERMINAL_STAGES
                   and (native.get("success") is True or native.get("setter_attempted") is False))
    if safe_return:
        result.update(success=native["success"], completion_verified=native["completion_verified"],
                      setting_readback_verified=native["setting_readback_verified"],
                      outcome_unknown=False, error=native["error"])
        state.update(phase="recovered", recovery_basis="fixed_settings_operation_returned",
                     executor_recovery_performed=False)
    else:
        result.update(outcome_unknown=True, error="Native settings completion or launcher exit was not verified")
    script_recovery._write_state(exchange, state)
    _write(folder / "result.json", result)
    return result
