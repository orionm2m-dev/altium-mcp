# Explicit PCB routing batches

`set_pcb_routing(pcb_path, tracks=None, vias=None)` adds or deletes explicit
free copper tracks and standard through vias. It does not choose a route,
create nets, move components, change widths/rules automatically or rip up
unmentioned routing. Ground polygons, repour, differential-pair constraints,
blind/buried vias and custom via stacks are outside this tool's scope.

All geometry is in millimetres relative to the board origin. Nets are exact,
case-sensitive names that must exist exactly once in the board. Tracks require
an enabled signal layer (`top`, `bottom`, `mid1`..`mid30`). Vias require the
explicit layer pair `start_layer="top", end_layer="bottom"` and positive hole
and diameter with diameter larger than hole. This does not qualify the chosen
annular ring or via design rules.

```json
{
  "pcb_path": "C:\\Boards\\Controller.PcbDoc",
  "tracks": [
    {"operation":"add", "net":"SIGNAL", "layer":"bottom",
     "x1_mm":1, "y1_mm":2, "x2_mm":3, "y2_mm":4,
     "width_mm":0.25, "locked":true}
  ],
  "vias": [
    {"operation":"add", "net":"GND", "x_mm":3, "y_mm":4,
     "diameter_mm":0.6, "hole_mm":0.3,
     "start_layer":"top", "end_layer":"bottom", "locked":true}
  ]
}
```

`locked` defaults to true for additions. A deletion uses `operation="delete"`
and the complete same geometry/net fields, with `locked` omitted. A changed
route can delete its exact old geometry and add its new geometry. The same
geometry cannot be addressed twice within one batch, even for different nets
or actions. An absent deletion is an error; it is not silently accepted.

## Exact identity and ownership

The native writer converts coordinates/dimensions to integer Altium units and
uses the complete track geometry (layer, both endpoints, width) or via geometry
(position, diameter, hole, start/end layers) as its key. Track endpoint order is
canonicalized. It does not read or assign `UniqueId` for routing identity.
Native conversion can make two numerically different requests identical; that
case is checked again before the first mutation.

An existing key must match exactly one object on the requested net. An add with
one exact match and the same lock state reports `unchanged`; zero matches adds
an object. Duplicate matches, foreign nets, keepouts, component-owned objects,
polygon-owned objects and custom via stacks are refused. Existing objects are
not adopted by approximate distance, bounding box, net alone or object order.
Deletion removes only the exact matched free object through the PCB API; it
does not destroy objects referenced by native undo/connectivity structures.
Overlapping but non-identical geometry remains the responsibility of the route
planner and DRC, not the identity matcher.

## Execution and verification

The entire batch is checked for valid geometry, enabled layers, unique nets,
existing-object ownership and target ambiguity before `PCBServer.PreProcess`.
Both client and PCB process depths must be zero. Changes are grouped in one
transaction with `finally PostProcess`. The tool then rebuilds connectivity
using the existing `PCB:UpdateConnectivity` / `ConnectivelyValidateNets` path
and enumerates every requested object again. Geometry/net/ownership/lock state
must match; deletes must be absent. Saving happens only when all readbacks
pass. Returned `native_geometry_key` contains the actual integer geometry;
reported dimensions/coordinates are also read from the actual object.

A bounded, flushed `pcb_routing_spec.trace.txt` retains the last 64 native
phases alongside the exchange spec. A timeout or native interpreter failure
can leave partial changes in memory; `finally` is not a guarantee against an
interpreter pause. Inspect the last phase, process depth and actual objects
before retrying. There is no automatic rollback, restart or force recovery.

A successful save verifies the requested primitive operations, not electrical
quality or manufacturing readiness. Run the normal DRC tool with relevant
categories enabled and verify its stop limit was not reached. Check saved
tracks/vias, net connectivity, shorts/clearances, widths/annular rings, return
paths, critical loops and component preservation. A shortened ratsnest or
`get_net_connections` airline score does not prove that routing is complete.

## Qualification

Offline tests check record encoding, exact input constraints, duplicate and
reversed-track requests, unsupported vias, public dispatch rejection and native
guard/save structure. They do not compile DelphiScript or call Altium.

```powershell
python -B -m unittest discover -s server/tests -p test_pcb_routing.py -v
python -B -m unittest discover -s server/tests -p test_pcb_primitives.py -v
```

Qualify on a disposable board with existing nets: add a track and a through
via, save/reopen and inspect the native file, repeat the add (zero additions),
then precisely delete them and verify saved absence and unchanged components.
Exercise foreign-net/owner and duplicate rejection before using production
geometry. Unregistered factory property probes alone are not this qualification.

The public Python tool requires a supported MCP-server refresh before its first
use. Native routing functions live in the existing pcb_primitives.pas unit: an
already open Altium script project may retain its source membership even after
the MCP server restarts. Adding a separate unit can therefore leave its entry
point undeclared while existing units reload correctly. This layout avoids that
additional source-registration dependency. It does not restart Altium.

References:

- [Altium PCB design-object API](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-design-objects-interfaces-reference)
- [Altium Situs autorouter](https://www.altium.com/documentation/altium-designer/pcb/routing/situs-topological-autorouter): separate capability, not invoked by this tool.

## Script failure handling

The bridge watches for narrowly recognized compiler diagnostics and explicit
script-runtime errors from the configured Altium executable. It captures their
text and returns promptly instead of consuming the full response timeout.
Only revalidated error dialogs are closed. Save confirmations, busy-instance
messages, library errors and generic access violations are not answered.
Multiple visible instances disable the automatic Stop action because the
command-line Stop process has no PID selector.

A recognized failure dispatches the documented EditScript:Stop process once,
without a shell window. It never retries the failed operation. The response
keeps mutation_state unknown and executor_recovered false: inspect the native
trace/file and run the public recover_script_executor probe before deciding
whether a retry is safe. A queued Stop action alone is not proof of recovery.

Classifier, fail-fast response and hidden-process contracts have offline
tests. Their Windows dialog capture still needs native qualification.
Changes to this Python bridge require a supported MCP-server restart to take
effect; editing the file does not update an already running server.
