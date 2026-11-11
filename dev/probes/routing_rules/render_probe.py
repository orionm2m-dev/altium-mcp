"""Render one standalone, read-only PAS probe; never dispatch or write a PCB."""
from pathlib import Path, PureWindowsPath
import json
import re

DIRECTORY = Path(__file__).resolve().parent


def _path(value, suffix, label):
    if not isinstance(value, str) or any(c in value for c in "\r\n\0"):
        raise ValueError(label + " must be single-line text")
    path = PureWindowsPath(value)
    if not path.is_absolute() or path.suffix.casefold() != suffix or ".." in path.parts:
        raise ValueError(label + " must be an absolute " + suffix + " path without traversal")
    return str(path)


def render_probe(name, pcb_path, report_path, probe_id):
    """Return source only. Caller must use a dedicated project, helpers disabled.

    The parent-qualified runner owns dispatch. A matching completed receipt with
    matched_rules > 0 qualifies only the exercised getters, never setters.
    """
    manifest = json.loads((DIRECTORY / "probe_manifest.json").read_text("utf-8"))
    if name not in manifest["sequence"]:
        raise ValueError("unknown standalone probe")
    pcb_path = _path(pcb_path, ".pcbdoc", "pcb_path")
    report_path = _path(report_path, ".json", "report_path")
    if not isinstance(probe_id, str) or re.fullmatch(r"[0-9a-f]{32}", probe_id) is None:
        raise ValueError("probe_id must be a unique lowercase UUID hex string")
    source = (DIRECTORY / (name + ".pas.in")).read_text("utf-8")
    values = {"__PCB_PATH__": pcb_path, "__REPORT_PATH__": report_path, "__PROBE_ID__": probe_id}
    # One substitution pass prevents placeholder-like text inside paths from
    # being interpreted again. Delphi strings escape apostrophes by doubling.
    return re.sub(r"__PCB_PATH__|__REPORT_PATH__|__PROBE_ID__",
                  lambda match: values[match.group()].replace("'", "''"), source)
