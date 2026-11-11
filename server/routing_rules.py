"""Validated PCB rule specs. This module does not dispatch to Altium.

Extended properties are documented/installed-interface candidates, not native
qualification. A saved native readback is required for each newly used branch.
"""
from math import isfinite
from pathlib import PureWindowsPath

RULE_FIELDS = {
    "Width": {"min", "max", "preferred", "impedance_driven", "min_impedance", "max_impedance", "preferred_impedance"},
    "RoutingViaStyle": {"hole_min", "hole_max", "hole_preferred", "pad_min", "pad_max", "pad_preferred", "via_style"},
    "RoutingLayers": set(),
    "FanoutControl": {"fanout_style", "fanout_direction", "bga_direction", "bga_via_mode", "via_grid"},
    "Clearance": {"gap", "ignore_pad_to_pad", "matrix_enabled"},
    "BoardOutlineClearance": {"gap"},
    "HoleSize": {"min", "max"},
    "MinimumAnnularRing": {"min"},
    "MinimumSolderMaskSliver": {"gap"},
    "HoleToHoleClearance": {"gap"},
    "SilkToSolderMaskClearance": {"gap"},
    "SilkToSilkClearance": {"gap"},
    "ComponentClearance": {"gap", "vertical_gap"},
    "Height": {"min", "max", "preferred"},
    "SolderMaskExpansion": {"expansion"},
    "PasteMaskExpansion": {"expansion"},
}
BOOLEAN_FIELDS = {"impedance_driven", "ignore_pad_to_pad", "matrix_enabled"}
ENUM_RANGES = {"via_style": (0, 3), "fanout_style": (0, 4), "fanout_direction": (0, 5),
               "bga_direction": (0, 5), "bga_via_mode": (0, 1)}
RULE_KEYS = {"name", "kind", "scope1", "scope2", "priority", "enabled", "comment", "delete", "values"}


def _field(value, label, allow_empty=True):
    if not isinstance(value, str) or any(c in value for c in "|\r\n\x00"):
        raise ValueError(f"{label} must be text without spec delimiters")
    if not allow_empty and not value.strip():
        raise ValueError(f"{label} must not be empty")
    return value


