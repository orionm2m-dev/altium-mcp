"""Public screenshot evidence binding and read-only capture tests; no native calls."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import script_error_visual as visual


class VisualEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        self.receipt_path = folder / "00_get_screenshot.json"
        self.image_path = folder / "00_get_screenshot.1.png"
        self.image_path.write_bytes(b"verified PNG bytes")
        self.digest = hashlib.sha256(self.image_path.read_bytes()).hexdigest()
        self.identity = {"pid": 123, "creation_filetime": 456, "exe": "configured.exe", "user_sid": "SID", "running": True}
        self.target = Mock(hwnd=42, exe="configured.exe", identity=self.identity)
        self.meta = {"success": True, "exact_window_requested": True, "capture_method": "PrintWindow",
            "image_format": "PNG", "capture_dpi_awareness": "per_monitor_v2", "window_handle": 42,
            "window_pid": 123, "window_class": "TMessageForm", "window_title": "Error",
            "pinned_process_identity": self.identity}
        self.receipt = {"tool": "get_screenshot", "protocol": "public MCP tools/call", "phase": "RETURNED",
            "isError": False, "arguments": {"view_type": "current", "window_handle": 42}, "content": []}
        self.confirmation = {"receipt_path": str(self.receipt_path), "image_sha256": self.digest,
                             "classification": "undeclared_identifier"}
        p = patch.object(visual, "fresh_image_sha256", return_value=self.digest)
        self.fresh = p.start()
        self.addCleanup(p.stop)
        self.write()

    def write(self):
        self.receipt["content"] = [{"type": "text", "text": json.dumps(self.meta)},
                                   {"type": "image", "path": str(self.image_path)}]
        self.receipt_path.write_text(json.dumps(self.receipt), "utf-8")

    def test_exact_public_image_and_live_fingerprint_accept_explicit_visual_classification(self):
        result = visual.verify(self.target, self.confirmation)
        self.assertTrue(result["fresh_image_matches"])
        self.assertEqual(result["image_sha256"], self.digest)
        self.assertIn("not OCR", result["classification_source"])
        self.fresh.assert_called_once_with("configured.exe", 42, self.identity)
        self.assertEqual(self.target.verify.call_count, 2)

    def test_changed_png_or_live_pixels_refuse(self):
        self.image_path.write_bytes(b"altered")
        with self.assertRaisesRegex(ValueError, "PNG"):
            visual.verify(self.target, self.confirmation)
        self.fresh.assert_not_called()
        self.image_path.write_bytes(b"verified PNG bytes")
        self.fresh.return_value = "f" * 64
        with self.assertRaisesRegex(ValueError, "Live dialog image changed"):
            visual.verify(self.target, self.confirmation)

    def test_foreign_identity_or_wrong_capture_metadata_refuse_before_capture(self):
        original = copy.deepcopy(self.meta)
        for updates in ({"window_handle": 99}, {"window_pid": 999}, {"window_title": "Save"},
                        {"window_class": "TShowException"}, {"capture_method": "BitBlt"},
                        {"exact_window_requested": False}, {"capture_dpi_awareness": None},
                        {"pinned_process_identity": dict(self.identity, creation_filetime=999)}):
            self.meta = dict(original, **updates)
            self.write()
            with self.assertRaises(ValueError):
                visual.verify(self.target, self.confirmation)
        self.fresh.assert_not_called()

    def test_unknown_classification_wrong_receipt_and_extra_keys_refuse(self):
        for updates in ({"classification": "other"}, {"image_sha256": "bad"}, {"extra": True}):
            with self.assertRaises(ValueError):
                visual.verify(self.target, dict(self.confirmation, **updates))
        self.receipt["tool"] = "other"
        self.write()
        with self.assertRaisesRegex(ValueError, "public exact-window"):
            visual.verify(self.target, self.confirmation)
        self.fresh.assert_not_called()


class CaptureTests(unittest.TestCase):
    def test_worker_pins_identity_and_restores_dpi_without_input(self):
        identity = {"pid": 123, "creation_filetime": 456, "exe": "configured.exe", "user_sid": "SID"}
        target, scope = Mock(identity=identity), Mock()
        with patch.object(visual, "CaptureTarget", return_value=target), patch.object(visual, "ThreadDpiScope", return_value=scope), \
             patch.object(visual, "_png", return_value=b"png") as capture:
            digest = visual.fresh_image_sha256("configured.exe", 42, identity)
            self.assertEqual(digest, hashlib.sha256(b"png").hexdigest())
            capture.assert_called_once_with(target)
        target.close.assert_called_once()
        scope.close.assert_called_once()

    def test_cleanup_failure_or_changed_identity_blocks_visual_evidence(self):
        identity = {"pid": 123, "creation_filetime": 456, "exe": "configured.exe", "user_sid": "SID"}
        target, scope = Mock(identity=identity), Mock()
        with patch.object(visual, "CaptureTarget", return_value=target), patch.object(visual, "ThreadDpiScope", return_value=scope), \
             patch.object(visual, "_png", return_value=b"png") as capture:
            with self.assertRaisesRegex(ValueError, "identity differs"):
                visual.fresh_image_sha256("configured.exe", 42, dict(identity, pid=999))
            capture.assert_not_called()
            scope.close.side_effect = OSError("DPI restore failed")
            with self.assertRaisesRegex(ValueError, "DPI restore failed"):
                visual.fresh_image_sha256("configured.exe", 42, identity)

    def test_unfinished_read_only_capture_refuses_without_waiting_indefinitely(self):
        worker = Mock()
        worker.is_alive.return_value = True
        with patch.object(visual.threading, "Thread", return_value=worker):
            with self.assertRaisesRegex(OSError, "timed out; no input"):
                visual.fresh_image_sha256("configured.exe", 42, {})
        worker.start.assert_called_once()
        worker.join.assert_called_once_with(5)

    def test_png_uses_same_printwindow_rgb_encoding_and_releases_gdi(self):
        from PIL import Image
        bitmap = Mock()
        bitmap.GetInfo.return_value = {"bmWidth": 2, "bmHeight": 1}
        bitmap.GetBitmapBits.return_value = bytes(8)
        bitmap.GetHandle.return_value = 88
        source, destination = Mock(), Mock()
        source.CreateCompatibleDC.return_value = destination
        destination.SelectObject.return_value = None
        destination.GetSafeHdc.return_value = 77
        gui = SimpleNamespace(GetWindowRect=Mock(return_value=(0, 0, 2, 1)), GetWindowDC=Mock(return_value=66),
                              ReleaseDC=Mock(), DeleteObject=Mock())
        ui = SimpleNamespace(CreateDCFromHandle=Mock(return_value=source), CreateBitmap=Mock(return_value=bitmap))
        native = SimpleNamespace(PrintWindow=Mock(return_value=1))
        target = Mock(hwnd=42)
        with patch.dict(sys.modules, {"win32gui": gui, "win32ui": ui}), patch("ctypes.WinDLL", return_value=native):
            png = visual._png(target)
        expected = io.BytesIO()
        Image.new("RGB", (2, 1)).save(expected, format="PNG")
        self.assertEqual(png, expected.getvalue())
        native.PrintWindow.assert_called_once_with(42, 77, 2)
        gui.DeleteObject.assert_called_once_with(88)
        gui.ReleaseDC.assert_called_once_with(42, 66)
        destination.DeleteDC.assert_called_once()
        source.DeleteDC.assert_called_once()
        self.assertEqual(target.verify.call_count, 2)


if __name__ == "__main__":
    unittest.main()
