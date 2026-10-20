"""Two-phase, exact-handle emergency termination. No native calls at import.

Caller must hold the bridge command lock. Acknowledgements identify the managed
session and accept loss of unsaved memory; disk copies do not preserve that memory.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import time

import script_guard
import script_recovery

TTL_SECONDS = 300
WAIT_MS = 5000
EXIT_CODE = 0xE0000001
_plans = {}
_EXTENSIONS = {".pcbdoc", ".schdoc", ".prjpcb", ".pcblib", ".schlib", ".prjscr",
               ".outjob", ".dsnwrk", ".prjmbd", ".mbsdoc", ".pas"}


class EmergencyClaimBusy(OSError):
    pass


@contextmanager
def emergency_claim(exchange):
    """Emergency/recovery-only cross-process lock; OS releases it on owner exit."""
    path = Path(exchange) / "emergency_stop.lock"
    with path.open("a+b") as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise EmergencyClaimBusy("Another emergency/recovery operation holds the claim; no action") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _canonical(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


class WinProcess:
    """One process HANDLE for identity checks, TerminateProcess and bounded wait."""
    def __init__(self, pid, terminate_access=False):
        import ctypes
        from ctypes import wintypes as w
        if os.name != "nt":
            raise OSError("Windows is required")
        self.c, self.w = ctypes, w
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.a = ctypes.WinDLL("advapi32", use_last_error=True)
        signatures = {
            "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "GetProcessId": ([w.HANDLE], w.DWORD),
            "GetProcessTimes": ([w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4, w.BOOL),
            "QueryFullProcessImageNameW": ([w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)], w.BOOL),
            "GetExitCodeProcess": ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
            "TerminateProcess": ([w.HANDLE, w.UINT], w.BOOL),
            "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "LocalFree": ([w.HANDLE], w.HANDLE),
        }
        for name, (args, result) in signatures.items():
            method = getattr(self.k, name)
            method.argtypes, method.restype = args, result
        self.a.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
        self.a.OpenProcessToken.restype = w.BOOL
        self.a.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                               w.DWORD, ctypes.POINTER(w.DWORD)]
        self.a.GetTokenInformation.restype = w.BOOL
        self.a.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.LPWSTR)]
        self.a.ConvertSidToStringSidW.restype = w.BOOL
        access = 0x1000 | (0x0001 | 0x00100000 if terminate_access else 0)
        self.handle = self.k.OpenProcess(access, False, pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle)
            self.handle = None

    def _check(self, ok):
        if not ok:
            raise self.c.WinError(self.c.get_last_error())

    def identity(self):
        c, w = self.c, self.w
        times = [w.FILETIME() for _ in range(4)]
        self._check(self.k.GetProcessTimes(self.handle, *(c.byref(t) for t in times)))
        size = w.DWORD(32768)
        path = c.create_unicode_buffer(size.value)
        self._check(self.k.QueryFullProcessImageNameW(self.handle, 0, path, c.byref(size)))
        code = w.DWORD()
        self._check(self.k.GetExitCodeProcess(self.handle, c.byref(code)))
        token = w.HANDLE()
        self._check(self.a.OpenProcessToken(self.handle, 0x0008, c.byref(token)))
        try:
            needed = w.DWORD()
            self.a.GetTokenInformation(token, 1, None, 0, c.byref(needed))
            if not needed.value:
                raise OSError("Token identity unavailable")
            data = c.create_string_buffer(needed.value)
            self._check(self.a.GetTokenInformation(token, 1, data, needed, c.byref(needed)))
            sid_pointer = c.cast(data, c.POINTER(c.c_void_p))[0]
            sid = w.LPWSTR()
            self._check(self.a.ConvertSidToStringSidW(sid_pointer, c.byref(sid)))
            try:
                user_sid = sid.value
            finally:
                self.k.LocalFree(c.cast(sid, w.HANDLE))
        finally:
            self.k.CloseHandle(token)
        return {"pid": int(self.k.GetProcessId(self.handle)),
                "creation_filetime": int(times[0].dwHighDateTime) << 32 | times[0].dwLowDateTime,
                "exe": _canonical(path.value), "user_sid": user_sid,
                "running": code.value == 259}

    def terminate(self, exit_code=EXIT_CODE):
        self._check(self.k.TerminateProcess(self.handle, exit_code))

    def wait(self, timeout_ms=WAIT_MS):
        status = int(self.k.WaitForSingleObject(self.handle, timeout_ms))
        if status == 0xFFFFFFFF:
            raise self.c.WinError(self.c.get_last_error())
        code = self.w.DWORD()
        self._check(self.k.GetExitCodeProcess(self.handle, self.c.byref(code)))
        return {"terminated_confirmed": status == 0, "wait_status": status,
                "exit_code": int(code.value) if status == 0 else None}


def _current_sid():
    with WinProcess(os.getpid()) as process:
        return process.identity()["user_sid"]


def _same_identity(a, b):
    return all(a.get(k) == b.get(k) for k in ("pid", "creation_filetime", "exe", "user_sid"))


def _valid_identity(identity, exe, pid):
    if (identity.get("pid") != pid or identity.get("exe") != _canonical(exe)
            or not identity.get("creation_filetime") or not identity.get("running")
            or identity.get("user_sid") != _current_sid()):
        raise ValueError("Target is not the running configured executable under this Windows user")


def _hung_window(hwnd):
    import ctypes
    from ctypes import wintypes as w
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.IsHungAppWindow.argtypes, api.IsHungAppWindow.restype = [w.HWND], w.BOOL
    api.SendMessageTimeoutW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM,
                                      w.UINT, w.UINT, ctypes.POINTER(ctypes.c_size_t)]
    api.SendMessageTimeoutW.restype = w.LPARAM
    reply = ctypes.c_size_t()
    # Both the OS hung indication and a bounded WM_NULL check must agree.
    return bool(api.IsHungAppWindow(hwnd)) and not bool(api.SendMessageTimeoutW(
        hwnd, 0, 0, 0, 2, 300, ctypes.byref(reply)))


def inspect_failure(exe, identity):
    """Read-only, PID-scoped visible evidence. Never answer Information/Save/Error."""
    import dialogs
    windows, evidence = [], []
    for hwnd in dialogs.top_windows():
        if not dialogs.is_visible(hwnd):
            continue
        pid, path = script_guard._window_process(hwnd)
        if pid != identity["pid"] or not path or _canonical(path) != _canonical(exe):
            continue
        cls, title = dialogs.window_class(hwnd), dialogs.window_text(hwnd)
        children = dialogs.children(hwnd)
        if not dialogs.is_visible(hwnd):
            continue
        text = " | ".join(dict.fromkeys(t.strip() for _, _, t in children if t.strip()))
        item = {"hwnd": hwnd, "pid": pid, "class": cls, "title": title, "text": text}
        windows.append(item)
        if cls == "TShowException":
            evidence.append(dict(item, kind="native_crash"))
        elif (cls in {"TMessageForm", "#32770"} and title.casefold() == "error"
              and "cannot close the current document" in text.casefold()
              and "script is stopped on a breakpoint" in text.casefold()):
            evidence.append(dict(item, kind="breakpoint_stop_blocker"))
        elif cls not in {"TMessageForm", "#32770"} and _hung_window(hwnd):
            evidence.append(dict(item, kind="hung_ui"))
    # A current Popen handle identifies a launcher even if its PID was reused.
    launcher = script_recovery._launches.get(identity["pid"])
    if launcher is not None and launcher.poll() is None and getattr(launcher, "_handle", None):
        with WinProcess(identity["pid"]) as probe:
            original = probe.handle
            probe.handle = int(launcher._handle)
            try:
                launched_identity = probe.identity()
            finally:
                probe.handle = original
        if _same_identity(launched_identity, identity):
            evidence.append({"pid": identity["pid"], "kind": "tracked_pending_launcher",
                             "creation_filetime": identity["creation_filetime"]})
    return {"windows": windows, "failure_evidence": evidence}


def _hash(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _backup(paths, folder):
    if not isinstance(paths, list) or not paths:
        raise ValueError("Explicit nonempty backup_paths of working Altium documents are required")
    sources = []
    for value in paths:
        path = Path(value)
        if not path.is_absolute() or not path.is_file() or path.suffix.casefold() not in _EXTENSIONS:
            raise ValueError("Each backup path must be an absolute existing Altium document: " + str(path))
        path = path.resolve()
        if path not in sources:
            sources.append(path)
    folder.mkdir(parents=True, exist_ok=False)
    result = []
    for index, source in enumerate(sources):
        before = _hash(source)
        destination = folder / (str(index) + "_" + source.name)
        shutil.copyfile(source, destination)
        if _hash(destination) != before or _hash(source) != before:
            raise ValueError("A document changed during disk backup: " + str(source))
        result.append({"source": str(source), "backup": str(destination),
                       "sha256": before, "size": source.stat().st_size})
    return result


def _verify_backups(entries):
    for entry in entries:
        for key in ("source", "backup"):
            path = Path(entry[key])
            if not path.is_file() or path.stat().st_size != entry["size"] or _hash(path) != entry["sha256"]:
                raise ValueError("Disk backup/source no longer matches the plan: " + str(path))


def _write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def _latch(exchange, plan, phase, **extra):
    state = {"phase": phase, "emergency_token": plan["token"],
             "emergency_identity": plan["identity"], "expires_at": plan["expires_at"],
             "require_fresh_instance": True, "unsaved_state": "unknown",
             "emergency_checkpoint": plan["checkpoint"], **extra}
    script_recovery._write_state(exchange, state)
    return state


def _refused(message, **extra):
    return {"success": False, "blocked": True, "error": message,
            "termination_requested": False, "terminated_confirmed": False,
            "unsaved_state": "unknown", **extra}


def _validate_attestation(value):
    """An explicit caller statement, never reconstructed native/live proof."""
    keys = {"reason", "prior_receipt_path", "prior_receipt_sha256"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("failure_attestation requires exactly reason, prior_receipt_path and prior_receipt_sha256")
    if not isinstance(value["reason"], str) or not value["reason"].strip():
        raise ValueError("A nonempty unresolved-failure reason is required")
    supplied = value["prior_receipt_sha256"]
    if (not isinstance(supplied, str) or len(supplied) != 64
            or any(c not in "0123456789abcdef" for c in supplied.lower())):
        raise ValueError("prior_receipt_sha256 must be a SHA-256 hex digest")
    if not isinstance(value["prior_receipt_path"], str):
        raise ValueError("prior_receipt_path must be an absolute existing file")
    path = Path(value["prior_receipt_path"])
    if not path.is_absolute() or not path.is_file():
        raise ValueError("prior_receipt_path must be an absolute existing file")
    path = path.resolve()
    if _hash(path) != supplied.lower():
        raise ValueError("Prior receipt no longer matches its supplied SHA-256")
    return {"reason": value["reason"].strip(), "prior_receipt_path": str(path),
            "prior_receipt_sha256": supplied.lower()}


def _record_native_incident(exchange, identity, observed):
    """Persist actually observed live evidence, not the caller's historical receipt."""
    if not observed["failure_evidence"]:
        return None
    folder = Path(exchange) / "emergency_stop" / "incidents"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (str(identity["pid"]) + "_" + secrets.token_hex(8) + ".json")
    _write_json(path, {"capture_time_unix": time.time(), "identity": identity,
                      "observed": observed, "live_failure_verified": True,
                      "meaning": "Failure observed at capture time; this is not proof it remains active later"})
    return str(path)