def _integer(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{label} must be an integer in {low}..{high}")
    return value


def _number(value, label, allow_negative=False, coordinate=True):
    if isinstance(value, bool) or not isinstance(value, (int, float)) :
        raise ValueError(f"{label} must be a finite number")
    try:
        finite = isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{label} must be a finite number")
    if not allow_negative and value < 0:
        raise ValueError(f"{label} must not be negative")
    # TCoord is signed 32-bit; 1 mm is approximately 393700.7874 native units.
    if coordinate and abs(value) > 5454:
        raise ValueError(f"{label} exceeds the native coordinate range")
    return format(value, ".15g")


def _bool(value, label):
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be boolean")
    return "1" if value else "0"


def _ordered(values, names, label):
    present = [(name, values[name]) for name in names if name in values]
    for (left, a), (right, b) in zip(present, present[1:]):
        if a > b:
            raise ValueError(f"{label}: {left} must be <= {right}")


def flatten_values(kind, values):
    """Return stable numeric key/value pairs; omitted fields remain omitted."""
    if kind not in RULE_FIELDS:
        raise ValueError("a supported kind is required when setting values")
    if not isinstance(values, dict):
        raise ValueError("values must be an object")
    result = {}
    structured = {"layer_widths", "routing_layers", "clearance_matrix"}
    for key, value in values.items():
        if key in structured:
            continue
        if key not in RULE_FIELDS[kind]:
            raise ValueError(f"{kind}: unsupported value {key!r}")
        if key in BOOLEAN_FIELDS:
            result[key] = _bool(value, key)
        elif key in ENUM_RANGES:
            result[key] = str(_integer(value, key, *ENUM_RANGES[key]))
        else:
            result[key] = _number(value, key, key == "expansion", not key.endswith("impedance"))
    _ordered(values, ("min", "preferred", "max"), kind)
    _ordered(values, ("min_impedance", "preferred_impedance", "max_impedance"), kind)
    for prefix in ("hole", "pad"):
        _ordered(values, (prefix + "_min", prefix + "_preferred", prefix + "_max"), kind)
    if "layer_widths" in values:
        if kind != "Width" or not isinstance(values["layer_widths"], list):
            raise ValueError("layer_widths is a list available only for Width")
        seen = set()
        for entry in values["layer_widths"]:
            if not isinstance(entry, dict) or set(entry) - {"layer_id", "min", "preferred", "max"}:
                raise ValueError("each layer width needs layer_id and min/preferred/max fields only")
            layer = _integer(entry.get("layer_id"), "layer_id", 1, 32)
            if layer in seen or len(entry) < 2:
                raise ValueError("layer widths require unique layers and at least one value")
            seen.add(layer)
            for key in ("min", "preferred", "max"):
                if key in entry:
                    result[f"layer_{layer}_{key}"] = _number(entry[key], key)
            _ordered(entry, ("min", "preferred", "max"), f"layer {layer}")
    if "routing_layers" in values:
        if kind != "RoutingLayers" or not isinstance(values["routing_layers"], list):
            raise ValueError("routing_layers is a list available only for RoutingLayers")
        seen = set()
        for entry in values["routing_layers"]:
            if not isinstance(entry, dict) or set(entry) != {"layer_id", "allowed"}:
                raise ValueError("routing layer entries need exactly layer_id and allowed")
            layer = _integer(entry["layer_id"], "layer_id", 1, 32)
            if layer in seen:
                raise ValueError("routing_layers must not repeat a layer")
            seen.add(layer)
            result[f"layer_{layer}_allowed"] = _bool(entry["allowed"], "allowed")
    if "clearance_matrix" in values:
        if kind not in {"Clearance", "BoardOutlineClearance"} or not isinstance(values["clearance_matrix"], list):
            raise ValueError("clearance_matrix is a list available only for clearance rules")
        if "gap" in values:
            raise ValueError("gap and clearance_matrix must be separate operations; gap may replace matrix values")
        seen = set()
        for entry in values["clearance_matrix"]:
            if not isinstance(entry, dict) or set(entry) != {"first", "second", "gap"}:
                raise ValueError("matrix entries need exactly first, second and gap")
            a = _integer(entry["first"], "first", 0, 14)
            b = _integer(entry["second"], "second", 0, 14)
            pair = tuple(sorted((a, b)))
            if pair in seen:
                raise ValueError("matrix must not repeat a symmetric pair")
            if kind == "BoardOutlineClearance" and not ((a <= 9 and 10 <= b <= 12) or (b <= 9 and 10 <= a <= 12)):
                raise ValueError("board-outline cells must pair primitive 0..9 with edge 10..12")
            if kind == "Clearance" and (a > 9 or b > 9):
                raise ValueError("electrical clearance cells currently require primitive ids 0..9")
            seen.add(pair)
            result[f"matrix_{a}_{b}"] = _number(entry["gap"], "gap")
    return result


def build_rule_spec(pcb_path, rules):
    """Validate every record before producing the existing pipe spec format.

    This does not inspect a live board. Kind, final priority collisions, existing
    omitted bounds, and exact saved values still require native preflight/readback.
    """
    _field(pcb_path, "pcb_path", False)
    if not PureWindowsPath(pcb_path).is_absolute() or not pcb_path.lower().endswith(".pcbdoc"):
        raise ValueError("pcb_path must be an absolute Windows .PcbDoc path")
    if not isinstance(rules, list) or not rules or len(rules) > 512:
        raise ValueError("rules must contain 1..512 objects")
    lines = ["PCB|" + pcb_path]
    seen = set()
    explicit_priorities = set()
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - RULE_KEYS:
            raise ValueError("unknown rule fields or non-object rule")
        name = _field(rule.get("name"), "name", False)
        if name.casefold() in seen:
            raise ValueError("each rule name may occur only once")
        seen.add(name.casefold())
        delete = rule.get("delete", False)
        if not isinstance(delete, bool):
            raise ValueError("delete must be boolean")
        if delete:
            if set(rule) - {"name", "delete"}:
                raise ValueError("delete must not be combined with modifications")
            lines.append("DELETE|" + name)
            continue
        kind = _field(rule.get("kind", ""), "kind")
        if kind and kind not in RULE_FIELDS:
            raise ValueError("unsupported rule kind")
        raw_values = rule.get("values", {})
        if not isinstance(raw_values, dict):
            raise ValueError("values must be an object")
        values = flatten_values(kind, raw_values) if raw_values else {}
        priority = rule.get("priority")
        if priority is not None:
            priority = _integer(priority, "priority", 1, 65535)
            if not kind:
                raise ValueError("kind is required with an explicit priority")
            if (kind, priority) in explicit_priorities:
                raise ValueError("requested rules of the same kind must have distinct priorities")
            explicit_priorities.add((kind, priority))
        enabled = rule.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        metadata = [_field(rule.get(key, ""), key) for key in ("scope1", "scope2", "comment")]
        if "comment" in rule and not metadata[2]:
            raise ValueError("clearing comment is not supported by the legacy pipe format")
        if any(key in rule and not rule[key].strip() for key in ("scope1", "scope2")):
            raise ValueError("explicit scopes must not be empty")
        lines.append("RULE|{}|{}|{}|{}|{}|{}|{}|{}".format(
            name, kind, metadata[0], metadata[1], "" if priority is None else priority,
            "" if enabled is None else str(enabled).lower(), metadata[2],
            ";".join(f"{key}={value}" for key, value in values.items())))
    return "\n".join(lines) + "\n"
