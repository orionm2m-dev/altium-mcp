"""Strict isolated native Lock Pre-routes programs and correlated readback.

No app imports, UI Automation, process launch, board file writes, or dispatch.
The registered caller owns session pinning, serialization and native execution.
Getter and setter qualification are independent; never infer one from the other.
"""
from __future__ import annotations

import json
from pathlib import Path, PureWindowsPath
import re

TEMPLATE = Path(__file__).with_name("native_templates") / "situs_lock_preroutes.pas.in"
STAGES = {"entered", "guard_failed", "before_options", "before_getter", "expected_state_mismatch",
          "before_setter", "readback_failed", "completed"}
PARTIAL_STAGES = ("entered", "before_options", "before_getter", "before_setter")


def _path(value, suffix, label):
    if not isinstance(value, str) or not value or any(c in value for c in "\r\n\0"):
        raise ValueError(label + " must be single-line text")
    path = PureWindowsPath(value)
    if not path.is_absolute() or path.suffix.casefold() != suffix or ".." in path.parts:
        raise ValueError(label + " must be an absolute " + suffix + " path without traversal")
    if any(":" in part for part in path.parts[1:]):
        raise ValueError(label + " must not contain an alternate stream")
    return str(path)


def _literal(value):
    return "'" + value.replace("'", "''") + "'"


def build_settings_request(pcb_path, operation_id, action="inspect", expected_checked=None, checked=None):
    """Describe one operation. The option is global to the pinned PCB server."""
    pcb_path = _path(pcb_path, ".pcbdoc", "pcb_path")
    if not isinstance(operation_id, str) or re.fullmatch(r"[0-9a-f]{32}", operation_id) is None:
        raise ValueError("operation_id must be a unique 32-character lowercase hex id")
    if action not in {"inspect", "set"}:
        raise ValueError("action must be inspect or set")
    if action == "inspect":
        if expected_checked is not None or checked is not None:
            raise ValueError("inspect accepts no expected or requested state")
    elif type(expected_checked) is not bool or type(checked) is not bool:
        raise ValueError("set requires boolean expected_checked and checked")
    return {"schema_version": 1, "operation_id": operation_id, "pcb_path": pcb_path,
            "action": action, "setting": "lock_preroutes", "scope": "global_pcb_system_options",
            "expected_checked": expected_checked, "requested_checked": checked,
            "no_board_write": True, "document_save_requested": False,
            "persistence_verified": False, "pid_verified_by_native": False}


def build_lock_preroutes_program(pcb_path, operation_id, receipt_path, action="inspect",
                                expected_checked=None, checked=None):
    """Return a standalone Run procedure; caller must disable shared helpers.

    Read programs contain no setter access. Set compares fresh state, performs
    at most one assignment and reacquires/readbacks the global options. If the
    requested state already exists, it succeeds without input even when the
    expected state is stale. Otherwise an expected-state mismatch blocks input.
    """
    request = build_settings_request(pcb_path, operation_id, action, expected_checked, checked)
    receipt_path = _path(str(receipt_path), ".json", "receipt_path")
    prefix = json.dumps(request, ensure_ascii=True, separators=(",", ":"))[:-1]
    if action == "inspect":
        body = "    SettingsReceipt('completed', SettingsBool(BeforeValue), SettingsBool(BeforeValue), '', True, False, False, False);"
    else:
        desired = "True" if checked else "False"
        expected = "True" if expected_checked else "False"
        body = """    // An already-satisfied request is an idempotent no-op, not a replay.
    if BeforeValue = DESIRED then
    begin SettingsReceipt('completed', SettingsBool(BeforeValue), SettingsBool(BeforeValue), '', True, False, False, True); Exit; end;
    if BeforeValue <> EXPECTED then
    begin SettingsReceipt('expected_state_mismatch', SettingsBool(BeforeValue), 'null', 'stale_expected_state', False, False, False, False); Exit; end;
    // Reacquire and read immediately before the single setter; never retry.
    Options := PCBServer.SystemOptions;
    if Options = nil then Exit;
    BeforeValue := Options.LockPreRoutes;
    if not SettingsGuard(Board) then
    begin SettingsReceipt('guard_failed', SettingsBool(BeforeValue), 'null', 'identity_changed_before_write', False, False, False, False); Exit; end;
    if BeforeValue = DESIRED then
    begin SettingsReceipt('completed', SettingsBool(BeforeValue), SettingsBool(BeforeValue), '', True, False, False, True); Exit; end;
    if BeforeValue <> EXPECTED then
    begin SettingsReceipt('expected_state_mismatch', SettingsBool(BeforeValue), 'null', 'stale_expected_state', False, False, False, False); Exit; end;
    // Persist the attempt marker before assignment; a partial receipt stays unknown.
    SettingsReceipt('before_setter', SettingsBool(BeforeValue), 'null', '', False, True, False, False);
    Options.LockPreRoutes := DESIRED;
    Options := PCBServer.SystemOptions;
    if Options = nil then Exit;
    AfterValue := Options.LockPreRoutes;
    if not SettingsGuard(Board) then
    begin SettingsReceipt('readback_failed', SettingsBool(BeforeValue), SettingsBool(AfterValue), 'identity_changed_after_write', False, True, BeforeValue <> AfterValue, False); Exit; end;
    if AfterValue <> DESIRED then
    begin SettingsReceipt('readback_failed', SettingsBool(BeforeValue), SettingsBool(AfterValue), 'requested_state_not_observed', False, True, BeforeValue <> AfterValue, False); Exit; end;
    SettingsReceipt('completed', SettingsBool(BeforeValue), SettingsBool(AfterValue), '', True, True, BeforeValue <> AfterValue, False);"""
        body = re.sub(r"\b(DESIRED|EXPECTED)\b", lambda m: {"DESIRED": desired, "EXPECTED": expected}[m.group()], body)
    # Every path is written at most once. A polling reader may open an earlier
    # marker without preventing the next stage from being created on Windows.
    body = re.sub(r"SettingsReceipt\(([^;]*?)\);", r"if not SettingsReceipt(\1) then Exit;", body)
    receipt_base = str(PureWindowsPath(receipt_path).with_suffix(""))
    substitutions = {"__METADATA__": _literal(prefix), "__RECEIPT_PATH__": _literal(receipt_path),
                     "__RECEIPT_BASE__": _literal(receipt_base),
                     "__ENTERED_PATH__": _literal(receipt_base + ".entered.json"),
                     "__PCB_PATH__": _literal(request["pcb_path"]), "__OPERATION_BODY__": body}
    source = TEMPLATE.read_text(encoding="utf-8")
    return re.sub(r"__METADATA__|__RECEIPT_PATH__|__RECEIPT_BASE__|__ENTERED_PATH__|__PCB_PATH__|__OPERATION_BODY__",
                  lambda m: substitutions[m.group()], source)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate native receipt key")
        result[key] = value
    return result


