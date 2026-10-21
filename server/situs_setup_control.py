"""Exact Situs setup inspection and one whitelisted control gesture."""
import win32gui
import win32process
from screenshot_target import CaptureTarget

BUTTON_CLASSES = {"Button", "TButton", "TBitBtn", "TXPBitBtn"}
CAPTIONS = {"ok": "ok", "cancel": "cancel", "route_all": "route all"}
CHECKBOX_CLASSES = {"Button", "TCheckBox", "TXPCheckBox"}
RIP_UP_CAPTION = "rip-up violations after routing"


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


def _message(target, hwnd, message):
    reply = target.api.c.c_size_t()
    if not target.api.api.SendMessageTimeoutW(hwnd, message, 0, 0, 0x22, 500,
                                             target.api.c.byref(reply)):
        raise OSError("Checkbox message failed or timed out; no retry")
    return int(reply.value)


def _checkbox(target, hwnd):
    row = _button(target, hwnd)
    row["button_type"] = win32gui.GetWindowLong(hwnd, -16) & 0xF
    if (row["class"] not in CHECKBOX_CLASSES or row["button_type"] not in {2, 3, 5, 6}
            or _caption(row["text"]) != RIP_UP_CAPTION):
        raise ValueError("Unsupported exact Rip-up checkbox class, caption or button style")
    state = _message(target, hwnd, 0x00F0)  # BM_GETCHECK; never a state-changing message.
    if state not in {0, 1}:
        raise ValueError("Rip-up checkbox state is indeterminate or unsupported")
    if (_button(target, hwnd) != {k: v for k, v in row.items() if k != "button_type"}
            or win32gui.GetWindowLong(hwnd, -16) & 0xF != row["button_type"]):
        raise ValueError("Checkbox changed during state read")
    target.verify()
    return dict(row, checked=bool(state))


def _buttons(target):
    handles = []
    win32gui.EnumChildWindows(target.hwnd, lambda hwnd, unused: handles.append(hwnd) or True, None)
    rows, skipped, checkboxes = [], 0, []
    for hwnd in handles:
        if win32process.GetWindowThreadProcessId(hwnd)[1] != target.identity["pid"]:
            skipped += 1
            continue  # Embedded webview descendants can belong to a separate process.
        if _caption(win32gui.GetWindowText(hwnd)) == RIP_UP_CAPTION:
            row = _button(target, hwnd)
            try:
                row = _checkbox(target, hwnd)
            except (ValueError, OSError) as error:
                row["state_error"] = str(error)
            checkboxes.append(row)
        elif win32gui.GetClassName(hwnd) in BUTTON_CLASSES:
            rows.append(_button(target, hwnd))
    target.verify()
    return rows, skipped, checkboxes


