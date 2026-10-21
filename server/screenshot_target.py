"""Pin one explicitly requested Altium window for read-only image capture."""
import altium_focus
import emergency_stop
import win32process


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

    def close(self):
        self.process.close()
