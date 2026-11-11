"""Bind explicit visual classification to an unchanged public screenshot; no input."""
import hashlib
import io
import json
from pathlib import Path
import queue
import re
import threading

import emergency_stop
from screenshot_target import CaptureTarget, ThreadDpiScope


def _png(target):
    import ctypes
    from ctypes import wintypes
    from contextlib import ExitStack
    from PIL import Image
    import win32gui
    import win32ui

    target.verify()
    left, top, right, bottom = win32gui.GetWindowRect(target.hwnd)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0 or width * height > 8_000_000:
        raise ValueError("Unexpected diagnostic image bounds")
    with ExitStack() as cleanup:
        window_dc = win32gui.GetWindowDC(target.hwnd)
        if not window_dc:
            raise OSError("Cannot acquire diagnostic window DC")
        cleanup.callback(win32gui.ReleaseDC, target.hwnd, window_dc)
        source_dc = win32ui.CreateDCFromHandle(window_dc)
        cleanup.callback(source_dc.DeleteDC)
        destination_dc = source_dc.CreateCompatibleDC()
        cleanup.callback(destination_dc.DeleteDC)
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(source_dc, width, height)
        cleanup.callback(win32gui.DeleteObject, bitmap.GetHandle())
        previous = destination_dc.SelectObject(bitmap)
        if previous is not None:
            cleanup.callback(destination_dc.SelectObject, previous)
        native = ctypes.WinDLL("user32", use_last_error=True)
        native.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        native.PrintWindow.restype = wintypes.BOOL
        # Same capture/encoding as public exact-window get_screenshot.
        if not native.PrintWindow(target.hwnd, destination_dc.GetSafeHdc(), 2):
            raise OSError("PrintWindow failed for the pinned error dialog")
        info = bitmap.GetInfo()
        image = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
            bitmap.GetBitmapBits(True), "raw", "BGRX", 0, 1)
        output = io.BytesIO()
        image.save(output, format="PNG")
        result = output.getvalue()
    target.verify()
    return result


def fresh_image_sha256(exe, hwnd, expected_identity):
    """A timed-out capture has its own pin/resources and can never send input."""
    results = queue.Queue()
    def read_only_worker():
        from contextlib import ExitStack
        try:
            with ExitStack() as cleanup:
                scope = ThreadDpiScope()
                cleanup.callback(scope.close)
                target = CaptureTarget(exe, hwnd)
                cleanup.callback(target.close)
                if not emergency_stop._same_identity(target.identity, expected_identity):
                    raise ValueError("Capture process identity differs from inspected dialog")
                digest = hashlib.sha256(_png(target)).hexdigest()
            results.put((True, digest))
        except Exception as error:
            results.put((False, str(error)))
    worker = threading.Thread(target=read_only_worker, daemon=True)
    worker.start()
    worker.join(5)
    if worker.is_alive():
        raise OSError("Read-only dialog capture timed out; no input permitted")
    ok, value = results.get_nowait()
    if not ok:
        raise ValueError(value)
    return value


def verify(target, confirmation):
    required = {"receipt_path", "image_sha256", "classification"}
    if (not isinstance(confirmation, dict) or set(confirmation) != required
            or confirmation["classification"] not in {"undeclared_identifier", "numeric_conversion_error"}
            or not isinstance(confirmation["image_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", confirmation["image_sha256"])):
        raise ValueError("Visual confirmation requires receipt_path, PNG image_sha256 and supported script-error classification")
    receipt_path = Path(confirmation["receipt_path"])
    if not receipt_path.is_absolute() or receipt_path.stat().st_size > 2_000_000:
        raise ValueError("Expected an absolute bounded public screenshot receipt file")
    receipt_bytes = receipt_path.read_bytes()
    receipt = json.loads(receipt_bytes)
    args = receipt.get("arguments", {})
    if (receipt.get("tool") != "get_screenshot" or receipt.get("protocol") != "public MCP tools/call"
            or receipt.get("phase") != "RETURNED" or receipt.get("isError") is not False
            or args.get("view_type") != "current" or args.get("window_handle") != target.hwnd
            or args.get("zoom_to") is not None):
        raise ValueError("Evidence must be a successful public exact-window current screenshot receipt")
    content = receipt.get("content", [])
    metadata = [json.loads(item["text"]) for item in content if item.get("type") == "text"]
    images = [item for item in content if item.get("type") == "image"]
    if len(metadata) != 1 or len(images) != 1:
        raise ValueError("Expected one screenshot metadata block and one PNG")
    meta = metadata[0]
    if (meta.get("success") is not True or meta.get("exact_window_requested") is not True
            or meta.get("capture_method") != "PrintWindow" or meta.get("image_format") != "PNG"
            or meta.get("capture_dpi_awareness") != "per_monitor_v2"
            or meta.get("window_handle") != target.hwnd or meta.get("window_pid") != target.identity["pid"]
            or meta.get("window_class") != "TMessageForm" or meta.get("window_title") != "Error"
            or not emergency_stop._same_identity(meta.get("pinned_process_identity", {}), target.identity)):
        raise ValueError("Screenshot does not bind to the exact pinned error dialog and process")
    image_name = Path(images[0]["path"]).name
    image_path = receipt_path.parent / image_name
    if image_path.suffix.casefold() != ".png" or image_path.stat().st_size > 10_000_000:
        raise ValueError("Expected the adjacent bounded PNG saved with the public receipt")
    expected = confirmation["image_sha256"]
    if hashlib.sha256(image_path.read_bytes()).hexdigest() != expected:
        raise ValueError("Public screenshot PNG does not match the visually confirmed hash")
    target.verify()
    if fresh_image_sha256(target.exe, target.hwnd, target.identity) != expected:
        raise ValueError("Live dialog image changed since visual confirmation; no input")
    target.verify()
    return {"classification": confirmation["classification"], "classification_source": "caller visual confirmation; not OCR",
        "receipt_path": str(receipt_path), "receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "image_sha256": expected, "fresh_image_matches": True}
