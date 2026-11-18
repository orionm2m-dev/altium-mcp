# Native PCB workflow: verified lessons, 2026-11-11

These notes describe Altium Designer 26.9.1 behavior observed during native work on a real board, and its limits. They are not a manufacturing release or general proof that every native API works. Board files, receipts and checksums are kept outside the repository.

## Related tools

- Interrupted routing reconciliation and background executor recovery ([native-routing-reconciliation.md](native-routing-reconciliation.md)). Closure of an error dialog, recovery of script execution and recovery of an abandoned PCB transaction are separate checks.
- Native embedded component models, native 3D silk visibility, and the narrowly guarded error recovery handler.
- Native board-core opacity control, qualified at opacity 1.0.
- Native coordinate-to-micrometre reporting correction.
- Canonical PCB path resolution and refusal of duplicate loaded aliases ([PCB_PATH_IDENTITY.md](../../dev/PCB_PATH_IDENTITY.md)).

## Guards and recovery

Use native MCP/application APIs for ordinary work. Extend a missing native tool, validate it on a protected copy, then apply it. Windows UI menu operations are not a native design API, even when wrapped by MCP. The only UI exception is a verified, process-bound handler for a blocking service error: inspect exact PID/creation identity, dialog text/type/button and effects; verify closure and executor recovery separately. It does not cover routine design UI, unknown Save/Discard responses or unrelated windows.

Bind each write to a fresh exact process/document identity, expected current geometry and transaction depths. A saved-file SHA does not prove that an open document has no unsaved changes. Preserve unexpected live changes before deciding what to restore. Allow one writer; never alter the production Pascal module or shared Sandbox while a dispatch may still be executing.

A transport timeout does not cancel DelphiScript. A keepout request with a contour of almost a thousand vertices timed out in the client at 120 seconds while Altium kept computing; the operation finished roughly 9 minutes 38 seconds after contour factory entry. It was not killed, stopped or replayed. The unique late response, completed trace and saved contour independently confirmed success. Observe the exact pending operation before any recovery or new write; CPU activity alone does not prove completion.

A faster keepout parser must not rely on short-circuit evaluation in bounds checks such as `(j < Fields.Count) and (Fields[j] <> '')`. Qualify any such parser on a disposable copy and verify the resulting contour before installing it; do not install it as part of restoring a working PCB.

## Model placement and persistence

`LoadGenericModelFromFile` plus embedding/component ownership was qualified on copies before applying models to the board. Locale-sensitive `StrToFloat` caused a real pre-transaction EConvertError; use the qualified locale-safe conversion. Native `MoveToXY` positioned a model bounding corner; `MoveByXY` was qualified for translation preserving the STEP's original center. Validate bottom-side projection and Z placement, not just XY.

Altium may reorder embedded model storage after moving a component. Moving one capacitor by +0.15 mm in Y moved its model record to index 0 and shifted other stream numbers. Comparing `Models/N` by index falsely reported changed models. Match `Models/Data` identity/name to payload SHA256 and verify component ownership and placement independently. All payloads were preserved; all other native bounds stayed fixed and the moved model translated correctly (maximum discrepancy 3e-6 mm).

Do not mix native model bounding approximations with exact STEP bounds at an excessively tight tolerance. One model differed by about 0.0047 mm between those representations. Compare native before/after with native, STEP before/after with STEP, and state the tolerance.

Simplified engineering envelopes may stand in for most parts, and bare testpads need no invented bodies. Model presence is not proof of manufacturer geometry or complete mechanical qualification.

## Copper and topology verification

Inspect saved physical cutouts, tracks, arcs, vias, pads and actual poured copper. Display visibility or keepout outlines alone do not establish clearance. Contact areas on opposite board sides need separate checks. At core opacity 0.5 top tracks were visible through the PCB; native opacity 1.0 fixed that display ambiguity without modifying copper. Hiding silk does not fix silk DRC.

Offline topology based on clipped old pours is insufficient. After a native repour one iteration split the ground net into three islands and lost hundreds of formerly connected pad pairs; the next iteration still left two islands. Both intermediate results were kept as failed checkpoints. The final local repair combined a 0.15 mm component move, a local via/track adjustment and two ground vias with a real copper bridge; after an actual repour every baseline pad pair and every ground pad was connected. Verify each repair after an actual repour; an offline plan that looked correct is not evidence.

Preserve baseline connected pairs as a set (net, pad A, pad B); new intended connections may increase its size. Counts alone can hide loss elsewhere. Check geometric clearances and exact mechanical constraints separately from connectivity. Pad/body/courtyard constraints all matter, particularly around MEMS components.

Native coordinate unit is 2.54e-6 mm. A board origin need not be a round number of millimetres (one observed origin was 100.00000066 mm). Placement and routing tools may accept different units (placement mils, routing/keepouts mm relative to origin); read each current schema and verify a native readback. Never assume absolute model coordinates and board-relative editor coordinates are interchangeable.

## DRC and remaining qualification

A fresh, completed native report whose findings the collector returned in full, none waived, still does not establish full checking coverage. No enabled-rule/stop-limit qualification was completed: `full_drc_verified=false`, `native_limit_verified=false`, `native_check_complete=null`. Collector completeness and report freshness do not prove full native checking coverage.

`saved_drc_options.py` can read saved settings, but is not proof of the live DRC configuration. Qualify exact enabled batch/online checks and stop-after-N through the supported native interface before claiming a full check. Do not waive or weaken rules merely to report green.

A native 3D view rebuild/save changed the same 24 bytes within the 65..109 block of every `Texts6` record, while text strings and other bytes remained unchanged. The original strict byte audit remains a historical FAIL; the serialization meaning and manufacturing impact are still unqualified. Keep that limitation in any qualification record.

## Prototype Situs modules

These modules are development prototypes. They are not wired into public MCP handlers, and keeping their source is not approval to execute every capability it contains:

- `server/situs_lifecycle.py`: process/document-bound jobs, lifecycle and receipt handling. Callback tests are mocked; native qualification remains open.
- `server/situs_native.py`: guarded generated DelphiScript for direct autorouting scopes. Generated source is not proof of execution in Altium. It is separate from the production native Situs service.
- `server/routing_rules.py`, the routing contract and enum catalog: batch validation and rule specifications. The candidate Pascal implementation stays isolated at `dev/probes/routing_rules/pcb_document.candidate.pas`; it is not the production `pcb_document.pas`.
- `server/saved_drc_options.py`: reads saved PCB DRC settings. Saved flags and limits do not prove live enabled coverage or a complete native DRC run.
- `dev/probes/open_documents`, `routing_rules`, `situs_native`: templates, static fixtures and qualification receipts. The open-document probe is PREPARED_NOT_DISPATCHED. Metadata and lint success do not prove DelphiScript interface binding.
- `server/situs_route_menu.py` and the related part of `situs_uia_worker.ps1`: a Windows UI Automation prototype for Route/Auto Route menu inspection/actions. This is UI control, not a native Altium design API. It must not be used for ordinary work or wired as an automatic fallback, and the blocking-error exception above does not cover this routing menu.

Offline unit tests cover these modules, and a PowerShell parse check of `situs_uia_worker.ps1` passes without executing the worker. Neither certifies native routing, rule binding or UI compliance. The JSON enum catalog, probe manifest and qualification receipts are tracked explicitly because the repository-wide `*.json` ignore would otherwise omit files the tests need. Keep native integration qualification separate from board design work.
