"""Inspect and acknowledge one exact informational undeclared-identifier dialog."""
import re

import script_guard
import win32gui
from screenshot_target import CaptureTarget
from situs_setup_control import BUTTON_CLASSES, _button, _caption


def _send(target, hwnd, message, wparam=0, lparam=0):
    reply = target.api.c.c_size_t()
    if not target.api.api.SendMessageTimeoutW(hwnd, message, wparam, lparam, 0x22, 500,
                                             target.api.c.byref(reply)):
        raise OSError("Dialog control message failed or timed out; no retry")
    return int(reply.value)


def _text(target, hwnd):
    # GetWindowText cannot read another process's edit contents. WM_GETTEXT is
    # read-only and marshalled by Windows; keep the buffer and wait bounded.
    buffer = target.api.c.create_unicode_buffer(4097)
    count = _send(target, hwnd, 0x000D, len(buffer),
                  target.api.c.cast(buffer, target.api.c.c_void_p).value)
    if count < 0 or count >= len(buffer) - 1 or count != len(buffer.value):
        raise ValueError("Dialog text is truncated or inconsistent")
    return buffer.value


def _main(target, target_pid, expected_title):
    target.verify()
    if (target.identity["pid"] != target_pid or target.row["class"] != "TMessageForm"
            or target.row["title"] != "Error" or not target.row["enabled"]):
        raise ValueError("Expected the enabled exact Error TMessageForm in the requested process")
    windows = target.api.windows(target_pid)
    mains = [row for row in windows if row["class"] == "TDocumentForm"]
    if (len(mains) != 1 or mains[0]["title"] != expected_title
            or not target.api.matches(mains[0]) or target.api.title(mains[0]["hwnd"]) != expected_title):
        raise ValueError("Exact main-window title does not match the pinned Altium session")
    allowed = {target.hwnd, mains[0]["hwnd"]}
    if any(row["hwnd"] not in allowed and not (row["class"] == "TApplication"
            and row["title"] == "") for row in windows):
        raise ValueError("Another visible form makes dialog acknowledgment ambiguous")
    return mains[0]


def _snapshot(target, target_pid, expected_title):
    main = _main(target, target_pid, expected_title)
    handles = []
    win32gui.EnumChildWindows(target.hwnd, lambda hwnd, unused: handles.append(hwnd) or True, None)
    children = []
    for hwnd in handles:
        before = _button(target, hwnd)  # Same-process descendant identity, not just a caption.
        text = _text(target, hwnd) or before["text"]
        if _button(target, hwnd) != before:
            raise ValueError("Dialog child changed during text inspection")
        children.append(dict(before, text=text))
    if _main(target, target_pid, expected_title) != main:
        raise ValueError("Main-window state changed during dialog inspection")
    visible = [row for row in children if row["visible"]]
    classification = script_guard.classify_script_dialog("Error", "TMessageForm",
        [(row["hwnd"], row["class"], row["text"]) for row in visible])
    buttons = [row for row in visible if "button" in row["class"].casefold()
               or "btn" in row["class"].casefold()]
    error_text = classification["text"] if classification else ""
    eligible = bool(classification and classification["kind"] == "compile"
        and re.search(r"\bundeclared\s+identifier\b", error_text, re.IGNORECASE)
        and len(buttons) == 1 and buttons[0]["class"] in BUTTON_CLASSES
        and _caption(buttons[0]["text"]) == "ok" and buttons[0]["enabled"])
    return {"main_window": main, "children": children, "error_text": error_text,
        "acknowledgment_allowed": eligible, "button": buttons[0] if len(buttons) == 1 else None,
        "classification": classification,
        "refusal_reason": None if eligible else "Readable Undeclared identifier text and one supported enabled OK button are required"}


def control(exe, action, target_pid, target_hwnd, expected_main_title,
            expected_error_text="", button_hwnd=None):
    result = {"success": False, "action": action, "input_attempted": False,
        "messages_attempted": 0, "messages_completed": 0, "planned_messages": 0,
        "dialog_closed_verified": False, "executor_recovered": False,
        "stop_dispatched": False, "script_dispatched": False, "process_terminated": False,
        "document_save_requested": False, "retry_performed": False}
    target = None
    try:
        if action not in {"inspect", "acknowledge"}:
            raise ValueError("action must be inspect or acknowledge")
        if type(target_pid) is not int or target_pid <= 0:
            raise ValueError("target_pid must be a positive integer")
        if not isinstance(expected_main_title, str) or not expected_main_title.strip():
            raise ValueError("Exact observed main-window title is required")
        if action == "acknowledge":
            if not expected_error_text or type(button_hwnd) is not int or button_hwnd <= 0:
                raise ValueError("acknowledge requires inspected expected_error_text and button_hwnd")
        elif expected_error_text or button_hwnd is not None:
            raise ValueError("Acknowledgment arguments are not valid for inspect")
        target = CaptureTarget(exe, target_hwnd)
        snapshot = _snapshot(target, target_pid, expected_main_title)
        result.update(identity=target.identity, dialog=target.window, **snapshot)
        if action == "inspect":
            return dict(result, success=True, no_input=True)
        if (not snapshot["acknowledgment_allowed"] or snapshot["error_text"] != expected_error_text
                or snapshot["button"]["hwnd"] != button_hwnd):
            raise ValueError("Dialog does not match the inspected informational error and OK button")
        button = snapshot["button"]
        pair = button["class"] == "TXPBitBtn"
        messages, rect = [(0x00F5, 0, 0)], None
        if pair:
            rect = tuple(win32gui.GetClientRect(button_hwnd))
            left, top, right, bottom = rect
            x, y = (left + right) // 2, (top + bottom) // 2
            if right <= left or bottom <= top or not 0 <= x <= 32767 or not 0 <= y <= 32767:
                raise ValueError("OK button has invalid control-client bounds")
            position = x | (y << 16)
            messages = [(0x0201, 1, position), (0x0202, 0, position)]
            result.update(control_client_rect=list(rect), control_client_point=[x, y])
        result.update(dispatch_method="control_client_down_up" if pair else "bm_click",
                      planned_messages=len(messages))
        for message, wparam, lparam in messages:
            if (_snapshot(target, target_pid, expected_main_title) != snapshot
                    or (pair and tuple(win32gui.GetClientRect(button_hwnd)) != rect)):
                raise ValueError("Dialog, text, OK button or main window changed before input")
            target.verify()
            result["input_attempted"] = True
            result["messages_attempted"] += 1
            _send(target, button_hwnd, message, wparam, lparam)
            result["messages_completed"] += 1
        return dict(result, success=True, outcome="One OK gesture delivered; inspect environment to verify closure and recovery separately")
    except Exception as error:
        return dict(result, error=str(error), blocked=not result["input_attempted"],
            outcome_unknown=result["input_attempted"],
            partial_dispatch=0 < result["messages_completed"] < result["planned_messages"])
    finally:
        if target is not None:
            target.close()
