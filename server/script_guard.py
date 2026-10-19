"""Conservative script-error capture; never accept general Altium dialogs."""
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
    """Return a known script failure; reject confirmations and vague errors."""
    if window_class not in {"TMessageForm", "#32770"}:
        return None
    if title.strip().casefold() not in _ERROR_TITLES:
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
    if buttons & _CONFIRM_BUTTONS:
        return None
    # At least one affirmative informational button is required. A VCL dialog
    # whose child text is unavailable must not be guessed from its title.
    if not buttons or not buttons <= {"ok", "close", "help", "copy"}:
        return None
    text = " | ".join(dict.fromkeys(parts))
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
        pid, path = _window_process(hwnd)
        if not path or os.path.normcase(os.path.abspath(path)) != expected:
            continue
        pids.add(pid)
        match = classify_script_dialog(
            dialogs.window_text(hwnd), dialogs.window_class(hwnd), dialogs.children(hwnd))
        if match:
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
    allowed = {(d["hwnd"], d["pid"], d["text"]) for d in expected if d["safe_to_stop"]}
    closed = []
    for match in fresh:
        key = match["hwnd"], match["pid"], match["text"]
        if match["safe_to_stop"] and key in allowed:
            dialogs._user32.PostMessageW(match["hwnd"], 0x0010, 0, 0)
            closed.append(match)
    return closed


def stop_script_executor(exe_path):
    """Dispatch the documented Stop process without a visible shell helper."""
    return subprocess.Popen(
        [exe_path, "-REditScript:Stop"], shell=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))