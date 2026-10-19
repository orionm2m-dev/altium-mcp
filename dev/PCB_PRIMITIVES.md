# Native PCB pad and keepout tools

`set_pcb_pads` adds free board pads or updates a pad identified by its
component designator and pad name. Names are matched case-insensitively and
must resolve exactly once for updates, or be absent for additions. All target
lookups happen before the first change. Coordinates use millimetres relative
to the board origin. An update to `hole_mm` alone preserves copper geometry,
position, rotation, net and component ownership. Supplying both copper sizes
explicitly requests a simple pad stack, replacing a full/top-middle-bottom
stack. These are board-instance edits; library synchronization may replace them.

A non-plated mechanical hole is a multilayer pad with `plated=false`, a
positive hole size, and copper dimensions no larger than the hole. Zero
copper dimensions are accepted by validation and sent unchanged to Altium;
inspect the actual dimensions returned by Altium. Do not infer absence of
copper from the plating flag alone. The tool does not change a pad net.

`set_pcb_keepouts` creates or updates free, straight-edge **regions only**.
The existing `unique_id` argument remains an explicit eight-character uppercase
alphanumeric key, but is stored in `IPCB_Region.Name`. It is not a claim that
`IPCB_Primitive.UniqueId` was set. A disposable Altium Designer 26 arc probe
accepted a `UniqueId` assignment but immediately read back an empty string;
layer/keepout readback alone therefore cannot prove stable identity. Track,
arc and fill inputs are rejected before Python dispatch and again in Pascal
before a board is opened, including requests from an already running older
Python server. Their native creation paths have been removed. Curves need an
explicitly reviewed polygon approximation; the tool does not approximate them.

A matching name must identify exactly one free copper-kind keepout region.
Duplicate names, component/polygon ownership, non-keepouts, other region kinds,
and collisions with another primitive's native unique ID are refused before
mutation. Existing anonymous objects are not adopted by geometry or deleted.
No layer is cleared. `top`, `bottom`, `mid1` through `mid30`, and `all` are
explicit choices. A copper layer must exist in the current board stack. This
does not create component-height restrictions or service reservations.

The tool uses the legacy `IPCB_Primitive.IsKeepout` scripting property for
all-object keepouts. The caller must explicitly request all five object
classes (`via`, `track`, `copper`, `smd_pad`, `th_pad`); selective requests fail
before dispatch. Although the installed API assemblies include a separate
`IPCB_KeepoutRestrictions` interface, its DelphiScript declaration failed in
the tested installation. It is deliberately not used by the production unit.
A scratch `IPCB_Region` created without registering it on a board successfully
round-tripped `IsKeepout=true` in Altium Designer 26. This is API evidence, not
an end-to-end validation of the tools.

After adding/registering or modifying a region, the tool reads `Region.Name`
back and compares it exactly with the requested key. A mismatch prevents the
save and returns failure; it can still leave changes in memory for review.
Reports identify the key as `identity_kind="region_name"`, include
`requested_id`, `actual_id`, `identity_verified`, and expose the actual name
in the compatibility `unique_id` result field. Actual/requested vertex counts,
layer, lock state and `IsKeepout` are also read/reported. Vertex counts are not
full coordinate verification. Saving requires every identity, layer and flag
check to pass. Read the saved native file or close/reopen a disposable board
to qualify persistence and repeat-upsert behavior in the target installation;
in-memory name readback alone is not end-to-end qualification.

`keepout_flag_verified` does not claim a selective restriction mask was
inspected. Check saved native restrictions, the effective Clearance rule, and
PCB DRC. A mechanical NPTH inside an all-object keepout can itself produce a
TH-pad violation; provide a geometric opening or an appropriately scoped rule
rather than suppressing unrelated pad-to-metal clearances. Clearance is
additional to keepout geometry, so a geometric safety expansion and a rule
expansion must not be counted twice.

Both tools group changes in a PCB transaction, refresh the view and save the
requested board. The report includes the actual save result. Preflight errors
make no changes. A native API exception during mutation can leave unsaved
changes; do not retry blindly after a timeout or exception. No automatic
rollback, restart, file-format patching or project-specific fallback is used.

## Process-depth guards and explicit recovery

Before `PCBServer.PreProcess`, both mutators read the client process depth and
`Client.ServerModuleByName('PCB').ProcessControl.ProcessDepth`. They refuse
nonzero depths. Their existing `try/finally PostProcess` remains in place, but
an interpreter pause or native runtime failure may prevent `finally` from
executing. These guards prevent stacking another transaction on an abandoned
one; they do not establish or eliminate the cause of a keepout API failure.

`recover_pcb_process(pcb_path, expected_depth, repair=False)` reports depths
without opening, focusing, editing or saving documents. The requested file
must exist, be an absolute canonical PcbDoc path, and match both the currently
focused document and current PCB board (case-insensitive). Client depth must
be zero; actual PCB depth must match `expected_depth`. A mismatch is reported
as a failure with the observed before/after depths.

Only after identifying an abandoned transaction and cancelling any interactive
command may the caller explicitly request `repair=True`. Both the expected
and actual PCB depths must then be exactly one. The tool calls
`PCBServer.PostProcess` **once**, re-reads both depths and reports whether the
call started/completed. It never changes a depth counter directly or loops,
and does not save or discard existing unsaved geometry. This finalizes the
transaction/undo environment; it is not an undo, rollback or geometry repair.
Review existing in-memory edits and save separately through the normal tool.
Do not use recovery to complete an intentionally active edit or infer that
unsaved changes are correct merely because the depth returned to zero.

Keepout operations keep at most 64 flushed phase entries in
`pcb_keepouts_spec.trace.txt` alongside the exchange specification. The file
is reset to `dispatch_pending` before each request; `trace_path` and
`last_phase` are added to returned results, including bridge timeout reports.
A phase identifies the next native operation attempted, not proof that it
completed. Trace writes are best effort and never trigger recovery or block
transaction finalization. A stopped interpreter can still require cancellation;
inspect the trace and current state rather than replaying a timed-out edit.
The trace does not prove absence of partial unsaved changes.

Offline checks cover strict repair input validation, default report mode,
public dispatch behavior and native safety contract structure. They do not
compile DelphiScript or qualify native recovery; use a disposable board and
explicit review before live recovery in a target installation.

The script project contains `pcb_primitives.pas`; the existing handler reads
this source on dispatch. Region input syntax and the public tool signature
are unchanged. An already running Python server still has its older validator
and tool description until that server/client session is restarted or reloaded
through a supported mechanism. The Pascal region-only guard also protects
that interval. This patch does not restart either the client or Altium. Use
the public tools for live tests on a disposable board first; do not call server
functions from development scripts to modify an application project.

Offline checks (do not import the server or start Altium):

```powershell
python -B -m unittest discover -s server/tests -p test_pcb_primitives.py -v
python -B -m unittest discover -s server/tests -p test_step_export.py -v
```

Offline tests exercise validation and record encoding, including explicit
side selection, named-region encoding, unsupported-kind rejection before
dispatch, zero-copper NPTH input, hole-only updates, invalid vertices, duplicate
identities and record injection. Native-source checks cover the identity/save
guards and refusal of foreign objects. They do not compile DelphiScript or
claim live geometry, name persistence or manufacturing validation.

API context:

- [PCB API design objects](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-design-objects-interfaces-reference)
- [Object-specific keepouts](https://www.altium.com/documentation/altium-designer/pcb/object-specific-keepouts)