def _successful_previous_stop_has_exited(exe, state):
    """Release only the completed plan's TTL, never the script-dispatch latch."""
    result = state.get("termination_result")
    identity = state.get("emergency_identity")
    if (state.get("phase") != "emergency_terminated" or not isinstance(result, dict)
            or result.get("terminated_confirmed") is not True or result.get("wait_status") != 0
            or not isinstance(identity, dict) or identity.get("exe") != _canonical(exe)
            or type(identity.get("pid")) is not int or identity["pid"] <= 0
            or type(identity.get("creation_filetime")) is not int or identity["creation_filetime"] <= 0):
        return False
    try:
        with WinProcess(identity["pid"]) as process:
            current = process.identity()
            if (current.get("pid") != identity["pid"]
                    or type(current.get("creation_filetime")) is not int
                    or current["creation_filetime"] <= 0):
                return False
            return (current["creation_filetime"] != identity["creation_filetime"]
                    or current.get("running") is False)
    except OSError as error:
        return getattr(error, "winerror", None) == 87  # Invalid PID, not access denied.


def _prepare_locked(exe, exchange, target_pid, backup_paths, acknowledge_managed_session=False,
                    failure_attestation=None):
    if not acknowledge_managed_session:
        return _refused("Explicit acknowledgement that this exact PID is the managed task session is required")
    if isinstance(target_pid, bool) or not isinstance(target_pid, int) or target_pid <= 0:
        return _refused("A positive exact target PID is required")
    old = script_recovery.blocked_state(exchange) or {}
    safe_new_prepare = ((old.get("phase") == "emergency_failed_unresolved"
                         and old.get("termination_requested") is False)
                        or _successful_previous_stop_has_exited(exe, old))
    if (old.get("emergency_token") and old.get("expires_at", 0) > time.time()
            and not safe_new_prepare):
        return _refused("An emergency plan is already active; use its token or wait for expiry", state=old)
    try:
        with WinProcess(target_pid) as process:
            identity = process.identity()
            _valid_identity(identity, exe, target_pid)
            observed = inspect_failure(exe, identity)
            incident_path = _record_native_incident(exchange, identity, observed)
            if failure_attestation is not None:
                qualification = {"kind": "caller_attested_abandoned_debugger", "live_failure_verified": False,
                                 "attestation": _validate_attestation(failure_attestation),
                                 "meaning": "Caller confirms the managed session remains failed; historical evidence is not current native-state proof"}
            elif observed["failure_evidence"]:
                qualification = {"kind": "live_native_failure", "live_failure_verified": True}
            else:
                return _refused("No qualifying failure for this PID; no stop was prepared", observed=observed)
            token = secrets.token_hex(24)
            folder = Path(exchange) / "emergency_stop" / token
            copies = _backup(backup_paths, folder / "backups")
            if not _same_identity(identity, process.identity()):
                raise ValueError("Target identity changed during preparation")
            plan = {"token": token, "created_at": time.time(), "expires_at": time.time() + TTL_SECONDS,
                    "identity": identity, "observed": observed, "backups": copies,
                    "qualification": qualification, "native_incident_path": incident_path,
                    "checkpoint": str(folder / "plan.json"), "unsaved_state": "unknown",
                    "backup_scope": "disk_only", "document_association": "caller_supplied_in_memory_unverified",
                    "managed_session_acknowledged": True,
                    "previous_recovery_state": old}
            _write_json(Path(plan["checkpoint"]), plan)
            _latch(exchange, plan, "emergency_prepared")
            _plans[token] = json.dumps(plan, sort_keys=True)
            return {"success": True, "prepared": True, "plan_token": token, "plan": plan,
                    "qualification": qualification,
                    "warning": "Disk copies cannot preserve unsaved documents in memory. Termination may lose them.",
                    "termination_requested": False, "terminated_confirmed": False}
    except (OSError, ValueError, TypeError) as error:
        return _refused(str(error))


