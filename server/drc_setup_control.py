"""Narrow inspection of the observed native Altium Design Rule Checker dialog."""
import win32gui
import win32process
from screenshot_target import CaptureTarget

DIALOG_CLASS = "TDesignRuleCheckForm"
DIALOG_TITLES = {"Design Rule Checker [mil]", "Design Rule Checker [mm]", "Design Rule Checker"}
CHECKBOX_CLASSES = {"Button", "TCheckBox", "TXPCheckBox"}


def _main(target, expected_title):
    target.verify()
    if (target.row["class"] != DIALOG_CLASS or target.row["title"] not in DIALOG_TITLES
            or not target.row["enabled"]):
        raise ValueError("Expected the enabled native Design Rule Checker dialog")
    mains = [row for row in target.api.windows(target.identity["pid"]) if row["class"] == "TDocumentForm"]
    if (len(mains) != 1 or mains[0]["title"] != expected_title
            or not target.api.matches(mains[0]) or target.api.title(mains[0]["hwnd"]) != expected_title):
        raise ValueError("Exact expected main-window title does not match the pinned Altium session")
    return mains[0]


def _child(target, hwnd):
    thread, pid = win32process.GetWindowThreadProcessId(hwnd)
    if pid != target.identity["pid"] or not win32gui.IsChild(target.hwnd, hwnd):
        raise ValueError("Control no longer belongs to the pinned DRC dialog")
    row = {"hwnd": hwnd, "pid": pid, "thread_id": thread,
        "class": win32gui.GetClassName(hwnd), "text": win32gui.GetWindowText(hwnd),
        "visible": bool(win32gui.IsWindowVisible(hwnd)), "enabled": bool(win32gui.IsWindowEnabled(hwnd)),
        "parent_hwnd": win32gui.GetParent(hwnd), "rect": list(win32gui.GetWindowRect(hwnd)),
        "style": win32gui.GetWindowLong(hwnd, -16)}
    if ((thread, pid) != tuple(win32process.GetWindowThreadProcessId(hwnd))
            or not win32gui.IsChild(target.hwnd, hwnd)):
        raise ValueError("Control identity changed during inspection")
    return row


def _message(target, hwnd, msg, wp=0, lp=0):
    reply = target.api.c.c_size_t()
    if not target.api.api.SendMessageTimeoutW(hwnd, msg, wp, lp, 0x22, 500, target.api.c.byref(reply)):
        raise OSError("DRC control message failed or timed out; no retry")
    return int(reply.value)


def _controls(target):
    handles = []
    win32gui.EnumChildWindows(target.hwnd, lambda hwnd, unused: handles.append(hwnd) or True, None)
    rows, skipped = [], 0
    for hwnd in handles:
        if win32process.GetWindowThreadProcessId(hwnd)[1] != target.identity["pid"]:
            skipped += 1
            continue
        row = _child(target, hwnd)
        if row["class"] in CHECKBOX_CLASSES and row["style"] & 0xF in {2, 3, 5, 6}:
            try:
                row["check_state"] = _message(target, hwnd, 0xF0)
            except OSError as error:
                row["check_state_error"] = str(error)
        rows.append(row)
    target.verify()
    return rows, skipped


def control(exe, action, target_hwnd, expected_main_title):
    result = {"success": False, "action": action, "input_attempted": False,
        "script_dispatched": False, "document_save_requested": False, "retry_performed": False,
        "completion_verified": False,
        "document_binding": "exact main-window title (filename only); full path is not verified"}
    target = None
    try:
        if action != "inspect":
            raise ValueError("Only inspect is currently supported")
        if (not isinstance(expected_main_title, str) or not expected_main_title.strip()
                or ".pcbdoc" not in expected_main_title.casefold()):
            raise ValueError("Expected exact main-window title containing the scratch PcbDoc filename")
        target = CaptureTarget(exe, target_hwnd)
        main = _main(target, expected_main_title)
        controls, skipped = _controls(target)
        if _main(target, expected_main_title) != main:
            raise ValueError("Main-window state changed during inspection")
        return dict(result, success=True, no_input=True, identity=target.identity,
            dialog=target.window, main_window=main, controls=controls, foreign_descendants_skipped=skipped)
    except Exception as error:
        return dict(result, error=str(error), blocked=True)
    finally:
        if target is not None:
            target.close()
