"""Pin one explicitly requested Altium window for read-only image capture."""
import altium_focus
import emergency_stop
import win32process
import win32gui


class ThreadDpiScope:
    """Use physical coordinates on this worker thread, restoring its old context."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("user32", use_last_error=True)
        self.set_context = api.SetThreadDpiAwarenessContext
        self.set_context.argtypes = [wintypes.HANDLE]
        self.set_context.restype = wintypes.HANDLE
        self.previous = self.set_context(-4)  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if not self.previous:
            raise OSError("Cannot establish per-monitor DPI context for exact capture")

    def close(self):
        if self.previous is not None:
            previous, self.previous = self.previous, None
            if not self.set_context(previous):
                raise OSError("Cannot restore capture thread DPI context")


class CaptureTarget:
    def __init__(self, exe, hwnd):
        if type(hwnd) is not int or hwnd <= 0:
            raise ValueError("window_handle must be a positive integer")
        self.exe, self.hwnd = exe, hwnd
        thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
        if not thread_id or not pid:
            raise ValueError("Cannot identify requested window")
        self.process = emergency_stop.WinProcess(pid)
        try:
            self.identity = self.process.identity()
            emergency_stop._valid_identity(self.identity, exe, pid)
            self.api = altium_focus.WindowAPI()
            rows = [row for row in self.api.windows(pid) if row["hwnd"] == hwnd]
            if len(rows) != 1 or rows[0]["thread_id"] != thread_id or rows[0]["minimized"]:
                raise ValueError("Requested window is not a visible, unminimized Altium window")
            self.row = rows[0]
            self.verify()
            self.window = {"handle": hwnd, "title": self.row["title"],
                "class_name": self.row["class"], "rect": self.row["rect"], "pid": pid}
        except BaseException:
            self.close()
            raise

    def verify(self):
        current = self.process.identity()
        emergency_stop._valid_identity(current, self.exe, self.identity["pid"])
        if (not emergency_stop._same_identity(current, self.identity)
                or not self.api.matches(self.row)
                or self.api.title(self.hwnd) != self.row["title"]):
            raise ValueError("Requested window or pinned process identity changed during capture")

    def children(self):
        """Read descendant window labels and physical screen bounds; no messages."""
        self.verify()
        rows = []
        def collect(hwnd, unused):
            thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid != self.identity["pid"] or not win32gui.IsChild(self.hwnd, hwnd):
                raise ValueError("Child window no longer belongs to the pinned Altium dialog")
            row = {"hwnd": hwnd, "class": win32gui.GetClassName(hwnd),
                "text": win32gui.GetWindowText(hwnd), "enabled": bool(win32gui.IsWindowEnabled(hwnd)),
                "visible": bool(win32gui.IsWindowVisible(hwnd)), "rect": list(win32gui.GetWindowRect(hwnd))}
            if ((thread_id, pid) != tuple(win32process.GetWindowThreadProcessId(hwnd))
                    or not win32gui.IsChild(self.hwnd, hwnd)):
                raise ValueError("Child window changed during inspection")
            rows.append(row)
            return True
        win32gui.EnumChildWindows(self.hwnd, collect, None)
        self.verify()
        return rows

    def close(self):
        self.process.close()
