"""Answer the modal dialogs Altium opens while a script command runs.

Some Altium commands stop the script until a button is pressed: the
Engineering Change Order of Design » Import Changes From, the message box
that reports no differences. A tool that runs such a command starts a
driver here before sending the command; the driver watches for the window
by caption, presses the buttons the tool asked for and keeps a log of what
it saw, so the tool can report it. Everything goes through the Win32 window
messages Altium itself uses for its buttons; nothing moves the mouse.
Windows only, as Altium is.
"""

import ctypes
import re
import threading
import time
from ctypes import wintypes

try:
    _user32 = ctypes.windll.user32
except AttributeError:  # not Windows
    _user32 = None

BM_CLICK = 0x00F5
ECO_TITLE = "Engineering Change Order"

_EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def available() -> bool:
    return _user32 is not None


def window_text(hwnd) -> str:
    length = _user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def window_class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def top_windows() -> list:
    found = []

    def cb(hwnd, _):
        if _user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    _user32.EnumWindows(_EnumWindowsProc(cb), 0)
    return found


def children(hwnd) -> list:
    """(hwnd, class, text) of every descendant window."""
    found = []

    def cb(child, _):
        found.append((child, window_class(child), window_text(child)))
        return True

    _user32.EnumChildWindows(hwnd, _EnumWindowsProc(cb), 0)
    return found


def find_window(title: str):
    """The visible top-level window whose caption equals title (first match)."""
    for hwnd in top_windows():
        if window_text(hwnd) == title:
            return hwnd
    return None


def _plain(caption: str) -> str:
    return re.sub(r"\.\.\.$", "", caption.replace("&", "")).strip().lower()


def find_button(hwnd, caption: str):
    want = _plain(caption)
    for child, cls, text in children(hwnd):
        if "button" in cls.lower() and _plain(text) == want:
            return child
    return None


def is_enabled(hwnd) -> bool:
    return bool(_user32.IsWindowEnabled(hwnd))


def click(hwnd) -> None:
    _user32.PostMessageW(hwnd, BM_CLICK, 0, 0)


class DialogDriver(threading.Thread):
    """Watch for a window by caption and press its buttons in order.

    steps: a list of (button caption, wait) pairs. Each button is pressed once
    it exists and is enabled; `wait` is a caption of a button that must be
    enabled before the next step runs (None to continue at once). The log
    records every event with a timestamp; `seen` is set once the window
    appeared. `stop()` ends the watch.
    """

    def __init__(self, title: str, steps: list, timeout: float = 300.0, poll: float = 0.5):
        super().__init__(daemon=True)
        self.title = title
        self.steps = steps
        self.timeout = timeout
        self.poll = poll
        self.log = []
        self.seen = False
        self.done = False
        self._stop = threading.Event()

    def _note(self, message: str) -> None:
        self.log.append(f"{time.strftime('%H:%M:%S')} {message}")

    def stop(self) -> None:
        self._stop.set()

    def _wait_button(self, hwnd, caption: str, enabled: bool = True):
        while not self._stop.is_set() and time.time() < self._deadline:
            if not _user32.IsWindow(hwnd):
                return None
            button = find_button(hwnd, caption)
            if button is not None and (not enabled or is_enabled(button)):
                return button
            time.sleep(self.poll)
        return None

    def run(self) -> None:
        if _user32 is None:
            self._note("not on Windows; no dialog is driven")
            return
        self._deadline = time.time() + self.timeout
        hwnd = None
        while not self._stop.is_set() and time.time() < self._deadline:
            hwnd = find_window(self.title)
            if hwnd:
                break
            time.sleep(self.poll)
        if not hwnd:
            self._note(f'"{self.title}" did not appear')
            return
        self.seen = True
        self._note(f'"{self.title}" appeared')
        for caption, wait_for in self.steps:
            button = self._wait_button(hwnd, caption)
            if button is None:
                self._note(f'button "{caption}" not found or never enabled')
                return
            click(button)
            self._note(f'pressed "{caption}"')
            if wait_for:
                if self._wait_button(hwnd, wait_for) is None:
                    self._note(f'"{wait_for}" never became enabled after "{caption}"')
                    return
                self._note(f'"{wait_for}" enabled')
        self.done = True
        # The window may outlive the last click for a moment; wait for it to go.
        while not self._stop.is_set() and time.time() < self._deadline and _user32.IsWindow(hwnd):
            time.sleep(self.poll)
        self._note(f'"{self.title}" closed')


def eco_driver(timeout: float = 300.0) -> DialogDriver:
    """Execute every change of the ECO dialog, then close it."""
    return DialogDriver(ECO_TITLE, [("Execute Changes", "Close"), ("Close", None)], timeout=timeout)
