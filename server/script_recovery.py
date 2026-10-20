"""Bounded, single-flight executor recovery; no GUI input or document writes."""
import asyncio
import json
import os
import re
from pathlib import Path
import subprocess
import time
import uuid

import script_guard

# Keep actual Popen handles. A timed-out X2 client must never be terminated:
# it may have become the application instance rather than a disposable helper.
_launches = {}


def blocked_state(exchange_dir):
    path = Path(exchange_dir) / "script_recovery_state.json"
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"phase": "unreadable_recovery_checkpoint"}
    if not isinstance(state, dict):
        return {"phase": "invalid_recovery_checkpoint"}
    return None if state.get("phase") == "recovered" else state


def _write_state(exchange_dir, state):
    path = Path(exchange_dir) / "script_recovery_state.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(path)


def _process_path(pid):
    """Query only process identity; pywin32 does not expose this API everywhere."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
        wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        path = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
            return path.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def inspect_environment(exe_path, required_stopped_pids=()):
    """Read-only process/window checks; responsiveness is NOT native idle."""
    if os.name != "nt":
        return {"ready": False, "reason": "Windows is required"}
    import ctypes
    from ctypes import wintypes
    import win32gui
    import win32process
    import dialogs

    expected = os.path.normcase(os.path.abspath(exe_path))
    pids = []
    process_ids = set(win32process.EnumProcesses())
    unresolved = sorted(process_ids.intersection(required_stopped_pids))
    if unresolved:
        return {"ready": False, "reason": "Recorded recovery launcher is still present",
                "unresolved_launch_ids": unresolved, "native_idle_verified": False}
    for pid in sorted(process_ids):
        if not pid:
            continue
        path = _process_path(pid)
        if path and os.path.normcase(os.path.abspath(path)) == expected:
            pids.append(pid)
    window_inventory = {pid: [] for pid in pids}
    for hwnd in dialogs.top_windows():
        if not dialogs.is_visible(hwnd):
            continue
        pid, _ = script_guard._window_process(hwnd)
        if pid in window_inventory:
            window_inventory[pid].append({"hwnd": hwnd,
                "class": dialogs.window_class(hwnd), "title": dialogs.window_text(hwnd)})
    modal_classes = {"#32770", "TMessageForm", "TShowException", "TPCBImportWizard"}
    crashes = []
    for pid, items in window_inventory.items():
        for window in items:
            if window["class"] == "TShowException":
                match = script_guard.classify_script_dialog(
                    window["title"], window["class"], dialogs.children(window["hwnd"]))
                if dialogs.is_visible(window["hwnd"]):
                    crashes.append(dict(match, hwnd=window["hwnd"], pid=pid))
    # A form may hide after enumeration or while child text is being read.
    # Remove it from both the blocking decision and the reported inventory.
    window_inventory = {pid: [w for w in items if dialogs.is_visible(w["hwnd"])]
                        for pid, items in window_inventory.items()}
    visible_handles = {w["hwnd"] for items in window_inventory.values() for w in items}
    crashes = [d for d in crashes if d["hwnd"] in visible_handles]
    interactive = [pid for pid, items in window_inventory.items()
                   if any(w["class"] not in modal_classes for w in items)]
    windowless = [pid for pid, items in window_inventory.items() if not items]
    result = {"ready": False, "configured_process_ids": pids,
              "visible_application_process_ids": interactive,
              "windowless_process_ids": windowless,
              "process_windows": window_inventory,
              "native_idle_verified": False}
    if crashes:
        result.update(script_errors=crashes,
            reason="Native exception dialog blocks recovery; inspect and close it manually. "
                   "No Stop, probe or report submission was dispatched.")
        return result
    if len(pids) != 1:
        if len(interactive) == 1 and windowless:
            result["reason"] = ("One visible Altium session plus windowless X2 processes "
                                "(possible pending dispatch clients); no new launch")
        else:
            result["reason"] = ("Cannot select one Altium process safely; visible sessions, "
                                "dialog-only processes and windowless clients are listed separately")
        return result
    pid = pids[0]
    windows = [item["hwnd"] for item in window_inventory[pid]]
    modal = [h for h in windows
             if dialogs.is_visible(h) and dialogs.window_class(h) in modal_classes]
    result.update(target_pid=pid, dialog_handles=modal)
    foreground = win32gui.GetForegroundWindow()
    result["foreground_confirmed"] = bool(foreground) and (
        win32process.GetWindowThreadProcessId(foreground)[1] == pid)
    if modal or not result["foreground_confirmed"]:
        result["reason"] = "Bring Altium forward and handle its dialog manually"
        return result
    user32 = ctypes.windll.user32
    user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT,
        wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
        ctypes.POINTER(ctypes.c_size_t)]
    user32.SendMessageTimeoutW.restype = wintypes.LPARAM
    reply = ctypes.c_size_t()
    result["ui_responsive"] = bool(user32.SendMessageTimeoutW(
        foreground, 0, 0, 0, 2, 500, ctypes.byref(reply)))
    result["ready"] = result["ui_responsive"]
    if not result["ready"]:
        result["reason"] = "Altium UI is not responding; idle cannot be established"
    return result


def _launch(exe, argument):
    # Preserve Altium's raw process syntax without cmd.exe, shell escaping or
    # inherited MCP pipes. The returned handle belongs to X2, not a shell.
    process = subprocess.Popen('"' + exe + '" ' + argument, executable=exe,
        shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _launches[process.pid] = process
    return process


async def _wait_exit(process, seconds):
    deadline = time.monotonic() + seconds
    while process.poll() is None and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
    return process.poll()


def _probe_files(exchange_dir, token):
    folder = Path(exchange_dir) / "recovery" / token
    folder.mkdir(parents=True, exist_ok=False)
    result = folder / "result.json"
    pas = folder / "RecoveryProbe.pas"
    payload = json.dumps({"probe": "executor ok", "token": token})
    quote = lambda value: str(value).replace("'", "''")
    pas.write_text("procedure Run;\nvar Lines: TStringList;\nbegin\n"
        "  Lines := TStringList.Create;\n  try\n"
        "    Lines.Text := '" + quote(payload) + "';\n"
        "    Lines.SaveToFile('" + quote(result) + "');\n"
        "  finally\n    Lines.Free;\n  end;\nend;\n", encoding="utf-8")
    project = folder / "RecoveryProbe.PrjScr"
    project.write_text("[Design]\nVersion=1.0\n\n[Document1]\n"
                       "DocumentPath=RecoveryProbe.pas\n", encoding="utf-8")
    return project, result


def _blocked(state, reason, **extra):
    return dict(recovered=False, blocked=True, error=reason,
        manual_action="In the intended Altium session, activate the failed script editor "
                      "and use Run > Stop / Ctrl+F3. After dispatch clients exit, call "
                      "recover_script_executor with manual_stop_confirmed=true. "
                      "If they remain, retain this diagnostic; do not kill X2 or close "
                      "other sessions. Do not retry mutations.",
        state=state, **extra)


def _pending_probe_evidence(exchange_dir, old):
    """Read one token-bound result and establish that its launcher has exited."""
    import emergency_stop
    latch_path = Path(exchange_dir) / "script_recovery_state.json"
    raw = latch_path.read_bytes()
    if json.loads(raw) != old or old.get("phase") != "probe_pending":
        raise ValueError("Pending probe checkpoint changed")
    token = old.get("token", "")
    if not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{32}", token) is None:
        raise ValueError("Exact unique probe token is unavailable")
    expected_path = (Path(exchange_dir) / "recovery" / token / "result.json").resolve()
    if Path(old.get("probe_result_path", "")).resolve() != expected_path:
        raise ValueError("Probe result path does not match this checkpoint")
    result_raw = expected_path.read_bytes()
    outcome = json.loads(result_raw.decode("utf-8-sig"))
    if outcome != {"probe": "executor ok", "token": token}:
        raise ValueError("Existing probe result does not match this recovery token")
    expected = old.get("fresh_instance_identity") or old.get("verified_prior_stop_exit", {}).get("main_identity")
    if (not isinstance(expected, dict) or type(expected.get("pid")) is not int
            or expected["pid"] <= 0 or type(expected.get("creation_filetime")) is not int
            or expected["creation_filetime"] <= 0):
        raise ValueError("Recorded main-process identity is unavailable")
    launches = old.get("launches", [])
    probes = [item for item in launches if item.get("phase") == "probe"]
    if (len(probes) != 1 or probes[0] != launches[-1]
            or probes[0].get("exit_code") is not None
            or type(probes[0].get("pid")) is not int or probes[0]["pid"] <= 0
            or probes[0]["pid"] == expected["pid"]
            or any(item.get("phase") != "stop" or item.get("exit_code") != 0
                   for item in launches[:-1])):
        raise ValueError("One exact pending probe launcher is required")
    probe_pid = probes[0]["pid"]
    for process in _launches.values():
        if process.poll() is None:
            raise ValueError("A tracked recovery launcher is still running")
    owned = _launches.get(probe_pid)
    if owned is not None:
        if owned.poll() != 0:
            raise ValueError("The tracked probe launcher exited unsuccessfully")
        exit_evidence = {"source": "owned_popen", "exit_confirmed": True, "exit_code": 0}
    else:
        try:
            probe = emergency_stop.WinProcess(probe_pid)
        except OSError as error:
            if getattr(error, "winerror", None) != 87:
                raise
            exit_evidence = {"source": "pid_absent_at_open", "exit_confirmed": True,
                             "exit_code": None, "historical_creation_verified": False}
        else:
            with probe:
                observed = probe.exit_observation()
            if (observed.get("pid") != probe_pid
                    or type(observed.get("creation_filetime")) is not int
                    or observed["creation_filetime"] <= 0
                    or type(observed.get("exit_code")) is not int
                    or observed["exit_code"] != 0):
                raise ValueError("Probe launcher is live or successful exit is unconfirmed")
            exit_evidence = {"source": "query_handle_exited", "exit_confirmed": True,
                             "exit_code": 0, "historical_creation_verified": False,
                             "observed_creation_filetime_decimal": str(observed["creation_filetime"])}
    return latch_path, raw, expected_path, result_raw, outcome, expected, probe_pid, exit_evidence


def _reconcile_late_probe(exe, exchange_dir, old):
    """Reconcile one existing unique result; never launch Stop/probe or claim idle."""
    import emergency_stop
    try:
        (latch_path, raw, expected_path, result_raw, outcome, expected,
         probe_pid, exit_evidence) = _pending_probe_evidence(exchange_dir, old)
        with emergency_stop.WinProcess(expected["pid"]) as main:
            current = main.identity()
            emergency_stop._valid_identity(current, exe, expected["pid"])
            if not emergency_stop._same_identity(current, expected):
                raise ValueError("Main identity changed since the existing probe")
            environment = inspect_environment(exe)
            windows = environment.get("process_windows", {})
            rows = windows.get(expected["pid"], windows.get(str(expected["pid"]), []))
            if (environment.get("ready") is not True
                    or environment.get("target_pid") != expected["pid"]
                    or environment.get("configured_process_ids") != [expected["pid"]]
                    or environment.get("foreground_confirmed") is not True
                    or not any(w.get("class") == "TDocumentForm" for w in rows)
                    or any(w.get("class") in {"#32770", "TMessageForm", "TShowException", "TPCBImportWizard"} for w in rows)
                    or environment.get("dialog_handles") or environment.get("script_errors")):
                raise ValueError("The recorded main application is not ready")
            if not emergency_stop._same_identity(main.identity(), current):
                raise ValueError("Main identity changed during reconciliation")
            if latch_path.read_bytes() != raw or expected_path.read_bytes() != result_raw:
                raise ValueError("Probe result or checkpoint changed during reconciliation")
            state = json.loads(raw)
            state["phase"] = "recovered"
            state["late_probe_completion"] = {"reconciled": True, "launcher": exit_evidence,
                "main_identity_unchanged_since_probe": True, "new_dispatches": 0,
                "main_restarted_by_reconciliation": False}
            state["launches"][-1].update(exit_confirmed=True,
                exit_code=exit_evidence["exit_code"], completion="late_reconciled")
            _write_state(exchange_dir, state)
        _launches.pop(probe_pid, None)
        return {"recovered": True, "blocked": False, "probe": outcome, "state": state,
                "late_completion": True, "no_new_dispatch": True,
                "native_transaction_idle_verified": False}
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        return _blocked(old, "Late probe not reconciled: " + str(error), no_new_dispatch=True)


def _fresh_start_after_pending_probe(exe, exchange_dir, old):
    """Authorize one new probe after a recorded normal start; never reuse old success."""
    import emergency_stop
    (latch_path, raw, result_path, result_raw, outcome, previous,
     probe_pid, exit_evidence) = _pending_probe_evidence(exchange_dir, old)
    if (previous.get("exe") != emergency_stop._canonical(exe)
            or previous.get("user_sid") != emergency_stop._current_sid()):
        raise ValueError("Previous main identity does not match the configured executable and user")
    if not emergency_stop._exact_target_has_exited(previous):
        raise ValueError("The previous exact main process has not exited")
    startup_path = Path(exchange_dir) / "altium_startup.json"
    startup_raw = startup_path.read_bytes()
    startup = json.loads(startup_raw)
    identity = startup.get("identity")
    if (not isinstance(identity, dict)
            or startup.get("success") is not True or startup.get("launched") is not True
            or startup.get("script_dispatched") is not False
            or startup.get("mutation_replayed") is not False
            or startup.get("status") != "process_started_ui_unverified"
            or startup.get("exit_code") is not None
            or emergency_stop._canonical(startup.get("configured_exe", "")) != emergency_stop._canonical(exe)
            or type(identity.get("pid")) is not int or identity["pid"] <= 0
            or startup.get("target_pid") != identity["pid"]
            or type(identity.get("creation_filetime")) is not int
            or identity["creation_filetime"] <= previous["creation_filetime"]
            or startup.get("creation_filetime_decimal") != str(identity["creation_filetime"])):
        raise ValueError("A newer exact successful normal-start receipt is required")
    with emergency_stop.WinProcess(identity["pid"]) as main:
        current = main.identity()
        emergency_stop._valid_identity(current, exe, identity["pid"])
        if not emergency_stop._same_identity(current, identity):
            raise ValueError("Current main does not match the normal-start receipt")
        environment = inspect_environment(exe)
        windows = environment.get("process_windows", {})
        rows = windows.get(identity["pid"], windows.get(str(identity["pid"]), []))
        if (environment.get("ready") is not True
                or environment.get("target_pid") != identity["pid"]
                or environment.get("configured_process_ids") != [identity["pid"]]
                or environment.get("foreground_confirmed") is not True
                or not any(w.get("class") == "TDocumentForm" for w in rows)
                or any(w.get("class") in {"#32770", "TMessageForm", "TShowException", "TPCBImportWizard"} for w in rows)
                or environment.get("dialog_handles") or environment.get("script_errors")):
            raise ValueError("The newly started main application is not ready")
        if not emergency_stop._same_identity(main.identity(), current):
            raise ValueError("New main identity changed during verification")
        if (latch_path.read_bytes() != raw or result_path.read_bytes() != result_raw
                or startup_path.read_bytes() != startup_raw):
            raise ValueError("Recovery result, checkpoint or startup receipt changed")
    return {"identity": current, "environment": environment,
            "evidence": {"previous_main_identity": previous,
                "previous_probe_token": old["token"], "previous_probe_launcher": exit_evidence,
                "previous_probe_certifies_current_main": False,
                "normal_start_identity_verified": True,
                "old_main_exit_verified": True, "new_probe_required": True}}


async def _recover_emergency_serialized(exe, exchange_dir, timeout_seconds=30,
                  dispatch_timeout_seconds=8, manual_stop_confirmed=False, *, verified_stop=None):
    """Caller holds the shared bridge lock throughout this operation."""
    if not 1 <= timeout_seconds <= 60 or not 1 <= dispatch_timeout_seconds <= 15:
        return _blocked({}, "Probe timeout must be 1..60s; dispatch timeout 1..15s")
    old = blocked_state(exchange_dir)
    restarted = None
    if (old and old.get("phase") == "probe_pending" and not manual_stop_confirmed
            and verified_stop is None):
        reconciled = _reconcile_late_probe(exe, exchange_dir, old)
        if reconciled.get("recovered"):
            return reconciled
        try:
            restarted = _fresh_start_after_pending_probe(exe, exchange_dir, old)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            return _blocked(old, "Pending probe/new-start verification failed: " + str(error),
                            no_new_dispatch=True, late_probe_diagnostic=reconciled.get("error"))
    fresh_emergency = bool(old and old.get("require_fresh_instance"))
    if fresh_emergency:
        import emergency_stop
        reason = emergency_stop.recovery_block(exe, old)
        if reason:
            return _blocked(old, reason)
    if verified_stop is not None:
        from pending_stop_wizard_resume import _VerifiedPendingStop
        if (not isinstance(verified_stop, _VerifiedPendingStop)
                or manual_stop_confirmed or fresh_emergency):
            return _blocked(old or {}, "Invalid internal verified-Stop capability")
    pending = []
    for pid, process in list(_launches.items()):
        if process.poll() is None:
            pending.append(pid)
        else:
            _launches.pop(pid, None)
    if pending:
        return _blocked(old or {}, "Previous recovery launcher is still running",
                        pending_launch_ids=pending)
    if old and not manual_stop_confirmed and not fresh_emergency and verified_stop is None and restarted is None:
        return _blocked(old, "Previous recovery is unresolved; no new launch dispatched")
    recorded_pending = [item["pid"] for item in (old or {}).get("launches", [])
                        if item.get("pid") and item.get("exit_code") is None]
    environment = restarted["environment"] if restarted is not None else inspect_environment(exe, recorded_pending)
    if not environment.get("ready"):
        return _blocked(old or {}, environment.get("reason", "Preflight failed"),
                        environment=environment)
    stop_evidence = None
    if verified_stop is not None:
        try:
            stop_evidence = verified_stop.consume(exe, exchange_dir, old, environment)
        except (OSError, ValueError, TypeError) as error:
            return _blocked(old or {}, "Pending Stop proof rejected: " + str(error),
                            environment=environment)
    fresh_identity = restarted["identity"] if restarted is not None else None
    if fresh_emergency:
        # The old exact process was proven gone above. Independently identify the
        # single ready current instance; absence of the old PID alone is not proof.
        try:
            previous = old.get("emergency_identity") or {}
            pid = environment.get("target_pid")
            if (type(pid) is not int or pid <= 0
                    or environment.get("configured_process_ids") != [pid]
                    or not any(window.get("class") == "TDocumentForm" for window in
                               environment.get("process_windows", {}).get(pid, []))
                    or type(previous.get("pid")) is not int or previous["pid"] <= 0
                    or type(previous.get("creation_filetime")) is not int
                    or previous["creation_filetime"] <= 0):
                raise ValueError("A complete previous identity and one ready current PID with a native main window are required")
            with emergency_stop.WinProcess(pid) as process:
                fresh_identity = process.identity()
                emergency_stop._valid_identity(fresh_identity, exe, pid)
                if (type(fresh_identity.get("creation_filetime")) is not int
                        or fresh_identity["creation_filetime"] <= 0
                        or (fresh_identity["pid"] == previous["pid"]
                            and fresh_identity["creation_filetime"] == previous["creation_filetime"])):
                    raise ValueError("Current process is not a verified fresh emergency replacement")
        except (OSError, ValueError, TypeError) as error:
            return _blocked(old, "Fresh-instance identity was not verified: " + str(error),
                            environment=environment)
    token = uuid.uuid4().hex
    state = {"token": token, "phase": "preflight", "environment": environment,
             "manual_stop_confirmed": manual_stop_confirmed,
             "fresh_instance_confirmed": fresh_identity is not None, "launches": []}
    if stop_evidence is not None:
        state["verified_prior_stop_exit"] = stop_evidence
    if fresh_identity is not None:
        state.update(fresh_instance_identity=fresh_identity,
                     fresh_instance_creation_filetime_decimal=str(fresh_identity["creation_filetime"]))
        if restarted is not None:
            state["verified_normal_restart"] = restarted["evidence"]
        else:
            state["previous_emergency_identity"] = old["emergency_identity"]
    _write_state(exchange_dir, state)

    async def launch_once(phase, argument, deadline):
        state["phase"] = phase + "_launching"
        _write_state(exchange_dir, state)  # Crash before/after Popen stays blocked.
        process = _launch(exe, argument)
        item = {"phase": phase, "pid": process.pid, "exit_code": None}
        state["launches"].append(item)
        _write_state(exchange_dir, state)
        item["exit_code"] = await _wait_exit(process, deadline)
        if item["exit_code"] is not None:
            _launches.pop(process.pid, None)
        state["phase"] = phase + ("_exited" if item["exit_code"] is not None else "_pending")
        _write_state(exchange_dir, state)
        return item["exit_code"]

    try:
        if not manual_stop_confirmed and fresh_identity is None and stop_evidence is None:
            code = await launch_once("stop", "-REditScript:Stop", dispatch_timeout_seconds)
            if code != 0:
                return _blocked(state, "Stop dispatch did not exit successfully; delivery unproven")
            # Exit 0 permits only a read-only probe; it does not prove Stop was
            # delivered or that any native PCB transaction has been balanced.
            environment = inspect_environment(exe)
            if not environment.get("ready"):
                state["phase"] = "blocked_after_stop"
                _write_state(exchange_dir, state)
                return _blocked(state, "Altium is not ready for a probe", environment=environment)
        project, result = _probe_files(exchange_dir, token)
        state["probe_result_path"] = str(result)
        code = await launch_once("probe", '-RScriptingSystem:RunScript(ProjectName="'
            + str(project) + '"|ProcName="RecoveryProbe>Run")', dispatch_timeout_seconds)
        if code != 0:
            return _blocked(state, "Probe dispatch did not exit successfully; no retry dispatched")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if result.exists():
                try:
                    outcome = json.loads(result.read_text(encoding="utf-8-sig"))
                except (OSError, ValueError):
                    outcome = None  # File may still be being written.
                if outcome == {"probe": "executor ok", "token": token}:
                    state["phase"] = "recovered"
                    _write_state(exchange_dir, state)
                    return {"recovered": True, "blocked": False, "probe": outcome,
                            "state": state, "native_transaction_idle_verified": False}
                if outcome is not None:
                    state["phase"] = "probe_token_mismatch"
                    _write_state(exchange_dir, state)
                    return _blocked(state, "Probe output does not match this recovery token")
            await asyncio.sleep(0.1)
        state["phase"] = "probe_timeout"
        _write_state(exchange_dir, state)
        return _blocked(state, "Probe did not complete; no new launch dispatched")
    except asyncio.CancelledError:
        state["phase"] = "cancelled_unresolved"
        _write_state(exchange_dir, state)
        raise
    except Exception as exc:
        state["phase"] = "failed_unresolved"
        _write_state(exchange_dir, state)
        return _blocked(state, str(exc))


async def recover(exe, exchange_dir, timeout_seconds=30,
                  dispatch_timeout_seconds=8, manual_stop_confirmed=False):
    """Serialize emergency state transitions across MCP processes as well."""
    import emergency_stop
    try:
        with emergency_stop.emergency_claim(exchange_dir):
            return await _recover_emergency_serialized(exe, exchange_dir, timeout_seconds,
                dispatch_timeout_seconds, manual_stop_confirmed)
    except OSError as error:
        return _blocked(blocked_state(exchange_dir) or {}, str(error))
