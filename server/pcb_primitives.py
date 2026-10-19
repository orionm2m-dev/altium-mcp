"""Validated, side-effect-free specifications for native PCB primitive tools."""

import math
from pathlib import PureWindowsPath
import re


_RESTRICTIONS = {"via": 1, "track": 2, "copper": 4, "smd_pad": 8, "th_pad": 16}
_PAD_FIELDS = ("x_mm", "y_mm", "size_x_mm", "size_y_mm", "hole_mm", "plated", "layer", "shape", "locked")


def _text(value, field, allow_empty=False):
    if not isinstance(value, str) or (not value and not allow_empty):
        raise ValueError(f"{field} must be a string" + ("" if allow_empty else " and not empty"))
    if any(c in value for c in "|\r\n\x00"):
        raise ValueError(f"{field} contains a spec delimiter or control character")
    return value


def _pcb_path(value):
    value = _text(value, "pcb_path")
    if not value.lower().endswith(".pcbdoc"):
        raise ValueError("pcb_path must name a .PcbDoc, not a library or another document type")
    return value


def _number(value, field, minimum=None, strict=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    if minimum is not None and (value < minimum or (strict and value == minimum)):
        raise ValueError(f"{field} must be {'greater than' if strict else 'at least'} {minimum}")
    return format(value, ".12g")


def _boolean(value, field):
    if type(value) is not bool:
        raise ValueError(f"{field} must be true or false")
    return "1" if value else "0"


def _layer(value, keepout=False):
    allowed = {"top", "bottom", "all" if keepout else "multi"}
    if keepout:
        allowed.update(f"mid{i}" for i in range(1, 31))
    if value not in allowed:
        raise ValueError(f"unsupported layer: {value!r}")
    return value


def _unknown(item, allowed):
    if not isinstance(item, dict):
        raise ValueError("each item must be an object")
    extra = set(item) - set(allowed)
    if extra:
        raise ValueError(f"unknown fields: {', '.join(sorted(extra))}")


def build_pad_spec(pcb_path, pads):
    """Build add/update records; omitted update fields remain unchanged."""
    lines = ["PCB|" + _pcb_path(pcb_path)]
    if not isinstance(pads, list) or not pads:
        raise ValueError("pads must be a nonempty list")
    seen = set()
    for pad in pads:
        _unknown(pad, {"operation", "component", "name", *_PAD_FIELDS})
        operation = pad.get("operation")
        if operation not in {"add", "update"}:
            raise ValueError("operation must be add or update")
        component = _text(pad.get("component", ""), "component", allow_empty=True)
        name = _text(pad.get("name"), "name")
        identity = (component.casefold(), name.casefold())
        if identity in seen:
            raise ValueError(f"duplicate pad target: {component}.{name}")
        seen.add(identity)
        if operation == "add":
            if component:
                raise ValueError("new pads must be free board pads; component pads can only be updated")
            required = {"x_mm", "y_mm", "size_x_mm", "size_y_mm", "hole_mm", "plated", "layer", "shape"}
            if not required.issubset(pad):
                raise ValueError(f"new pad missing fields: {', '.join(sorted(required - set(pad)))}")
        elif not set(_PAD_FIELDS).intersection(pad):
            raise ValueError("update needs at least one changed field")
        if ("size_x_mm" in pad) != ("size_y_mm" in pad):
            raise ValueError("size_x_mm and size_y_mm must be supplied together")
        values = []
        for key in _PAD_FIELDS:
            if key not in pad:
                values.append("")
            elif key in {"plated", "locked"}:
                values.append(_boolean(pad[key], key))
            elif key == "layer":
                values.append(_layer(pad[key]))
            elif key == "shape":
                if pad[key] not in {"round", "rect", "octagonal"}:
                    raise ValueError("shape must be round, rect, or octagonal")
                values.append(pad[key])
            else:
                values.append(_number(pad[key], key, 0 if key.startswith(("size_", "hole_")) else None))
        if pad.get("layer") in {"top", "bottom"} and pad.get("hole_mm", 0) > 0:
            raise ValueError("a drilled pad must use layer multi")
        lines.append("|".join(["PAD", operation, component, name, *values]))
    return "\n".join(lines) + "\n"


def build_keepout_spec(pcb_path, keepouts):
    """Build named-region upserts; unique_id is stored as Region.Name."""
    lines = ["PCB|" + _pcb_path(pcb_path)]
    if not isinstance(keepouts, list) or not keepouts:
        raise ValueError("keepouts must be a nonempty list")
    seen = set()
    for item in keepouts:
        if not isinstance(item, dict) or item.get("kind") != "region":
            raise ValueError("only kind=region is supported; stable identity uses Region.Name")
        _unknown(item, {"unique_id", "kind", "layer", "restrictions", "locked", "vertices"})
        uid = item.get("unique_id", "")
        if not isinstance(uid, str) or not re.fullmatch(r"[A-Z0-9]{8}", uid):
            raise ValueError("unique_id must contain exactly eight uppercase letters or digits")
        if uid in seen:
            raise ValueError(f"duplicate keepout unique_id: {uid}")
        seen.add(uid)
        layer = _layer(item.get("layer"), keepout=True)
        restrictions = item.get("restrictions")
        if not isinstance(restrictions, list) or not restrictions:
            raise ValueError("restrictions must be a nonempty explicit list")
        if any(not isinstance(value, str) or value not in _RESTRICTIONS for value in restrictions):
            raise ValueError("restriction must be via, track, copper, smd_pad, or th_pad")
        mask = sum(_RESTRICTIONS[value] for value in set(restrictions))
        if mask != 31:
            raise ValueError("this tool supports all five restrictions together; selective keepouts are not supported")
        locked = _boolean(item.get("locked", True), "locked")
        points = item.get("vertices")
        if not isinstance(points, list) or len(points) < 3:
            raise ValueError("region needs at least three vertices")
        coords = []
        numeric = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError("each vertex must be [x_mm, y_mm]")
            coords.extend((_number(point[0], "vertex x"), _number(point[1], "vertex y")))
            numeric.append(tuple(point))
        if len(set(numeric)) != len(numeric):
            raise ValueError("vertices must be distinct; do not repeat the closing vertex")
        area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(numeric, numeric[1:] + numeric[:1]))
        if abs(area) < 1e-12:
            raise ValueError("region must have nonzero area")
        lines.append("|".join(["KEEPOUT", uid, "region", layer, str(mask), locked, *coords]))
    return "\n".join(lines) + "\n"


def build_process_recovery_spec(pcb_path, expected_depth, repair=False):
    """Request a depth report, or explicitly close one abandoned transaction."""
    path = _pcb_path(pcb_path)
    if not PureWindowsPath(path).is_absolute():
        raise ValueError("pcb_path must be an absolute Windows path")
    if type(expected_depth) is not int or expected_depth < 0:
        raise ValueError("expected_depth must be a nonnegative integer")
    repair_value = _boolean(repair, "repair")
    if repair and expected_depth != 1:
        raise ValueError("repair requires expected_depth exactly 1")
    return f"PCB|{path}\nPROCESS|{expected_depth}|{repair_value}\n"
