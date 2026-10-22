# Named PCB polygons and polygon-only cutouts

`set_pcb_polygons(pcb_path, polygons=None, cutouts=None, dry_run=False)` is a
public explicit geometry writer. It supports solid signal-layer copper on the
outer layers only. It does not infer ground topology, change clearance or pad
connection rules, move parts, add nets, modify board shape, or route signals.
New Python tools require a supported MCP server restart before use. Native
functions remain in the already loaded `pcb_primitives.pas` source unit.

## Request

All dimensions are millimetres from the PCB origin. A polygon requires exact
case-sensitive `name`, an existing `net`, and `layer=top|bottom`.
`operation=create|update|upsert` requires `vertices=[[x,y],...]`, a simple outer
contour of 3..4096 straight vertices, without a repeated closing point. Concave
contours are supported; intersecting, touching, overlapping, zero-area and
native-grid-collapsed contours are rejected. Holes and arcs are not accepted.

Optional polygon creation/update settings:

- `locked=true` by default.
- `remove_dead=true` removes copper disconnected from the selected net.
- `neck_width_mm=0.15`: narrow-neck removal is enabled at this positive width.
- `arc_tolerance_mm=0.01`: positive arc approximation tolerance for the pour.
- `allow_empty=false`: an empty generated copper result prevents saving.

Solid fill pours over all same-net objects. Area-based island removal is off,
so a connected small island is not silently removed by a size threshold.
`remove_dead` is a distinct connectivity setting, not the same as area removal.
The existing polygon connection and electrical-clearance rules still apply;
no claimed reliability or manufacturing class follows from these defaults.

`operation=repour` requires only name/net/layer and optional `allow_empty`.
It preserves the current definition and verifies that it survived the pour.
`operation=delete` requires name/net/layer only. It must match exactly one
existing free polygon; an absent deletion is an error.

A cutout has `operation=create|update|upsert|delete`, name and top/bottom layer.
Create/update/upsert requires the same `vertices` format, optional `locked`.
It has no net and is an `eRegionKind_Cutout` region with `IsKeepout=false`.
This excludes polygon copper while allowing intentional pads, tracks and vias
through the area. It is not a milled board opening or an all-object keepout.

```json
{
  "pcb_path":"C:\\Boards\\Controller.PcbDoc",
  "polygons":[{
    "operation":"upsert", "name":"GroundTop", "net":"GND", "layer":"top",
    "vertices":[[-10,-10],[10,-10],[10,10],[-10,10]],
    "neck_width_mm":0.15, "remove_dead":true
  }],
  "cutouts":[{
    "operation":"upsert", "name":"SensitiveAreaTop", "layer":"top",
    "vertices":[[2,2],[4,2],[4,4],[2,4]]
  }]
}
```

This is a syntax example, not geometry intended for an existing design.

## Identity, preflight and side effects

Names are shared across polygon and region targets. Whole-batch preflight
rejects duplicates, ambiguous native matches, different case spellings,
foreign net/layer/type, component-owned objects, polygon-owned children,
keepouts, split-plane polygons and unsupported existing contour geometry.
Existing net and layer are immutable under this tool; change them only through
an explicit delete followed by a separately reviewed creation. `create` must
be absent, `update` must exist, `upsert` supports either. Repeated identical
upserts report unchanged definitions and still explicitly repour the polygon.

Every existing polygon on a changed layer must be explicitly included in the
batch, at least as `repour`, or preflight fails. This intentionally conservative
condition avoids silently leaving a stale unmentioned pour when a cutout or
polygon changes. Cutouts are processed before polygon creation. Explicit pour
order follows the supplied polygon list, while native priorities stay intact.

`dry_run=true` performs full preflight and returns actual polygon/cutout
inventory without mutation, repour or save. An empty dry run is inventory only.
Mutation mode rejects an empty batch. Both process depths must be idle.

