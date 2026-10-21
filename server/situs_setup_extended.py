"""Guarded semantic Situs setup operations through Windows UI Automation.

No scripting API or unverified custom-control messages are used. Every mutation
requires a complete freshly matching inspection receipt and one supported UIA
pattern. Public callers must hold the bridge and emergency-operation locks.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess

from screenshot_target import CaptureTarget

DIALOG_TITLES = {"setup": "Situs Routing Strategies", "strategy": "Situs Strategy Editor", "directions": "Layer Directions"}
DEFAULT_STRATEGIES = {"Cleanup", "Default 2 Layer Board", "Default 2 Layer With Edge Connectors", "Default Multi Layer Board", "General Orthogonal", "Via Miser"}
PASSES = {"Adjacent Memory", "Clean Pad Entries", "Completion", "Fan out Signal", "Fan out to Plane", "Globally Optimised Main", "Globally Optimized Main", "Hug", "Layer Patterns", "Main", "Memory", "Multilayer Main", "Recorner", "Spread", "Straighten"}
DIRECTIONS = {"Not Used", "Horizontal", "Vertical", "Any", "1 O'Clock", "2 O'Clock", "4 O'Clock", "5 O'Clock", "45 Up", "45 Down", "Fan Out", "Automatic"}
CHECKBOXES = {"lock_preroutes": ("setup", "lock all pre-routes"), "ripup": ("setup", "rip-up violations after routing"), "orthogonal": ("strategy", "orthogonal")}
BUTTONS = {
    "setup": {"add_strategy": "add", "edit_strategy": "edit", "remove_strategy": "remove", "duplicate_strategy": "duplicate", "edit_directions": "edit layer directions", "edit_rules": "edit rules", "save_report": "save report as", "ok": "ok", "cancel": "cancel", "route_all": "route all"},
    "strategy": {"add_pass": "add", "remove_pass": "remove", "move_pass_up": "move up", "move_pass_down": "move down", "ok": "ok", "cancel": "cancel"},
    "directions": {"ok": "ok", "cancel": "cancel"},
}
TEXT_FIELDS = {"name": {"name", "strategy name"}, "description": {"description", "strategy description"}}
STATE_FIELDS = {"toggle": "toggle", "value": "value", "range": "range", "select": "selected", "expand": "expand"}


def _label(value):
    return str(value).replace("&", "").strip().rstrip(".: >").lstrip("< ").casefold()


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _run_worker(request, timeout=15):
    """Kill only our worker on timeout; the remote action outcome remains unknown."""
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        process = subprocess.run([str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(Path(__file__).with_name("situs_uia_worker.ps1"))],
            input=json.dumps(request, ensure_ascii=False, allow_nan=False), text=True, encoding="utf-8", capture_output=True,
            timeout=timeout, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), shell=False)
    except subprocess.TimeoutExpired as error:
        return {"success": False, "error": "UIA worker timed out; no retry", "input_attempted": request["operation"] != "inspect",
                "outcome_unknown": request["operation"] != "inspect", "timeout": True}
    lines = [line for line in process.stdout.splitlines() if line.strip()]
    try:
        result = json.loads(lines[-1])
        if not isinstance(result, dict) or type(result.get("success")) is not bool:
            raise ValueError("invalid worker result")
    except (IndexError, ValueError):
        return {"success": False, "error": "UIA worker returned no valid result", "input_attempted": request["operation"] != "inspect",
                "outcome_unknown": request["operation"] != "inspect", "worker_exit_code": process.returncode}
    result["worker_exit_code"] = process.returncode
    return result


def _bind(target, kind, expected_main_title):
    target.verify()
    if not isinstance(expected_main_title, str) or ".pcbdoc" not in expected_main_title.casefold():
        raise ValueError("An exact main-window title containing the PCB filename is required")
    mains = [row for row in target.api.windows(target.identity["pid"]) if row["class"] == "TDocumentForm"]
    if len(mains) != 1 or mains[0]["title"] != expected_main_title or not target.api.matches(mains[0]) or target.api.title(mains[0]["hwnd"]) != expected_main_title:
        raise ValueError("Exact main-window identity does not match the pinned Altium session")
    if kind == "main":
        if target.hwnd != mains[0]["hwnd"] or target.row["class"] != "TDocumentForm":
            raise ValueError("Read-only main inspection requires the exact TDocumentForm")
    elif kind not in DIALOG_TITLES or target.row["title"] != DIALOG_TITLES[kind] or not target.row["enabled"]:
        raise ValueError("Expected an enabled exact Situs dialog title")
    if kind == "setup" and target.row["class"] != "TSitusSetup":
        raise ValueError("Expected exact TSitusSetup class")
    return mains[0]


def _request(target, main, kind, operation="inspect", text_limit=4096):
    return {"identity": target.identity, "dialog": {"hwnd": target.hwnd, "class": target.row["class"], "title": target.row["title"]},
            "main": {"hwnd": main["hwnd"], "title": main["title"]}, "operation": operation,
            "text_limit": text_limit, "allow_partial": kind == "main"}


def _receipt(target, main, kind, result):
    receipt = {"identity": target.identity, "dialog": {"hwnd": target.hwnd, "class": target.row["class"], "title": target.row["title"]},
               "main": {"hwnd": main["hwnd"], "title": main["title"]}, "kind": kind,
               "nodes": result.get("nodes", []), "complete": result.get("complete") is True}
    return dict(receipt, snapshot_sha256=_digest(receipt))


def _node(receipt, runtime_id):
    if not isinstance(runtime_id, list) or not runtime_id or any(type(v) is not int for v in runtime_id):
        raise ValueError("Use an observed UIA runtime_id integer array")
    matches = [node for node in receipt["nodes"] if node.get("runtime_id") == runtime_id]
    if len(matches) != 1:
        raise ValueError("One exact observed accessibility element is required")
    node = matches[0]
    if node.get("pid") != receipt["identity"]["pid"] or node.get("enabled") is not True or node.get("offscreen") is not False or node.get("password"):
        raise ValueError("Element must be visible, enabled, non-password and in the pinned process")
    return node


def _has(node, pattern):
    if pattern + "PatternIdentifiers.Pattern" not in node.get("patterns", []):
        raise ValueError("Required UIA " + pattern + " pattern is unavailable; no fallback")


def _plan(receipt, action, runtime_id, value, setting):
    """Map a documented semantic action to one observable UIA operation."""
    kind = receipt["kind"]
    if kind == "main":
        raise ValueError("Main-window accessibility is read-only")
    node = _node(receipt, runtime_id)
    states = node.get("states", {})
    name = _label(node.get("name", ""))
    label = _label(node.get("label", ""))
    control = node.get("control_type")
    operation = None
    if action == "set_checkbox":
        if setting not in CHECKBOXES or CHECKBOXES[setting] != (kind, name) or type(value) is not bool:
            raise ValueError("Exact documented checkbox and boolean target state required")
        _has(node, "Toggle")
        if type(states.get("toggle")) is not int or states["toggle"] not in (0, 1):
            raise ValueError("Checkbox binary state is not observable")
        operation = "toggle"
        if states["toggle"] == int(value):
            return node, operation, True
    elif action == "set_text":
        if kind != "strategy" or setting not in TEXT_FIELDS or not ({name, label} & TEXT_FIELDS[setting]) or control != "ControlType.Edit":
            raise ValueError("Exact labeled strategy Name or Description edit required")
        if not isinstance(value, str) or len(value) > 2048 or "\x00" in value or (setting == "name" and (not value.strip() or value in DEFAULT_STRATEGIES)):
            raise ValueError("Invalid custom strategy text")
        _has(node, "Value")
        if states.get("value_readonly") is not False:
            raise ValueError("Value field is not explicitly writable")
        operation = "value"
        if states.get("value") == value:
            return node, operation, True
    elif action == "set_via_effort":
        sliders = [n for n in receipt["nodes"] if n.get("control_type") == "ControlType.Slider" and n.get("pid") == receipt["identity"]["pid"]]
        if kind != "strategy" or control != "ControlType.Slider" or len(sliders) != 1 or sliders[0]["runtime_id"] != runtime_id:
            raise ValueError("One exact strategy via-effort slider required")
        _has(node, "RangeValue")
        state = states.get("range", {})
        if type(value) not in (float, int) or not math.isfinite(value) or state.get("readonly") is not False or not state["minimum"] <= value <= state["maximum"]:
            raise ValueError("Via-effort value outside the observed writable range")
        operation = "range"
        if state["value"] == value:
            return node, operation, True
    elif action in {"select_strategy", "select_pass", "select_direction"}:
        expected_kind = {"select_strategy": "setup", "select_pass": "strategy", "select_direction": "directions"}[action]
        allowed_names = PASSES if action == "select_pass" else DIRECTIONS if action == "select_direction" else None
        if kind != expected_kind or control not in {"ControlType.ListItem", "ControlType.DataItem"} or not node.get("name") or (allowed_names is not None and node["name"] not in allowed_names):
            raise ValueError("Exact documented item in its corresponding dialog required")
        _has(node, "SelectionItem")
        if type(states.get("selected")) is not bool or not states.get("selection_container"):
            raise ValueError("Selection state and container are not observable")
        operation = "select"
        if states["selected"]:
            return node, operation, True
    elif action == "expand_direction":
        if kind != "directions" or control != "ControlType.ComboBox":
            raise ValueError("Exact layer-direction combo required")
        _has(node, "ExpandCollapse")
        if states.get("expand") not in (0, 1):
            raise ValueError("Combo state is not observable")
        operation = "expand"
        if states["expand"] == 1:
            return node, operation, True
    elif action in BUTTONS[kind]:
        if control != "ControlType.Button" or name != BUTTONS[kind][action]:
            raise ValueError("Exact documented button required for this action")
        _has(node, "Invoke")
        if kind == "setup" and action in {"edit_strategy", "remove_strategy", "duplicate_strategy"}:
            selected = [n for n in receipt["nodes"] if n.get("control_type") in {"ControlType.ListItem", "ControlType.DataItem"} and n.get("states", {}).get("selected") is True]
            if len(selected) != 1:
                raise ValueError("One selected strategy must be observable")
            if action != "duplicate_strategy" and selected[0].get("name") in DEFAULT_STRATEGIES:
                raise ValueError("Builtin strategies cannot be edited or removed")
        operation = "invoke"
    else:
        raise ValueError("Unsupported semantic Situs action")
    return node, operation, False


def _verify_setting(before, after, runtime_id, operation, value):
    if not after.get("complete"):
        raise ValueError("Complete post-action readback unavailable")
    current = _node(after, runtime_id)
    states = current.get("states", {})
    desired = {"toggle": int(value) if type(value) is bool else value, "value": value, "range": value, "select": True, "expand": 1}[operation]
    actual = states.get(STATE_FIELDS[operation])
    if operation == "range":
        actual = actual.get("value") if isinstance(actual, dict) else None
    if actual != desired:
        raise ValueError("Requested UIA state was not verified after one operation")
    other_before = {tuple(n["runtime_id"]): n.get("states", {}).get("toggle") for n in before["nodes"] if n["runtime_id"] != runtime_id and "toggle" in n.get("states", {})}
    other_after = {tuple(n["runtime_id"]): n.get("states", {}).get("toggle") for n in after["nodes"] if n["runtime_id"] != runtime_id and "toggle" in n.get("states", {})}
    if other_before != other_after:
        raise ValueError("An unrelated observable checkbox changed; do not retry")


def control(exe, action, target_hwnd, expected_main_title, dialog_kind="setup", expected_snapshot=None,
            runtime_id=None, value=None, setting=None):
    """Inspect, capture a report, or perform one semantic settings gesture.

    Inspection returns a snapshot_sha256. Pass that entire snapshot back for a
    mutation; it is compared with fresh state and rechecked inside the worker.
    An Invoke success is only dispatch acknowledgement; it is not evidence of
    acceptance, persistence, route completion or a completed file export.
    """
    result = {"success": False, "action": action, "input_attempted": False, "completion_verified": False,
              "setting_readback_verified": False, "retry_performed": False, "script_dispatched": False,
              "document_save_requested": False, "document_binding": "exact main-window title; full PCB path is not verified"}
    target = None
    try:
        if action in {"inspect", "read_report"} and any(arg is not None for arg in (expected_snapshot, runtime_id, value, setting)):
            raise ValueError("Read-only actions accept no mutation arguments")
        if action == "read_report" and dialog_kind != "setup":
            raise ValueError("Routing Setup Report requires the setup dialog")
        target = CaptureTarget(exe, target_hwnd)
        main = _bind(target, dialog_kind, expected_main_title)
        request = _request(target, main, dialog_kind, text_limit=262144 if action == "read_report" else 4096)
        observed = _run_worker(request)
        if not observed.get("success"):
            raise ValueError(observed.get("error", "Accessibility inspection failed"))
        if _bind(target, dialog_kind, expected_main_title) != main:
            raise ValueError("Main-window state changed during inspection")
        receipt = _receipt(target, main, dialog_kind, observed)
        result["snapshot"] = receipt
        if action in {"inspect", "read_report"}:
            if action == "read_report":
                docs = [n for n in receipt["nodes"] if n.get("control_type") == "ControlType.Document" and "text" in n.get("states", {})]
                if len(docs) != 1 or not docs[0]["states"]["text"].strip() or docs[0]["states"].get("text_truncated") or not receipt["complete"]:
                    raise ValueError("One complete observable report document required; no inferred HTML export")
                result.update(report_text=docs[0]["states"]["text"], report_runtime_id=docs[0]["runtime_id"], report_format="accessibility_text", report_complete=True)
            return dict(result, success=True, no_input=True)
        if not receipt["complete"] or not isinstance(expected_snapshot, dict) or expected_snapshot != receipt:
            raise ValueError("Expected complete inspection snapshot differs from current state; no input")
        node, operation, noop = _plan(receipt, action, runtime_id, value, setting)
        if noop:
            return dict(result, success=True, no_input=True, setting_readback_verified=True, already_satisfied=True)
        request.update(operation=operation, runtime_id=runtime_id, value=value, expected_nodes=receipt["nodes"])
        target.verify()
        changed = _run_worker(request)
        result.update(input_attempted=bool(changed.get("input_attempted")), operation=operation,
                      operation_returned=bool(changed.get("operation_returned")))
        if not changed.get("success"):
            raise ValueError(changed.get("error", "Accessibility action failed"))
        if operation == "invoke":
            result["post_action_readback_error"] = changed.get("readback_error")
            if changed.get("complete"):
                result["after_snapshot"] = _receipt(target, main, dialog_kind, changed)
            return dict(result, success=True, outcome="One button invocation returned; verify resulting dialog or routing state separately")
        target.verify()
        if _bind(target, dialog_kind, expected_main_title) != main:
            raise ValueError("Main-window state changed after action")
        after = _receipt(target, main, dialog_kind, changed)
        result["after_snapshot"] = after
        _verify_setting(receipt, after, runtime_id, operation, value)
        return dict(result, success=True, setting_readback_verified=True, outcome="Requested UIA state verified; dialog acceptance and persistence are separate")
    except Exception as error:
        return dict(result, error=str(error), blocked=not result["input_attempted"], outcome_unknown=result["input_attempted"] and not result["setting_readback_verified"])
    finally:
        if target is not None:
            target.close()
