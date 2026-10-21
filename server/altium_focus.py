"""Explicit PID-scoped window activation; no scripts or document operations."""
import time
from pathlib import Path

import altium_lifecycle
import emergency_stop

MAIN_CLASS = "TDocumentForm"


class WindowAPI:
    """Bounded window operations, loaded only by an explicit focus request."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes as w
        self.c, self.w = ctypes, w
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        self.callback = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        signatures = {
            "EnumWindows": ([self.callback, w.LPARAM], w.BOOL),
            "GetWindowThreadProcessId": ([w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
            "GetClassNameW": ([w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetWindowTextLengthW": ([w.HWND], ctypes.c_int),
            "GetWindowTextW": ([w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetWindow": ([w.HWND, w.UINT], w.HWND),
            "IsWindowVisible": ([w.HWND], w.BOOL),
            "IsWindowEnabled": ([w.HWND], w.BOOL),
            "IsIconic": ([w.HWND], w.BOOL),
            "IsHungAppWindow": ([w.HWND], w.BOOL),
            "GetWindowLongW": ([w.HWND, ctypes.c_int], w.LONG),
            "GetWindowRect": ([w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
            "GetForegroundWindow": ([], w.HWND),
            "ShowWindowAsync": ([w.HWND, ctypes.c_int], w.BOOL),
            "SetForegroundWindow": ([w.HWND], w.BOOL),
            "SendMessageTimeoutW": ([w.HWND, w.UINT, w.WPARAM, w.LPARAM,
                w.UINT, w.UINT, ctypes.POINTER(ctypes.c_size_t)], w.LPARAM),
        }
        for name, (args, result) in signatures.items():
            method = getattr(self.api, name)
            method.argtypes, method.restype = args, result

    def windows(self, pid):
        rows = []
        errors = []

        def collect(hwnd, unused):
            try:
                actual = self.w.DWORD()
                thread_id = self.api.GetWindowThreadProcessId(hwnd, self.c.byref(actual))
                if not thread_id:
                    raise OSError("Cannot identify an enumerated window")
                if actual.value != pid or not self.api.IsWindowVisible(hwnd):
                    return True
                cls = self.c.create_unicode_buffer(256)
                rect = self.w.RECT()
                if not self.api.GetClassNameW(hwnd, cls, len(cls)) or not self.api.GetWindowRect(hwnd, self.c.byref(rect)):
                    raise OSError("Cannot read target window identity or bounds")
                row = {"hwnd": int(hwnd), "pid": int(actual.value), "thread_id": int(thread_id), "class": cls.value,
                    "title": self.title(hwnd),
                    "owner_hwnd": self.owner(hwnd),
                    "enabled": bool(self.api.IsWindowEnabled(hwnd)),
                    "minimized": bool(self.api.IsIconic(hwnd)),
                    "tool_window": bool(self.api.GetWindowLongW(hwnd, -20) & 0x80),
                    "rect": [rect.left, rect.top, rect.right, rect.bottom]}
                if not self.matches(row):
                    raise OSError("Target window identity changed during enumeration")
                rows.append(row)
                return True
            except Exception as error:
                errors.append(str(error))
                return False

        ok = self.api.EnumWindows(self.callback(collect), 0)
        if errors or not ok:
            raise OSError(errors[0] if errors else "Cannot enumerate target windows")
        return rows

    def title(self, hwnd):
        self.c.set_last_error(0)
        length = self.api.GetWindowTextLengthW(hwnd)
        if (not length and self.c.get_last_error()) or length < 0 or length > 32767:
            raise OSError("Cannot read target window title length")
        buffer = self.c.create_unicode_buffer(length + 1)
        self.c.set_last_error(0)
        copied = self.api.GetWindowTextW(hwnd, buffer, len(buffer))
        if not copied and self.c.get_last_error():
            raise OSError("Cannot read target window title")
        self.c.set_last_error(0)
        final_length = self.api.GetWindowTextLengthW(hwnd)
        if (not final_length and self.c.get_last_error()) or copied != length or final_length != copied:
            raise OSError("Target window title changed or was truncated during read")
        return buffer.value

    def owner(self, hwnd):
        self.c.set_last_error(0)
        owner = self.api.GetWindow(hwnd, 4)  # GW_OWNER
        if not owner and self.c.get_last_error():
            raise OSError("Cannot read target window owner")
        return int(owner or 0)

    def matches(self, row):
        pid = self.w.DWORD()
        thread_id = self.api.GetWindowThreadProcessId(row["hwnd"], self.c.byref(pid))
        cls = self.c.create_unicode_buffer(256)
        return (int(thread_id) == row["thread_id"] and pid.value == row["pid"]
            and bool(self.api.GetClassNameW(row["hwnd"], cls, len(cls)))
            and cls.value == row["class"] and bool(self.api.IsWindowVisible(row["hwnd"]))
            and bool(self.api.IsWindowEnabled(row["hwnd"])) == row["enabled"]
            and self.owner(row["hwnd"]) == row["owner_hwnd"]
            and (row["class"] != "TApplication" or self.title(row["hwnd"]) == row["title"]))

    def responsive(self, hwnd):
        reply = self.c.c_size_t()
        return not self.api.IsHungAppWindow(hwnd) and bool(self.api.SendMessageTimeoutW(
            hwnd, 0, 0, 0, 0x22, 500, self.c.byref(reply)))

    def restore(self, hwnd):
        return bool(self.api.ShowWindowAsync(hwnd, 9))  # SW_RESTORE, no synchronous message.

    def activate(self, hwnd):
        return bool(self.api.SetForegroundWindow(hwnd))

    def foreground(self):
        return int(self.api.GetForegroundWindow() or 0)


def _select_main(api, pid):
    rows = api.windows(pid)
    mains = [row for row in rows if row["class"] == MAIN_CLASS]
    if len(mains) != 1:
        raise ValueError("Expected exactly one visible TDocumentForm main window")
    main = mains[0]
    # Delphi can expose its empty framework shell alongside the document
    # frame. AD26 also uses two unowned windows; an owner relationship is not
    # required in that observed case. Other classes/forms remain blocking.
    owners = [row for row in rows if row["class"] == "TApplication"
        and row.get("title") == "" and row["pid"] == pid
        and row["thread_id"] == main["thread_id"] and row["enabled"]
        and (row["hwnd"] == main.get("owner_hwnd") or main.get("owner_hwnd") == 0)
        and row.get("owner_hwnd") == 0]
    allowed_owner = owners[0] if len(owners) == 1 else None
    blockers = [row for row in rows if row["hwnd"] != main["hwnd"]
                and (allowed_owner is None or row["hwnd"] != allowed_owner["hwnd"])]
    if blockers or not main["enabled"]:
        raise ValueError("A modal, disabled main window or unrecognized visible form blocks focus; "
            + "main owner=" + str(main.get("owner_hwnd")) + "; additional windows="
            + str([(row["hwnd"], row["class"], row.get("owner_hwnd"),
                    row["pid"], row["thread_id"]) for row in blockers]))
    if not api.responsive(main["hwnd"]):
        raise ValueError("Altium main window is not responding; no activation")
    if not api.matches(main) or (allowed_owner is not None and not api.matches(allowed_owner)):
        raise ValueError("Target window or framework shell changed after responsiveness check")
    if allowed_owner is not None:
        main["framework_window"] = dict(allowed_owner)
    return main


def _focus_locked(exe, target_pid):
    result = {"success": False, "target_pid": target_pid, "restore_requested": False,
              "restored": False, "activation_requested": False, "foreground_confirmed": False,
              "script_dispatched": False, "document_saved": False, "native_idle_verified": False}
    try:
        inventory = altium_lifecycle.configured_instances(exe)
        if inventory["uncertain"] or [row["pid"] for row in inventory["configured"]] != [target_pid]:
            raise ValueError("Expected exactly the requested configured process, with no uncertain instances")
        with emergency_stop.WinProcess(target_pid) as process:
            identity = process.identity()
            emergency_stop._valid_identity(identity, exe, target_pid)
            result.update(identity=identity, creation_filetime_decimal=str(identity["creation_filetime"]))
            api = WindowAPI()
            main = _select_main(api, target_pid)
            hwnd = main["hwnd"]
            thread_id = main["thread_id"]
            result.update(hwnd=hwnd, window_before=main)

            def recheck():
                inventory = altium_lifecycle.configured_instances(exe)
                if inventory["uncertain"] or [row["pid"] for row in inventory["configured"]] != [target_pid]:
                    raise ValueError("Configured process inventory changed; no further activation")
                current = process.identity()
                emergency_stop._valid_identity(current, exe, target_pid)
                if not emergency_stop._same_identity(identity, current):
                    raise ValueError("Target process identity changed; no further activation")
                row = _select_main(api, target_pid)
                if row["hwnd"] != hwnd or row["thread_id"] != thread_id:
                    raise ValueError("Target main window changed; no further activation")
                return row

            main = recheck()
            if main["minimized"]:
                result["restore_requested"] = True
                if not api.restore(hwnd):
                    raise OSError("Windows refused the restore request")
                deadline = time.monotonic() + 2.0
                while True:
                    main = recheck()
                    if not main["minimized"]:
                        result["restored"] = True
                        break
                    if time.monotonic() >= deadline:
                        raise OSError("Window did not restore within the bounded wait")
                    time.sleep(0.05)
            main = recheck()
            if api.foreground() != hwnd:
                result["activation_requested"] = True
                accepted = api.activate(hwnd)
                result["activation_api_returned"] = accepted
            deadline = time.monotonic() + 1.0
            while api.foreground() != hwnd and time.monotonic() < deadline:
                time.sleep(0.05)
            main = recheck()
            foreground = api.foreground() == hwnd
            result.update(window_after=main, foreground_confirmed=foreground,
                success=foreground and not main["minimized"])
            if not result["success"]:
                result.update(blocked=True, error="Windows did not grant foreground focus; no input was injected")
            return result
    except (OSError, ValueError) as error:
        return dict(result, blocked=True, error=str(error))


def focus(exe, exchange, target_pid):
    """Caller holds the bridge lock; preserve recovery state under the OS claim."""
    try:
        if type(target_pid) is not int or target_pid <= 0:
            raise ValueError("target_pid must be a positive integer")
        configured = Path(exe)
        if not configured.is_absolute() or not configured.is_file() or configured.suffix.casefold() != ".exe":
            raise ValueError("Configured Altium executable must be an absolute existing .exe")
        with emergency_stop.emergency_claim(exchange):
            return _focus_locked(str(configured.resolve()), target_pid)
    except (OSError, ValueError) as error:
        return {"success": False, "blocked": True, "error": str(error),
                "restore_requested": False, "activation_requested": False,
                "script_dispatched": False, "document_saved": False, "native_idle_verified": False}