The writer groups definition changes in a balanced PreProcess/PostProcess
block, restores the user's PolygonRepour preference in a `finally`, then
rebuilds connectivity. Exact integer contour/options, identity/net/ownership,
lock state and deletion absence are read back before saving. Positive-area
generated copper regions must exist unless explicitly allowed empty. Their
existence proves neither complete fill nor connectivity: run native DRC and
inspect saved copper contours, thermal connections and voids independently.

The trace file is flushed and bounded using the primitive-tool mechanism.
A runtime exception or interpreter pause may leave unsaved partial changes.
No automatic rollback or retry occurs. Inspect the trace, process state and
actual board before recovery. A successful transaction is not a DRC pass.

## Native qualification gate

Offline tests validate request constraints, adverse contours, collisions,
no-dispatch rejection and critical transaction/save structure. They do not
compile or execute DelphiScript. Factory-only property probes are separate
from registered board mutation qualification. `dev/polygon_api_probe.json`
contains an unregistered object probe for the public `run_altium_script` tool;
it never calls AddPCBObject or accesses a production board.

Before use on a production PCB, through the public tools only:

1. Copy a saved board with known existing nets into a disposable qualification
   PCB, inventory it, and snapshot components/pads/nets/outline/rules/keepouts.
2. Dry-run a polygon around a known same-net pad or through via and a small
   cutout that partially intersects the prospective copper. Verify no change.
3. Create and pour. Read back the polygon and cutout; inspect the saved native
   definition and poured regions, including the actual cutout hole. Run DRC
   against another-net pad inside the polygon to exercise clearance exclusion.
4. Save/reopen and repeat the same upsert: added=updated=0 and unchanged=2,
   repoured=1. Existing definitions and unrelated board data must match.
5. Reject a foreign-net name collision, polygon/cutout name collision and an
   omitted affected polygon; verify no new objects or board changes.
6. Update the cutout and explicitly repour. Confirm copper is removed in its
   new area, while an intentional track inside the cutout is preserved.
7. Delete cutout and polygon explicitly; save/reopen, verify absence, no orphan
   poured regions, original unrelated design data and restored preferences.
8. Inspect real clearance/connectivity and routed return paths after applying
   production pours. A generated-region count alone is not electrical proof.

```powershell
python -B -m unittest discover -s server/tests -p test_pcb_polygons.py -v
python -B -m unittest discover -s server/tests -p test_pcb_routing.py -v
python -B -m unittest discover -s server/tests -p test_pcb_primitives.py -v
```

## API evidence and limitations

The [official PCB design-object API](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-design-objects-interfaces-reference)
documents IPCB_Polygon Rebuild, segment/solid/neck/dead-copper controls and
IPCB_Region Kind/Name/contour APIs. Its legacy PourOver property description
conflicts with its getter/setter type; the enum form is used and must be
qualified on the installed version.

The primary [Altium iterator example](https://github.com/Altium-Designer-addons/scripting-reference/blob/master/Delphiscript%20Scripts/PCB/PCB%20Iterators/IteratePolygons.pas)
identifies `ePolygonPourOver_SameNet`. The primary
[MillExporter example](https://github.com/Altium-Designer-addons/scripting-reference/blob/master/Delphiscript%20Scripts/PCB/Mill%20Exporter/MillExporter.pas)
uses polygon Name, explicit Rebuild and temporary PolygonRepour preference.
The [PCBObjectInspector implementation](https://github.com/Altium-Designer-addons/scripts-libraries/blob/master/Scripts%20-%20PCB/PCBObjectInspector/PCBObjectInspector.pas)
lists the real region enum `eRegionKind_Cutout`; the guessed identifier
`eRegionKind_PolygonCutout` is not used. These examples identify the interface;
they do not replace local mutation/pour/save qualification.

`set_pcb_rules` now has a narrow `PolygonConnect` ConnectStyle setter (offline validated; native qualification pending on 2026-10-22). See `docs/situs-reference/polygon-connect-style-contract.md`. Thermal dimension setters are still unavailable.
Polygon creation and rule mutation remain separate tools. Qualify the new scoped
style setter before use; its offline success does not replace native proof of
effective via/pad connection after repour. Polygon cutouts represent physical
copper exclusions, not an alternative to missing connection-rule controls.
