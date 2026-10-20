"""Conservative script-error/crash capture; never answer general dialogs."""
import os
import re
import subprocess


_COMPILER_ERRORS = (
    "undeclared identifier", "unknown identifier", "syntax error",
    "unexpected end of file", "unexpected symbol", "identifier expected",
    "statement expected", "type mismatch", "not enough actual parameters",
    "too many actual parameters",
)
_CONFIRM_BUTTONS = {"yes", "no", "cancel", "save", "save as", "abort", "retry", "ignore"}
_ERROR_TITLES = {"error", "script error", "compiler error", "script compile error"}


def classify_script_dialog(title, window_class, children):
    """Capture native crash reporters without permitting any automatic answer."""
    if window_class not in {"TMessageForm", "#32770", "TShowException"}:
        return None
    parts, buttons = [], set()
    for _, cls, text in children:
        text = text.strip()
        if not text:
            continue
        if "button" in cls.casefold() or "btn" in cls.casefold():
            buttons.add(text.replace("&", "").strip().casefold())
        else:
            parts.append(text)
    text = " | ".join(dict.fromkeys(parts))
    if window_class == "TShowException":
        # This is a native exception reporter, not a script's OK-only error.
        # The reporter may offer Send/Restart/Close. Never answer any button,
        # even when child text is unavailable or resembles a compile error.
        return {"title": title, "text": text or "Native exception dialog text unavailable",
                "kind": "native_crash", "text_available": bool(text),
                "buttons": sorted(buttons), "auto_dismiss": False}
    if title.strip().casefold() not in _ERROR_TITLES:
        return None
    if buttons & _CONFIRM_BUTTONS:
        return None
    # At least one affirmative informational button is required. A VCL dialog
    # whose child text is unavailable must not be guessed from its title.
    if not buttons or not buttons <= {"ok", "close", "help", "copy"}:
        return None
    lower = text.casefold()
    kind = None
    if any(token in lower for token in _COMPILER_ERRORS):
        kind = "compile"
    elif re.search(r"error in script|script runtime error|runtime error in script", lower):
        kind = "runtime"
    if kind is None:
        return None
    return {"title": title, "text": text, "kind": kind}


def _window_process(hwnd):
    """Read PID and executable path without requesting process write access."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)
    if not handle:
        return pid.value, ""
    try:
        size = wintypes.DWORD(32768)
        path = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
            return pid.value, path.value
        return pid.value, ""
    finally:
        kernel32.CloseHandle(handle)


def find_script_errors(exe_path):
    """Inspect windows of the configured executable; never alter a window."""
    if os.name != "nt":
        return []
    import dialogs
    expected = os.path.normcase(os.path.abspath(exe_path))
    found, pids = [], set()
    for hwnd in dialogs.top_windows():
        if not dialogs.is_visible(hwnd):
            continue
        pid, path = _window_process(hwnd)
        if not path or os.path.normcase(os.path.abspath(path)) != expected:
            continue
        pids.add(pid)
        match = classify_script_dialog(
            dialogs.window_text(hwnd), dialogs.window_class(hwnd), dialogs.children(hwnd))
        if match and dialogs.is_visible(hwnd):
            found.append(dict(match, hwnd=hwnd, pid=pid))
    for match in found:
        # The command-line Stop process cannot target an arbitrary PID.
        # Do not automatically stop when multiple visible Altium instances
        # share the configured executable.
        match["safe_to_stop"] = len(pids) == 1
    return found


def dismiss_script_errors(exe_path, expected):
    """Close only revalidated, previously captured script-error dialogs."""
    import dialogs
    fresh = find_script_errors(exe_path)
    allowed = {(d["hwnd"], d["pid"], d["text"]) for d in expected
               if d.get("safe_to_stop") and d.get("kind") in {"compile", "runtime"}}
    closed = []
    for match in fresh:
        key = match["hwnd"], match["pid"], match["text"]
        if (match["safe_to_stop"] and match.get("kind") in {"compile", "runtime"}
                and key in allowed):
            dialogs._user32.PostMessageW(match["hwnd"], 0x0010, 0, 0)
            closed.append(match)
    return closed


def blocking_crash_report(exe_path):
    """Read-only pre-dispatch check; retain native crash evidence, never dismiss."""
    failures = [d for d in find_script_errors(exe_path) if d["kind"] == "native_crash"]
    if not failures:
        return None
    return {"success": False, "blocked": True,
            "error": "Altium native crash: " + " | ".join(d["text"] for d in failures),
            "script_errors": failures, "mutation_state": "not_dispatched",
            "dialogs_closed": 0, "dialogs_dismissed": 0,
            "executor_stop_dispatched": False, "executor_recovered": False,
            "retry_performed": False, "requires_recovery_probe": True,
            "manual_action": "Inspect and close the native exception reporter manually; "
                             "do not send a report automatically. Then stop the failed "
                             "script and verify recovery before another command."}


def stop_script_executor(exe_path):
    """Dispatch the documented Stop process without a visible shell helper."""
    return subprocess.Popen(
        [exe_path, "-REditScript:Stop"], shell=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))