def _terminate_locked(exe, exchange, target_pid, plan_token, acknowledge_unsaved_loss=False):
    if not acknowledge_unsaved_loss:
        return _refused("acknowledge_unsaved_loss=true is required; disk backups do not preserve unsaved memory")
    frozen = _plans.pop(plan_token, None)  # A destructive attempt never reuses a token.
    if frozen is None:
        return _refused("Unknown, consumed or previous-server token; prepare a new plan")
    plan = json.loads(frozen)
    requested = False
    try:
        if target_pid != plan["identity"]["pid"] or time.time() >= plan["expires_at"]:
            raise ValueError("Plan PID mismatch or plan expired")
        if json.dumps(json.loads(Path(plan["checkpoint"]).read_text(encoding="utf-8")), sort_keys=True) != frozen:
            raise ValueError("Emergency checkpoint was modified")
        state = script_recovery.blocked_state(exchange) or {}
        if state.get("emergency_token") != plan_token:
            raise ValueError("Recovery latch no longer belongs to this plan")
        _verify_backups(plan["backups"])
        with WinProcess(target_pid, terminate_access=True) as process:
            identity = process.identity()
            _valid_identity(identity, exe, target_pid)
            if not _same_identity(identity, plan["identity"]):
                raise ValueError("PID identity differs from the prepared process")
            observed = inspect_failure(exe, identity)
            qualification = plan["qualification"]
            if qualification["kind"] == "caller_attested_abandoned_debugger":
                _validate_attestation(qualification["attestation"])
            elif not observed["failure_evidence"]:
                raise ValueError("Failure disappeared; no current qualifying native failure")
            _verify_backups(plan["backups"])
            if qualification["kind"] == "caller_attested_abandoned_debugger":
                _validate_attestation(qualification["attestation"])
            state = script_recovery.blocked_state(exchange) or {}
            if state.get("emergency_token") != plan_token:
                raise ValueError("Recovery latch changed during verification")
            _latch(exchange, plan, "emergency_termination_requested", observed_before_stop=observed)
            requested = True
            process.terminate(EXIT_CODE)
            result = process.wait(WAIT_MS)
            phase = "emergency_terminated" if result["terminated_confirmed"] else "emergency_exit_unconfirmed"
            _latch(exchange, plan, phase, termination_result=result)
            outcome = {"success": result["terminated_confirmed"], "blocked": True,
                       "termination_requested": True, **result, "target_identity": identity,
                       "unsaved_state": "unknown", "backup_scope": "disk_only",
                       "qualification": qualification, "observed_before_stop": observed,
                       "requires_fresh_instance_probe": True,
                       "next_action": "Launch the intended Altium session explicitly, then call recover_script_executor with manual_stop_confirmed=true. No mutation is replayed."}
            _write_json(Path(plan["checkpoint"]).with_name("result.json"), outcome)
            return outcome
    except BaseException as error:
        # Retain a durable latch even on cancellation, OS failure or unknown exit.
        try:
            current = script_recovery.blocked_state(exchange) or {}
            if current.get("emergency_token") == plan_token:
                _latch(exchange, plan, "emergency_failed_unresolved", error=str(error),
                       termination_requested=requested)
        except OSError:
            pass  # The pre-termination latch was written before TerminateProcess.
        if not isinstance(error, Exception):
            raise
        return _refused(str(error), termination_requested=requested, requires_fresh_instance_probe=True)


