"""Narrow inspection of the observed native Altium Design Rule Checker dialog."""
import win32gui
import win32process
from screenshot_target import CaptureTarget

DIALOG_CLASS = "TDesignRuleCheckForm"
DIALOG_TITLES = {"Design Rule Checker [mil]", "Design Rule Checker [mm]", "Design Rule Checker"}
CHECKBOX_CLASSES = {"Button", "TCheckBox", "TXPCheckBox"}
BUTTON_CLASSES = {"Button", "TButton", "TBitBtn", "TXPBitBtn"}


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


def _numeric_text(target, hwnd):
    buffer = target.api.c.create_unicode_buffer(64)
    count = _message(target, hwnd, 0xD, len(buffer), target.api.c.cast(buffer, target.api.c.c_void_p).value)
    if count < 1 or count >= len(buffer)-1 or count != len(buffer.value):
        raise ValueError("DRC numeric edit text is empty, truncated or inconsistent")
    if not buffer.value.isascii() or not buffer.value.isdecimal():
        raise ValueError("DRC limit field is not an unsigned decimal integer")
    return buffer.value


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
        if row["class"] == "TXPSpinEdit" and row["visible"]:
            try:
                row["value_text"] = _numeric_text(target, hwnd)
                row["value"] = int(row["value_text"])
            except (ValueError, OSError) as error:
                row["value_error"] = str(error)
        rows.append(row)
    target.verify()
    return rows, skipped


def _limit_control(controls, edit_hwnd, expected_limit):
    edits = [row for row in controls if row["class"] == "TXPSpinEdit" and row["visible"]]
    if (len(edits) != 1 or edits[0]["hwnd"] != edit_hwnd or not edits[0]["enabled"]
            or edits[0].get("value") != expected_limit or "value_error" in edits[0]):
        raise ValueError("One observed enabled TXPSpinEdit with the expected current limit is required")
    return edits[0]