def control(exe, action, target_hwnd, expected_main_title, checkbox_hwnd=None, expected_checked=None):
    result = {"success": False, "action": action, "button_message_attempted": False,
        "button_message_posted": False, "completion_verified": False, "script_dispatched": False,
        "message_pair_attempted": False, "message_pair_posted": False,
        "checkbox_message_attempted": False, "checkbox_message_completed": False,
        "checkbox_state_verified": False,
        "messages_attempted": 0, "messages_posted": 0, "planned_messages": 0,
        "document_save_requested": False, "retry_performed": False,
        "document_binding": "exact main-window title (filename only); full path is not verified"}
    target = None
    try:
        if action not in {"inspect", "enable_rip_up", *CAPTIONS}:
            raise ValueError("action must be inspect, enable_rip_up, ok, cancel or route_all")
        if action == "enable_rip_up":
            if type(expected_checked) is not bool or type(checkbox_hwnd) is not int or checkbox_hwnd <= 0:
                raise ValueError("enable_rip_up requires observed checkbox_hwnd and boolean expected_checked")
        elif checkbox_hwnd is not None or expected_checked is not None:
            raise ValueError("Checkbox arguments are only valid with enable_rip_up")
        if (not isinstance(expected_main_title, str) or not expected_main_title.strip()
                or ".pcbdoc" not in expected_main_title.casefold()):
            raise ValueError("Expected exact main-window title containing the scratch PcbDoc filename")
        target = CaptureTarget(exe, target_hwnd)
        main = _main(target, expected_main_title)
        buttons, skipped, checkboxes = _buttons(target)
        result.update(identity=target.identity, dialog=target.window, main_window=main,
            buttons=buttons, checkboxes=checkboxes, foreign_descendants_skipped=skipped)
        if _main(target, expected_main_title) != main:
            raise ValueError("Main-window state changed during inspection")
        if action == "inspect":
            return dict(result, success=True, no_input=True)
        if action == "enable_rip_up":
            if (len(checkboxes) != 1 or checkboxes[0]["hwnd"] != checkbox_hwnd
                    or not checkboxes[0]["visible"] or not checkboxes[0]["enabled"]
                    or "state_error" in checkboxes[0]):
                raise ValueError("One supported visible enabled Rip-up checkbox at the observed HWND is required")
            checkbox = checkboxes[0]
            if (_checkbox(target, checkbox_hwnd) != checkbox
                    or _main(target, expected_main_title) != main):
                raise ValueError("Checkbox or main window changed before enable request")
            result.update(checkbox=checkbox, desired_checked=True, expected_checked=expected_checked)
            if checkbox["checked"] != expected_checked:
                raise ValueError("Rip-up checkbox differs from expected current state; no input")
            if checkbox["checked"]:
                return dict(result, success=True, no_input=True, checkbox_state_verified=True,
                    already_enabled=True, checked_after=True)
            target.verify()
            result["checkbox_message_attempted"] = True
            _message(target, checkbox_hwnd, 0x00F5)  # One BM_CLICK so the VCL change handler runs.
            result["checkbox_message_completed"] = True
            after = _checkbox(target, checkbox_hwnd)
            if (after != dict(checkbox, checked=True) or _main(target, expected_main_title) != main):
                raise ValueError("Rip-up enabled state could not be verified after the single click")
            target.verify()
            return dict(result, success=True, checkbox_state_verified=True, checked_after=True,
                outcome="Rip-up checkbox reads checked; dialog acceptance and routing are separate actions")
        selected = [row for row in buttons if row["class"] in BUTTON_CLASSES
            and _caption(row["text"]) == CAPTIONS[action] and row["visible"] and row["enabled"]]
        if len(selected) != 1:
            raise ValueError("Exactly one visible enabled whitelisted action button is required")
        button = selected[0]
        pair = button["class"] == "TXPBitBtn"
        messages = [(0x00F5, 0, 0)]  # BM_CLICK for standard/VCL buttons that support it.
        rect = None
        if pair:
            rect = tuple(win32gui.GetClientRect(button["hwnd"]))
            left, top, right, bottom = rect
            x, y = (left + right) // 2, (top + bottom) // 2
            if (right <= left or bottom <= top or not 0 <= x <= 32767 or not 0 <= y <= 32767):
                raise ValueError("TXPBitBtn has invalid control-client bounds")
            position = x | (y << 16)
            messages = [(0x0201, 1, position), (0x0202, 0, position)]
            result.update(control_client_rect=list(rect), control_client_point=[x, y])
        result.update(button=button, dispatch_method="control_client_down_up" if pair else "bm_click",
            planned_messages=len(messages))
        for message, keys, position in messages:
            if (_button(target, button["hwnd"]) != button or _main(target, expected_main_title) != main
                    or (pair and tuple(win32gui.GetClientRect(button["hwnd"])) != rect)):
                raise ValueError("Button or main window changed before control message")
            target.verify()
            result.update(button_message_attempted=True, message_pair_attempted=pair)
            result["messages_attempted"] += 1
            win32gui.PostMessage(button["hwnd"], message, keys, position)
            result["messages_posted"] += 1
        return dict(result, success=True, button_message_posted=True, message_pair_posted=pair,
            partial_dispatch=False,
            outcome="control message(s) posted; inspect subsequent UI to establish completion")
    except Exception as error:
        attempted = result["button_message_attempted"] or result["checkbox_message_attempted"]
        return dict(result, error=str(error), blocked=not attempted,
            outcome_unknown=attempted,
            partial_dispatch=0 < result["messages_posted"] < result["planned_messages"])
    finally:
        if target is not None:
            target.close()
