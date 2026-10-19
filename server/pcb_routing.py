"""Strict record validation for explicit PCB routing primitives; no native access."""

from pathlib import PureWindowsPath
from pcb_primitives import _text, _pcb_path, _number, _boolean, _unknown


_SIGNAL_LAYERS = {"top", "bottom", *(f"mid{i}" for i in range(1, 31))}


def build_routing_spec(pcb_path, tracks=None, vias=None):
    """Encode explicit add/delete requests; native code matches integer geometry."""
    path = _pcb_path(pcb_path)
    if not PureWindowsPath(path).is_absolute():
        raise ValueError("pcb_path must be an absolute Windows path")
    tracks = [] if tracks is None else tracks
    vias = [] if vias is None else vias
    if not isinstance(tracks, list) or not isinstance(vias, list) or not (tracks or vias):
        raise ValueError("supply a nonempty tracks or vias list")
    lines = ["PCB|" + path]
    seen = set()
    for kind, entries in (("TRACK", tracks), ("VIA", vias)):
        for item in entries:
            common = {"operation", "net", "locked"}
            geom = ({"layer", "x1_mm", "y1_mm", "x2_mm", "y2_mm", "width_mm"}
                    if kind == "TRACK" else
                    {"start_layer", "end_layer", "x_mm", "y_mm", "diameter_mm", "hole_mm"})
            _unknown(item, common | geom)
            required = {"operation", "net"} | geom
            if not required.issubset(item):
                raise ValueError("missing routing fields: " + ", ".join(sorted(required - set(item))))
            operation = item["operation"]
            if operation not in {"add", "delete"}:
                raise ValueError("operation must be add or delete")
            net = _text(item["net"], "net")
            if not net.strip():
                raise ValueError("net must name an existing nonempty board net")
            if operation == "delete" and "locked" in item:
                raise ValueError("locked applies only to add; delete identifies exact geometry and net")
            locked = _boolean(item.get("locked", True), "locked")
            if kind == "TRACK":
                layer = item["layer"]
                if not isinstance(layer, str) or layer not in _SIGNAL_LAYERS:
                    raise ValueError("track layer must be top, bottom or mid1..mid30")
                coords = [_number(item[k], k) for k in ("x1_mm", "y1_mm", "x2_mm", "y2_mm")]
                width = _number(item["width_mm"], "width_mm", 0, strict=True)
                ends = sorted(((item["x1_mm"], item["y1_mm"]), (item["x2_mm"], item["y2_mm"])))
                if ends[0] == ends[1]:
                    raise ValueError("track endpoints must differ")
                key = (kind, layer, tuple(ends), item["width_mm"])
                fields = [kind, operation, net, layer, *coords, width, locked]
            else:
                if item["start_layer"] != "top" or item["end_layer"] != "bottom":
                    raise ValueError("only standard through vias top to bottom are supported")
                coords = [_number(item[k], k) for k in ("x_mm", "y_mm")]
                diameter = _number(item["diameter_mm"], "diameter_mm", 0, strict=True)
                hole = _number(item["hole_mm"], "hole_mm", 0, strict=True)
                if item["diameter_mm"] <= item["hole_mm"]:
                    raise ValueError("via diameter must be larger than its hole")
                key = (kind, item["x_mm"], item["y_mm"], item["diameter_mm"], item["hole_mm"], "top", "bottom")
                fields = [kind, operation, net, "top", "bottom", *coords, diameter, hole, locked]
            if key in seen:
                raise ValueError("duplicate routing geometry in batch, including reversed tracks or conflicting nets/actions")
            seen.add(key)
            lines.append("|".join(fields))
    return "\n".join(lines) + "\n"