def control(exe, action, target_hwnd, expected_main_title, edit_hwnd=None,
            expected_limit=None, new_limit=None, button_hwnd=None):
    result = {"success": False, "action": action, "input_attempted": False,
        "script_dispatched": False, "document_save_requested": False, "retry_performed": False,
        "completion_verified": False, "drc_run_requested": False, "rules_changed": False,
        "limit_readback_verified": False, "messages_attempted": 0, "messages_completed": 0,
        "document_binding": "exact main-window title (filename only); full path is not verified"}
    target = None
    try:
        if action not in {"inspect", "set_limit", "ok", "cancel"}:
            raise ValueError("action must be inspect, set_limit, ok or cancel")
        if action in {"set_limit", "ok"}:
            if (type(edit_hwnd) is not int or edit_hwnd <= 0 or type(expected_limit) is not int
                    or not 1 <= expected_limit <= 100000):
                raise ValueError("Observed edit_hwnd and expected_limit in 1..100000 are required")
        elif edit_hwnd is not None or expected_limit is not None:
            raise ValueError("Limit control arguments are only valid for set_limit or ok")
        if action == "set_limit":
            if type(new_limit) is not int or not expected_limit <= new_limit <= 100000:
                raise ValueError("new_limit must be an integer no smaller than expected_limit and at most 100000")
        elif new_limit is not None:
            raise ValueError("new_limit is only valid for set_limit")
        if action in {"ok", "cancel"}:
            if type(button_hwnd) is not int or button_hwnd <= 0:
                raise ValueError("The observed exact action button_hwnd is required")
        elif button_hwnd is not None:
            raise ValueError("button_hwnd is only valid for ok or cancel")
        if (not isinstance(expected_main_title, str) or not expected_main_title.strip()
                or ".pcbdoc" not in expected_main_title.casefold()):
            raise ValueError("Expected exact main-window title containing the scratch PcbDoc filename")
        target = CaptureTarget(exe, target_hwnd)
        main = _main(target, expected_main_title)
        controls, skipped = _controls(target)
        result.update(identity=target.identity, dialog=target.window, main_window=main,
            controls=controls, foreign_descendants_skipped=skipped)
        if _main(target, expected_main_title) != main:
            raise ValueError("Main-window state changed during inspection")
        if action == "inspect":
            return dict(result, success=True, no_input=True)
        if action in {"set_limit", "ok"} and any("check_state_error" in row for row in controls):
            raise ValueError("Cannot verify all report checkbox states before input")
        if action in {"set_limit", "ok"}:
            edit = _limit_control(controls, edit_hwnd, expected_limit)
            result.update(edit=edit, expected_limit=expected_limit)
        if action == "set_limit":
            result["new_limit"] = new_limit
            if new_limit == expected_limit:
                return dict(result, success=True, no_input=True, limit_readback_verified=True)
            if _controls(target)[0] != controls or _main(target, expected_main_title) != main:
                raise ValueError("DRC controls changed before setting limit")
            target.verify()
            buffer = target.api.c.create_unicode_buffer(str(new_limit))
            result.update(input_attempted=True, messages_attempted=1)
            accepted = _message(target, edit_hwnd, 0xC, 0, target.api.c.cast(buffer, target.api.c.c_void_p).value)
            result["messages_completed"] = 1
            if not accepted:
                raise ValueError("Native edit rejected WM_SETTEXT; no retry")
            after, _ = _controls(target)
            _limit_control(after, edit_hwnd, new_limit)
            before_others = [r for r in controls if r["hwnd"] != edit_hwnd]
            after_others = [r for r in after if r["hwnd"] != edit_hwnd]
            before_edit = {k:v for k,v in edit.items() if k not in {"value", "value_text", "text"}}
            after_edit = {k:v for k,v in _limit_control(after, edit_hwnd, new_limit).items() if k not in {"value", "value_text", "text"}}
            if (before_others != after_others or before_edit != after_edit
                    or _main(target, expected_main_title) != main):
                raise ValueError("DRC control state changed unexpectedly after setting limit")
            return dict(result, success=True, limit_readback_verified=True, limit_after=new_limit,
                outcome="Limit field read back; OK and reopen are required to verify persistence")
        buttons = [r for r in controls if r["class"] in BUTTON_CLASSES and r["visible"]
            and r["enabled"] and r["text"].replace("&", "").strip().casefold() == action]
        if len(buttons) != 1 or buttons[0]["hwnd"] != button_hwnd:
            raise ValueError("One visible enabled exact observed OK or Cancel button is required")
        button = buttons[0]
        rect = None
        messages = [(0xF5, 0, 0)]
        if button["class"] == "TXPBitBtn":
            rect = tuple(win32gui.GetClientRect(button_hwnd))
            left, top, right, bottom = rect
            x, y = (left+right)//2, (top+bottom)//2
            if right <= left or bottom <= top or not 0 <= x <= 32767 or not 0 <= y <= 32767:
                raise ValueError("DRC button has invalid client bounds")
            messages = [(0x201, 1, x | (y << 16)), (0x202, 0, x | (y << 16))]
        result.update(button=button, planned_messages=len(messages))
        for msg, wp, lp in messages:
            if (_controls(target)[0] != controls or _main(target, expected_main_title) != main
                    or (rect is not None and tuple(win32gui.GetClientRect(button_hwnd)) != rect)):
                raise ValueError("DRC dialog changed before action message")
            target.verify()
            result["input_attempted"] = True
            result["messages_attempted"] += 1
            win32gui.PostMessage(button_hwnd, msg, wp, lp)
            result["messages_completed"] += 1
        return dict(result, success=True, outcome="One action gesture posted; inspect closure and reopen for persistence separately")
    except Exception as error:
        return dict(result, error=str(error), blocked=not result["input_attempted"],
            outcome_unknown=result["input_attempted"],
            partial_dispatch=0 < result["messages_completed"] < result.get("planned_messages", result["messages_attempted"]))
    finally:
        if target is not None:
            target.close()
