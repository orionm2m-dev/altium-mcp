"""Read saved PCB DRC options without opening Altium or writing the CFB file.

This is a disk snapshot. It cannot establish the current unsaved/native state,
that a DRC run used these options, or that the reported violations are complete.
"""
import hashlib
from io import BytesIO
import json
from pathlib import Path
import struct

STREAM = "Design Rule Checker Options6/Data"
ENUM_FILE = Path(__file__).resolve().parents[1] / "docs/situs-reference/routing-rule-enums.json"
MAX_STREAM_BYTES = 1024 * 1024
MAX_BOARD_BYTES = 128 * 1024 * 1024
BOOLEAN_FIELDS = {
    "DOMAKEDRCFILE", "DOMAKEDRCERRORLIST", "DOSUBNETDETAILS", "CHECKEXTERNALNETLIST",
    "REPORTDRILLEDSMTPADS", "REPORTINVALIDMULTILAYERPADS", "INTERNALPLANEWARNINGS",
    "VERIFYSHORTINGCOPPER", "REPORTBROKENPLANES", "REPORTDEADCOPPER",
    "REPORTSTARVEDTHERMALS", "REPORTSTRADLINGHOLES", "REPORTHOLESINVOIDS",
}


def _boundary():
    return dict(no_new_dispatch=True, native_dispatched=False, no_write=True,
                snapshot_source="saved_pcb_file", reflects_unsaved_native_state=False,
                enabled_check_coverage_live_verified=False, full_drc_verified=False)


def _ids(value, label):
    if value == "":
        return []
    items = value.split(",")
    if any(not item.isascii() or not item.isdecimal() for item in items):
        raise ValueError(f"{label} must be comma-separated unsigned integer ids")
    result = [int(item) for item in items]
    if len(result) != len(set(result)) or any(item > 65535 for item in result):
        raise ValueError(f"{label} contains duplicates or out-of-range ids")
    return result


def parse_drc_options_stream(data, rule_names=None):
    """Decode exactly one length-prefixed ASCII-key record; preserve unknown ids."""
    if not isinstance(data, bytes) or not 5 <= len(data) <= MAX_STREAM_BYTES:
        raise ValueError("invalid or oversized DRC options stream")
    length = struct.unpack_from("<I", data)[0]
    if length != len(data) - 4:
        raise ValueError("DRC options length prefix does not match stream size")
    payload = data[4:]
    if not payload.endswith(b"\0") or b"\0" in payload[:-1]:
        raise ValueError("DRC options must have one terminal NUL")
    text = payload[:-1].decode("latin1")  # lossless raw values; not filename encoding inference
    if not text.startswith("|"):
        raise ValueError("DRC options record delimiter missing")
    fields = {}
    for item in text[1:].split("|"):
        if "=" not in item:
            raise ValueError("malformed DRC options field")
        key, value = item.split("=", 1)
        if not key or key in fields:
            raise ValueError("duplicate or empty DRC options field")
        fields[key] = value
    if fields.get("RECORD") != "DesignRuleCheckerOptions":
        raise ValueError("wrong DRC options record type")
    required = {"RULESETTOCHECK", "ONLINERULESETTOCHECK", "MAXVIOLATIONCOUNT"}
    if not required <= fields.keys():
        raise ValueError("required DRC option fields missing")
    mapping = rule_names or {}
    result = {}
    for key, output in [("RULESETTOCHECK", "batch_checks"), ("ONLINERULESETTOCHECK", "online_checks")]:
        ids = _ids(fields[key], key)
        result[output] = [dict(rule_kind_id=i, rule_kind_name=mapping.get(i),
                               mapping_status="MAPPED_INSTALLED_METADATA" if i in mapping else "UNKNOWN") for i in ids]
    maximum = fields["MAXVIOLATIONCOUNT"]
    if not maximum.isascii() or not maximum.isdecimal() or int(maximum) > 2147483647:
        raise ValueError("invalid MAXVIOLATIONCOUNT")
    result["max_violation_count"] = int(maximum)
    result["boolean_options"] = {}
    for key in BOOLEAN_FIELDS & fields.keys():
        if fields[key] not in {"TRUE", "FALSE"}:
            raise ValueError(f"invalid boolean {key}")
        result["boolean_options"][key] = fields[key] == "TRUE"
    result["raw_fields"] = fields
    result["stream_sha256"] = hashlib.sha256(data).hexdigest()
    result["stream_bytes"] = len(data)
    return dict(_boundary(), success=True, **result)


def _mapping():
    raw = ENUM_FILE.read_bytes()
    source = json.loads(raw.decode("utf-8-sig"))
    enums = [entry for entry in source["enums"] if entry["name"] == "RT_PCB.TRuleKind"]
    if len(enums) != 1:
        raise ValueError("exactly one installed TRuleKind mapping is required")
    names = {}
    for item in enums[0]["values"]:
        value, name = item["value"], item["name"]
        if isinstance(value, bool) or not isinstance(value, int) or not isinstance(name, str) or value in names:
            raise ValueError("invalid or duplicate rule-kind mapping")
        names[value] = name
    return names, dict(status=source["status"], assembly=source["assembly"],
                       assembly_sha256=source["assembly_sha256"], captured_date=source["captured_date"],
                       mapping_file_sha256=hashlib.sha256(raw).hexdigest(), runtime_verified=False)


def read_saved_drc_options(pcb_path):
    """Read a bounded saved CFB snapshot. No save, document focus, or live probe."""
    try:
        if not isinstance(pcb_path, str) or not pcb_path:
            raise ValueError("pcb_path must be nonempty text")
        path = Path(pcb_path)
        if not path.is_absolute() or path.suffix.lower() != ".pcbdoc":
            raise ValueError("pcb_path must be an absolute .PcbDoc path")
        import olefile  # declared dependency; no COM, Altium, or bridge imports
        before = path.stat()
        if not 512 <= before.st_size <= MAX_BOARD_BYTES:
            raise ValueError("saved PCB is empty, invalid or exceeds the 128 MiB reader bound")
        raw = path.read_bytes()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("saved PCB changed while reading; snapshot rejected")
        with olefile.OleFileIO(BytesIO(raw), write_mode=False, raise_defects=olefile.DEFECT_INCORRECT) as storage:
            if not storage.exists(STREAM):
                raise ValueError("saved PCB has no Design Rule Checker Options6/Data stream")
            data = storage.openstream(STREAM).read(MAX_STREAM_BYTES + 1)
        names, provenance = _mapping()
        result = parse_drc_options_stream(data, names)
        result.update(path=str(path), source_sha256=hashlib.sha256(raw).hexdigest(),
                      source_bytes=len(raw), source_mtime_ns=after.st_mtime_ns, stream=STREAM,
                      rule_kind_mapping=provenance,
                      limitation="Disk options may differ from the open unsaved board and do not prove a DRC run used these settings.")
        return result
    except ImportError:
        return dict(_boundary(), success=False, error="olefile dependency is required for saved PCB option readback")
    except (OSError, ValueError, KeyError, TypeError, struct.error) as exc:
        return dict(_boundary(), success=False, error=str(exc))
