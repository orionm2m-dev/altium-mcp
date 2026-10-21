"""Exact Situs setup inspection and one whitelisted button message."""
import win32gui
import win32process
from screenshot_target import CaptureTarget

BUTTON_CLASSES = {"Button", "TButton", "TBitBtn", "TXPBitBtn"}
CAPTIONS = {"ok": "ok", "cancel": "cancel", "route_all": "route all"}


def _caption(text):
    return text.replace("&", "").strip().casefold()


def _main(target, expected_title):
    target.verify()
    if (target.row["class"] != "TSitusSetup" or target.row["title"] != "Situs Routing Strategies"
            or not target.row["enabled"]):
        raise ValueError("Expected the enabled exact Situs Routing Strategies dialog")
    mains = [row for row in target.api.windows(target.identity["pid"]) if row["class"] == "TDocumentForm"]
    if (len(mains) != 1 or mains[0]["title"] != expected_title
            or not target.api.matches(mains[0]) or target.api.title(mains[0]["hwnd"]) != expected_title):
        raise ValueError("Exact expected main-window title does not match the pinned Altium session")
    return mains[0]


def _button(target, hwnd):
    thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
    if pid != target.identity["pid"] or not win32gui.IsChild(target.hwnd, hwnd):
        raise ValueError("Button no longer belongs to the pinned Situs dialog")
    row = {"hwnd": hwnd, "pid": pid, "thread_id": thread_id,
        "class": win32gui.GetClassName(hwnd), "text": win32gui.GetWindowText(hwnd),
        "visible": bool(win32gui.IsWindowVisible(hwnd)), "enabled": bool(win32gui.IsWindowEnabled(hwnd))}
    if ((thread_id, pid) != tuple(win32process.GetWindowThreadProcessId(hwnd))
            or not win32gui.IsChild(target.hwnd, hwnd)):
        raise ValueError("Button identity changed during inspection")
    return row


def _buttons(target):
    handles = []
    win32gui.EnumChildWindows(target.hwnd, lambda hwnd, unused: handles.append(hwnd) or True, None)
    rows, skipped = [], 0
    for hwnd in handles:
        if win32process.GetWindowThreadProcessId(hwnd)[1] != target.identity["pid"]:
            skipped += 1
            continue  # Embedded webview descendants can belong to a separate process.
        if win32gui.GetClassName(hwnd) in BUTTON_CLASSES:
            rows.append(_button(target, hwnd))
    target.verify()
    return rows, skipped


def control(exe, action, target_hwnd, expected_main_title):
    result = {"success": False, "action": action, "button_message_attempted": False,
        "button_message_posted": False, "completion_verified": False, "script_dispatched": False,
        "document_save_requested": False, "retry_performed": False,
        "document_binding": "exact main-window title (filename only); full path is not verified"}
    target = None
    try:
        if action not in {"inspect", *CAPTIONS}:
            raise ValueError("action must be inspect, ok, cancel or route_all")
        if (not isinstance(expected_main_title, str) or not expected_main_title.strip()
                or ".pcbdoc" not in expected_main_title.casefold()):
            raise ValueError("Expected exact main-window title containing the scratch PcbDoc filename")
        target = CaptureTarget(exe, target_hwnd)
        main = _main(target, expected_main_title)
        buttons, skipped = _buttons(target)
        result.update(identity=target.identity, dialog=target.window, main_window=main,
            buttons=buttons, foreign_descendants_skipped=skipped)
        if _main(target, expected_main_title) != main:
            raise ValueError("Main-window state changed during inspection")
        if action == "inspect":
            return dict(result, success=True, no_input=True)
        selected = [row for row in buttons if row["class"] in BUTTON_CLASSES
            and _caption(row["text"]) == CAPTIONS[action] and row["visible"] and row["enabled"]]
        if len(selected) != 1:
            raise ValueError("Exactly one visible enabled whitelisted action button is required")
        button = selected[0]
        if _button(target, button["hwnd"]) != button or _main(target, expected_main_title) != main:
            raise ValueError("Button or main window changed before dispatch")
        target.verify()
        result.update(button=button, button_message_attempted=True)
        win32gui.PostMessage(button["hwnd"], 0x00F5, 0, 0)  # One BM_CLICK; no fallback gesture.
        return dict(result, success=True, button_message_posted=True,
            outcome="button message posted; inspect subsequent UI to establish completion")
    except Exception as error:
        return dict(result, error=str(error), blocked=not result["button_message_attempted"],
            outcome_unknown=result["button_message_attempted"])
    finally:
        if target is not None:
            target.close()