def recovery_block(exe, state):
    """No Stop/probe while the exact failed instance is alive or identity is unknown."""
    if not state.get("require_fresh_instance"):
        return None
    identity = state.get("emergency_identity")
    if not identity or identity.get("exe") != _canonical(exe):
        return "Emergency checkpoint identity is unavailable; no dispatch"
    try:
        with WinProcess(identity["pid"]) as process:
            current = process.identity()
            if (current.get("running") and current.get("pid") == identity["pid"]
                    and current.get("creation_filetime") == identity["creation_filetime"]):
                return "The exact emergency target is still running; no Stop/probe or mutation dispatched"
    except OSError as error:
        if getattr(error, "winerror", None) != 87:  # Invalid PID: old process no longer exists.
            return "Cannot prove the emergency target exited: " + str(error)
    return None


def prepare(exe, exchange, target_pid, backup_paths, acknowledge_managed_session=False,
            failure_attestation=None):
    try:
        with emergency_claim(exchange):
            return _prepare_locked(exe, exchange, target_pid, backup_paths, acknowledge_managed_session,
                                   failure_attestation)
    except OSError as error:
        return _refused(str(error))


def terminate(exe, exchange, target_pid, plan_token, acknowledge_unsaved_loss=False):
    try:
        with emergency_claim(exchange):
            return _terminate_locked(exe, exchange, target_pid, plan_token, acknowledge_unsaved_loss)
    except OSError as error:
        return _refused(str(error))
