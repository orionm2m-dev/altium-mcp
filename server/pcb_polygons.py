"""Side-effect-free contract for named copper polygons and polygon-only cutouts."""
from pathlib import PureWindowsPath
from pcb_primitives import _text, _pcb_path, _number, _boolean, _unknown

_COORDS_PER_MM = 10000 / 0.0254
_ACTIONS = {"create", "update", "upsert", "delete", "repour"}


def _orientation(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def _on_segment(a, b, c):
    return (_orientation(a,b,c) == 0 and min(a[0],b[0]) <= c[0] <= max(a[0],b[0])
            and min(a[1],b[1]) <= c[1] <= max(a[1],b[1]))


def _intersects(a, b, c, d):
    o1,o2,o3,o4 = _orientation(a,b,c),_orientation(a,b,d),_orientation(c,d,a),_orientation(c,d,b)
    return ((o1*o2 < 0 and o3*o4 < 0) or _on_segment(a,b,c) or _on_segment(a,b,d)
            or _on_segment(c,d,a) or _on_segment(c,d,b))


def _vertices(value):
    if not isinstance(value, list) or not 3 <= len(value) <= 4096:
        raise ValueError("vertices must contain 3..4096 [x_mm,y_mm] pairs without a closing duplicate")
    points, fields = [], []
    for vertex in value:
        if not isinstance(vertex, (list,tuple)) or len(vertex) != 2:
            raise ValueError("each vertex must be exactly [x_mm,y_mm]")
        fields.extend(_number(v,"vertex") for v in vertex)
        if any(abs(v)>2000 for v in vertex):
            raise ValueError("coordinates beyond +/-2000 mm are unsupported")
        # Reject contours that collapse after conversion to the native grid.
        points.append(tuple(round(v*_COORDS_PER_MM) for v in vertex))
    if len(set(points)) != len(points):
        raise ValueError("duplicate vertices on native grid, including a closing duplicate")
    count=len(points)
    area=sum(points[i][0]*points[(i+1)%count][1]-points[(i+1)%count][0]*points[i][1] for i in range(count))
    if area == 0:
        raise ValueError("polygon area must not be zero")
    for i in range(count):
        a,b,c=points[i-1],points[i],points[(i+1)%count]
        if _orientation(a,b,c) == 0 and ((a[0]-b[0])*(c[0]-b[0])+(a[1]-b[1])*(c[1]-b[1]))>0:
            raise ValueError("adjacent edges overlap or backtrack")
        for j in range(i+1,count):
            if j == i+1 or (i == 0 and j == count-1):
                continue
            if _intersects(points[i],points[(i+1)%count],points[j],points[(j+1)%count]):
                raise ValueError("polygon must be simple: crossing, touching or overlapping edges")
    return fields


def build_polygon_spec(pcb_path, polygons=None, cutouts=None, dry_run=False):
    """Full batch validation precedes file/native access. Empty dry run inventories."""
    path=_pcb_path(pcb_path)
    if not PureWindowsPath(path).is_absolute():
        raise ValueError("pcb_path must be an absolute Windows path")
    dry=_boolean(dry_run,"dry_run")
    polygons=[] if polygons is None else polygons
    cutouts=[] if cutouts is None else cutouts
    if not isinstance(polygons,list) or not isinstance(cutouts,list):
        raise ValueError("polygons and cutouts must be lists")
    if not (polygons or cutouts) and not dry_run:
        raise ValueError("empty mutations are refused; use dry_run=true for inventory")
    lines=["PCB|"+path+"|"+dry]
    seen=set()
    # Cutouts precede pours in the native transaction regardless of caller order.
    for kind,entries in (("CUTOUT",cutouts),("POLYGON",polygons)):
        for item in entries:
            common={"operation","name","layer","vertices","locked"}
            extra={"net","remove_dead","neck_width_mm","arc_tolerance_mm","allow_empty"} if kind=="POLYGON" else set()
            _unknown(item,common|extra)
            op=item.get("operation")
            if op not in _ACTIONS or (kind=="CUTOUT" and op=="repour"):
                raise ValueError("operation must be create/update/upsert/delete; polygons also support repour")
            name=_text(item.get("name"),"name")
            if not name.strip() or len(name)>128:
                raise ValueError("name must have 1..128 nonblank characters")
            if name.casefold() in seen:
                raise ValueError("duplicate target name, including polygon/cutout collisions")
            seen.add(name.casefold())
            layer=item.get("layer")
            if layer not in {"top","bottom"}:
                raise ValueError("layer must be top or bottom")
            net=_text(item.get("net"),"net") if kind=="POLYGON" else ""
            if kind=="POLYGON" and not net.strip():
                raise ValueError("net must name an existing nonempty board net")
            if op in {"delete","repour"}:
                allowed={"operation","name","layer","net"} if kind=="POLYGON" else {"operation","name","layer"}
                if kind=="POLYGON" and op=="repour": allowed.add("allow_empty")
                _unknown(item,allowed)
                fields=[]
                locked,dead,neck,arc="1","1","0.15","0.01"
            else:
                fields=_vertices(item.get("vertices"))
                locked=_boolean(item.get("locked",True),"locked")
                dead=_boolean(item.get("remove_dead",True),"remove_dead") if kind=="POLYGON" else "1"
                neck=_number(item.get("neck_width_mm",.15),"neck_width_mm",0,strict=True)
                arc=_number(item.get("arc_tolerance_mm",.01),"arc_tolerance_mm",0,strict=True)
                if float(neck)>10 or float(arc)>1:
                    raise ValueError("neck_width_mm <= 10 and arc_tolerance_mm <= 1 required")
            allow=_boolean(item.get("allow_empty",False),"allow_empty")
            lines.append("|".join([kind,op,name,net,layer,locked,dead,neck,arc,allow,str(len(fields)//2),*fields]))
    return "\n".join(lines)+"\n"