def parse_settings_receipt(data, request):
    """Validate exact operation identity; an entered/partial operation is not done."""
    expected = build_settings_request(request["pcb_path"], request["operation_id"], request["action"],
                                      request.get("expected_checked"), request.get("requested_checked"))
    if not isinstance(data, (str, bytes)) or len(data) > 65536:
        raise ValueError("native receipt must be JSON text no larger than64KiB")
    result = json.loads(data, object_pairs_hook=_pairs)
    fields = {"stage", "success", "before", "after", "setter_attempted", "changed", "already_satisfied", "error"}
    if not isinstance(result, dict) or set(result) != set(expected) | fields:
        raise ValueError("invalid settings receipt schema")
    for key, value in expected.items():
        if type(result[key]) is not type(value) or result[key] != value:
            raise ValueError("settings receipt identity/contract mismatch: " + key)
    if result["stage"] not in STAGES or not isinstance(result["error"], str):
        raise ValueError("invalid settings receipt stage/error")
    for key in ("success", "setter_attempted", "changed", "already_satisfied"):
        if type(result[key]) is not bool:
            raise ValueError("invalid settings receipt boolean")
    if any(result[key] is not None and type(result[key]) is not bool for key in ("before", "after")):
        raise ValueError("invalid settings readback value")
    complete = result["stage"] == "completed"
    if result["success"] != complete or (complete and (result["before"] is None or result["after"] is None or result["error"])):
        raise ValueError("completion requires exact completed readback")
    if result["changed"] and (not result["setter_attempted"] or result["before"] is None or result["after"] is None or result["before"] == result["after"]):
        raise ValueError("changed receipt lacks a matching setter/readback")
    if expected["action"] == "inspect":
        if any(result[k] for k in ("setter_attempted", "changed", "already_satisfied")) or (complete and result["before"] != result["after"]):
            raise ValueError("inspection receipt asserts mutation")
    elif complete:
        if result["after"] is not expected["requested_checked"]:
            raise ValueError("requested setting was not read back")
        if result["setter_attempted"]:
            if result["already_satisfied"] or not result["changed"] or result["before"] is not expected["expected_checked"]:
                raise ValueError("setter receipt violates expected-state contract")
        elif not result["already_satisfied"] or result["changed"] or result["before"] != result["after"]:
            raise ValueError("no-input set receipt must be an already-satisfied readback")
    return dict(result, completion_verified=complete, setting_readback_verified=complete,
                outcome_unknown=not complete and result["setter_attempted"], retry_allowed=False)


def read_settings_receipt(path, request):
    """Read a terminal receipt or the newest immutable partial-stage marker.

    The terminal path is never overwritten. Stage paths are derived here rather
    than accepted from a receipt, and their content must match their stage name.
    Historical single-file receipts remain readable for audit purposes.
    """
    path = Path(path)
    expected_stage = None
    if not path.exists():
        for stage in reversed(PARTIAL_STAGES):
            candidate = path.with_name(path.stem + "." + stage + ".json")
            if candidate.exists():
                path = candidate
                expected_stage = stage
                break
        else:
            return {"success": False, "stage": "not_observed", "completion_verified": False,
                    "setting_readback_verified": False, "outcome_unknown": True, "retry_allowed": False}
    with path.open("rb") as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("oversized native settings receipt")
    result = parse_settings_receipt(data.decode("utf-8-sig"), request)
    if expected_stage is not None and result["stage"] != expected_stage:
        raise ValueError("immutable settings marker stage does not match its filename")
    return result
