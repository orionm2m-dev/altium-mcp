# Native PCB selection: documented contract and installed metadata

Reviewed 2026-10-22. Metadata research was followed by the bounded native
selection qualification below; it did not invoke routing or save a document.

## What is established

`IPCB_Primitive.Selected` is the documented selection state; components inherit
it. Its setter is `SetState_Selected(Boolean)`. The documented purpose of
`GraphicallyInvalidate` is graphical refresh, not selection synchronization.
Neither a mandatory redraw nor separate selection of component pads is
established by this reference. See the [design-object API reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-design-objects-interfaces-reference).

The [system API reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-system-interfaces-reference)
provides independent board selection readback:

- `SelectecObjectCount` / `GetState_SelectecObjectCount`: number of selected objects.
- `SelectecObject[index]` / `GetState_SelectecObject(index)`: indexed selected primitive.

The spelling **Selectec** is intentional. Do not substitute a guessed
`SelectedObjectCount` or `SelectedObjects[index]` API. These references are
legacy SDK documentation. The indexed getters were subsequently exercised in
the exact AD26.9.1 observation below; this does not qualify other members.

The [Situs guide](https://www.altium.com/documentation/altium-designer/pcb/routing/situs-topological-autorouter)
defines the selected-component actions in terms of connections from/between
pads of selected components. Both use Main. It does not specify which internal
selection collection Situs consumes or require selecting child pads.

## Installed-interface evidence

Metadata-only inspection of AD26.9.1 found, in
`Altium.Edp.Interfaces.dll`, `RT_PCB.IPCB_Board`:

| Member | Metadata signature |
| --- | --- |
| `GetState_SelectecObjectCount()` | Int32 |
| `GetState_SelectecObject(Int32)` | IPCB_Primitive |
| `SelectedObjectsCount()` | Int32 |
| `SelectedObjects_BeginUpdate/Clear/EndUpdate()` | Void |
| `SelectedObjects_Add(IPCB_Primitive)` | Void |

`RT_PCB.IPCB_Component` exposes `GetState_Selected`, `SetState_Selected`, and
`GraphicallyInvalidate`. `Altium.SDK.Interfaces.dll` also contains the board
count and collection methods, with adapter/helper object signatures.

Inspected file SHA-256:

- EDP: `66a2c1da130dbb316f8e112b7a76b6774bbd438533d2d42ab5e8e79647fd1009`
- SDK: `2b6d82e0b1be92bf2454764072bc14e9f170d0b80343c0c9469da4ce97c1c397`

Metadata was read with `System.Reflection.Metadata.PEReader`; no assembly code
was executed. The undocumented collection mutation methods are **not qualified**
for use. Their names alone do not prove that the selection flag bypasses a
collection, nor establish batching, restore, or Situs semantics.

## Observed native qualification

Observation `df176e457ec549e6b3cf28ec3ebcdca2` was recorded on
2026-10-21 at 21:37:50 UTC (2026-10-22 local date), using the public
`run_altium_script` after automatic UI dismissal was removed in commit `ada3095`.
The source-file SHA-256 is
`cb5f13bd12d0f44ffa9b6db52a14f01140337206a6933d060f724038d7bdc80c`.
The dispatched declarations hash after standard newline normalization is
`0d3296245f01c233de76419f44a0c1c341afec33ddf34b648b5a1a4ae05bdc6e`, matching the public preflight receipt.
Private board/session receipts remain outside this generic reference package.

Independent receipt comparison established:

| Snapshot | Selected components among 77 | Indexed selected objects |
| --- | --- | --- |
| Before | None | One pre-existing non-component primitive |
| After component-flag setter | J1 and TP05 | Same primitive plus both components |
| Restored | None | Exact original primitive, identity and selected flag |

All 77 component identities/flags and the complete indexed selection were
restored exactly. The component setter synchronized with indexed board selection
in this observation without graphical refresh, child-pad selection or
`SelectedObjects_*` collection mutations. The existing non-component selection
was preserved: this was **not** a test starting from an empty indexed list.
The native result reported complete snapshots, selection agreement and restoration;
routing and document save were false. The public result reported no dialogs
closed. This verifies the executed selection/readback sequence, not which
internal selection source Situs consumes or that it will route a connection.

A separate later process check retained the same PID/executable/creation identity
and reported no modal dialog. Its host readiness was false because foreground
was unconfirmed; `native_idle_verified` was also false in that host report.
This later report does not establish a busy PCB or invalidate the exact native
board/idle guards during the completed qualification. Do not convert a foreground
readiness condition into a routing failure or claim continued idle state from it.

## Remaining boundary

The original suspicion that the component setter failed to reach indexed
selection is not supported by this observation. Do not add refresh calls or
undocumented collection mutations to remedy that unproven cause. Indexed getters
and exact restoration are qualified only for this observed sequence/runtime;
Situs consumption of selection, child-pad semantics, broader input sets and
`SelectedObjects_*` mutation contracts remain unqualified. Investigate routing
constraints independently instead of repeating this selection trial.
