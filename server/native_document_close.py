"""Fixed clean non-active document close program; no dispatch, process or UI code."""
from __future__ import annotations

import json
from pathlib import Path, PureWindowsPath
import re

TEMPLATE = Path(__file__).with_name("native_templates") / "close_clean_document.pas.in"
TERMINAL_STAGES = frozenset({"completed", "guard_failed", "readback_failed", "native_exception"})
STAGES = TERMINAL_STAGES | {"entered", "before_close"}
COUNT_FIELDS = ("before_document_count", "after_document_count", "before_target_count",
                "after_target_count", "before_module_count", "after_module_count")
BOOL_FIELDS = ("success", "close_attempted", "closed_verified", "current_pcb_preserved",
               "other_documents_unchanged")


def _path(value, suffix, name):
    if not isinstance(value, str) or not value or any(c in value for c in "\r\n\0"):
        raise ValueError(name + " must be single-line text")
    path = PureWindowsPath(value)
    if not path.is_absolute() or path.suffix.casefold() != suffix or ".." in path.parts:
        raise ValueError(name + " must be an absolute " + suffix + " path without traversal")
    if any(":" in part for part in path.parts[1:]):
        raise ValueError(name + " must not contain an alternate stream")
    return str(path)


def _literal(value):
    return "'" + value.replace("'", "''") + "'"


def build_close_request(active_pcb_path, target_document_path, operation_id):
    active = _path(active_pcb_path, ".pcbdoc", "active_pcb_path")
    target = _path(target_document_path, ".pcbdoc", "target_document_path")
    if active.casefold() == target.casefold():
        raise ValueError("Only a non-active document may be closed")
    if not isinstance(operation_id, str) or re.fullmatch("[0-9a-f]{32}", operation_id) is None:
        raise ValueError("operation_id must be 32 lowercase hexadecimal characters")
    return dict(schema_version=1, operation_id=operation_id, active_pcb_path=active,
                target_document_path=target, target_kind="PCB", scope="exact_clean_nonactive_document",
                document_save_requested=False, focus_requested=False, pid_verified_by_native=False)


def _request(request):
    if not isinstance(request, dict) or not {"active_pcb_path", "target_document_path", "operation_id"} <= set(request):
        raise ValueError("A fixed close request is required")
    expected = build_close_request(request["active_pcb_path"], request["target_document_path"], request["operation_id"])
    if set(request) != set(expected) or any(type(request[k]) is not type(v) or request[k] != v for k, v in expected.items()):
        raise ValueError("Close request contract changed")
    return expected


def build_close_program(request, receipt_path):
    """Return standalone RunCleanDocumentClose source; host pins process and serializes."""
    request = _request(request)
    receipt_path = _path(str(receipt_path), ".json", "receipt_path")
    prefix = json.dumps(request, ensure_ascii=True, separators=(",", ":"))[:-1]
    replacements = {"__METADATA__": _literal(prefix), "__RECEIPT_PATH__": _literal(receipt_path),
                    "__ACTIVE_PCB_PATH__": _literal(request["active_pcb_path"]),
                    "__TARGET_PATH__": _literal(request["target_document_path"])}
    return re.sub("|".join(re.escape(k) for k in replacements), lambda m: replacements[m.group()],
                  TEMPLATE.read_text(encoding="utf-8"))


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate native receipt key")
        result[key] = value
    return result


def parse_close_receipt(data, request):
    expected = _request(request)
    if not isinstance(data, (str, bytes)) or len(data) > 65536:
        raise ValueError("Native close receipt must be bounded JSON text")
    result = json.loads(data, object_pairs_hook=_pairs)
    fields = set(BOOL_FIELDS) | set(COUNT_FIELDS) | {"stage", "error"}
    if not isinstance(result, dict) or set(result) != set(expected) | fields:
        raise ValueError("Invalid native close receipt schema")
    for key, value in expected.items():
        if type(result[key]) is not type(value) or result[key] != value:
            raise ValueError("Native close receipt identity/contract mismatch: " + key)
    if not isinstance(result["stage"], str) or result["stage"] not in STAGES or not isinstance(result["error"], str):
        raise ValueError("Invalid native close stage/error")
    if any(type(result[k]) is not bool for k in BOOL_FIELDS):
        raise ValueError("Invalid native close boolean")
    if any(result[k] is not None and (type(result[k]) is not int or result[k] < 0) for k in COUNT_FIELDS):
        raise ValueError("Invalid native inventory count")
    complete = result["stage"] == "completed"
    if result["success"] != complete:
        raise ValueError("Success requires the completed native stage")
    if complete:
        if (result["error"] or not all(result[k] for k in BOOL_FIELDS)
                or any(result[k] is None for k in COUNT_FIELDS)
                or result["before_target_count"] != 1 or result["after_target_count"] != 0
                or result["before_document_count"] < 2
                or result["after_document_count"] != result["before_document_count"] - 1
                or result["before_module_count"] < 1
                or result["after_module_count"] != result["before_module_count"]):
            raise ValueError("Completed close lacks exact target and preserved-inventory readback")
    elif result["stage"] in TERMINAL_STAGES and not result["error"]:
        raise ValueError("A refused/failed terminal receipt requires an error")
    if result["closed_verified"] and (not result["close_attempted"] or result["after_target_count"] != 0):
        raise ValueError("Closed assertion lacks target-absence readback")
    if result["stage"] == "before_close" and not result["close_attempted"]:
        raise ValueError("Before-close marker must conservatively record an attempt")
    return dict(result, completion_verified=complete, terminal=result["stage"] in TERMINAL_STAGES,
                outcome_unknown=result["close_attempted"] and not complete, retry_allowed=False)


def read_close_receipt(path, request):
    path = Path(path)
    if not path.exists():
        return dict(success=False, stage="not_observed", completion_verified=False,
                    closed_verified=False, outcome_unknown=True, retry_allowed=False)
    with path.open("rb") as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("Oversized native close receipt")
    return parse_close_receipt(data.decode("utf-8-sig"), request)
