"""Explicit normal cold start only. No script dispatch, recovery or native calls at import."""
import json
import os
from pathlib import Path
import subprocess
import time

import emergency_stop
import script_recovery

_started = {}  # Own Popen handles; never terminate or replay automatically.


def configured_instances(exe):
    """Read-only exact path inventory; uncertain same-name processes block start."""
    import ctypes
    from ctypes import wintypes as w
    if os.name != "nt":
        raise OSError("Windows is required")
    class ProcessEntry(ctypes.Structure):
        _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD),
                    ("th32ProcessID", w.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", w.DWORD), ("cntThreads", w.DWORD),
                    ("th32ParentProcessID", w.DWORD), ("pcPriClassBase", w.LONG),
                    ("dwFlags", w.DWORD), ("szExeFile", w.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    for name in ("Process32FirstW", "Process32NextW"):
        method = getattr(kernel, name)
        method.argtypes, method.restype = [w.HANDLE, ctypes.POINTER(ProcessEntry)], w.BOOL
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [w.HANDLE], w.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    result = {"configured": [], "uncertain": [], "other_paths": []}
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        valid = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while valid:
            if entry.szExeFile.casefold() == Path(exe).name.casefold():
                pid = int(entry.th32ProcessID)
                path = script_recovery._process_path(pid)
                item = {"pid": pid, "exe": path or None}
                if not path:
                    result["uncertain"].append(item)
                elif emergency_stop._canonical(path) == emergency_stop._canonical(exe):
                    result["configured"].append(item)
                else:
                    result["other_paths"].append(item)
            valid = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES is the only normal end.
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(snapshot)
    return result


def _owned_identity(process):
    """Read through the Popen handle itself, never trust a subsequently reused PID."""
    if not getattr(process, "_handle", None):
        raise OSError("The owned startup process handle is unavailable")
    # Initialize the existing read-only adapter with a harmless self handle,
    # borrow the owned launch handle only for identity, then restore for cleanup.
    with emergency_stop.WinProcess(os.getpid()) as reader:
        original = reader.handle
        reader.handle = int(process._handle)
        try:
            return reader.identity()
        finally:
            reader.handle = original


def _write_record(exchange, result):
    path = Path(exchange) / "altium_startup.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, indent=2), encoding="utf-8")
    temporary.replace(path)


def _initial_result():
    return {"success": False, "launched": False, "ui_ready_verified": False,
            "script_dispatched": False, "mutation_replayed": False,
            "requires_explicit_recovery_probe": True}


def _start_locked(executable, exchange):
    result = _initial_result()
    try:
        inventory = configured_instances(executable)
        if inventory["configured"] or inventory["uncertain"]:
            return dict(result, blocked=True, error="An existing or uncertain configured executable instance blocks cold start",
                        processes=inventory)
        pending = [pid for pid, process in _started.items() if process.poll() is None]
        if pending:
            return dict(result, blocked=True, error="A previous owned cold-start process is still running",
                        pending_process_ids=pending)
        latch = script_recovery.blocked_state(exchange)
        result.update(configured_exe=executable, recovery_latch_present=bool(latch),
                      started_at_unix=time.time(), status="launch_pending")
        _write_record(exchange, result)
        process = subprocess.Popen([executable], executable=executable,
            cwd=str(Path(executable).parent), shell=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _started[process.pid] = process
        result.update(launched=True, target_pid=process.pid, status="identity_pending")
        _write_record(exchange, result)
        identity = _owned_identity(process)
        if (identity.get("pid") != process.pid or identity.get("exe") != emergency_stop._canonical(executable)
                or type(identity.get("creation_filetime")) is not int or identity["creation_filetime"] <= 0
                or not identity.get("user_sid")):
            raise ValueError("Owned startup identity does not match the configured executable; no retry")
        exit_code = process.poll()
        running = identity.get("running") is True and exit_code is None
        result.update(identity=identity, creation_filetime_decimal=str(identity["creation_filetime"]),
                      exit_code=exit_code, success=running,
                      status="process_started_ui_unverified" if running else "startup_process_exited_or_unverified",
                      next_action="Inspect the current application state, then explicitly run recover_script_executor; no script or mutation was replayed.")
        _write_record(exchange, result)
        return result
    except (OSError, ValueError) as error:
        result.update(blocked=True, error=str(error), status="launch_failed_or_unverified")
        # The claim remains held during error receipt writes as well.
        try:
            _write_record(exchange, result)
        except OSError:
            pass
        return result


def start(exe, exchange):
    """Caller holds the bridge lock; all shared receipt writes hold the OS claim."""
    try:
        configured = Path(exe)
        if not configured.is_absolute() or not configured.is_file() or configured.suffix.casefold() != ".exe":
            raise ValueError("Configured Altium executable must be an absolute existing .exe")
        executable = str(configured.resolve())
        with emergency_stop.emergency_claim(exchange):
            return _start_locked(executable, exchange)
    except (OSError, ValueError) as error:
        # A rejected contender must never overwrite the active launch receipt.
        return dict(_initial_result(), blocked=True, error=str(error), status="not_launched")
