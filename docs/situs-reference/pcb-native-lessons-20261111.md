# Native PCB workflow: verified lessons from D44 ECAD, 2026-11-11

These notes describe observed AD26.9.1 Trial behavior and its limits. They are not a manufacturing release or general proof that every native API works. Project evidence lives under `Info/Research/PCB_D44_ECAD_20261110/` in VYBRO. Start with `ECAD_KNOWLEDGE_20261111.md` there.

## Existing implementation commits

- `5c39bb4`: interrupted routing reconciliation and background executor recovery. Closure of an error dialog, recovery of script execution and recovery of an abandoned PCB transaction are separate checks.
- `2764b26`: native embedded component models, native 3D silk visibility, and the narrowly guarded error recovery handler.
- `ef0fefb`: native board-core opacity control, qualified at opacity 1.0.
- `783a681`: native coordinate-to-micrometre reporting correction.
- `dbb0a32`: canonical PCB path resolution and refusal of duplicate loaded aliases.

The final project PCB SHA256 is `f0034268cf8bbfa1d3afa0adf52a0ad33565e3d21511455a82fcca655a80af28`; `ECAD_RECEIPT_11_SPRING_TOP_PROTECTION_20261111.json` records the native result. Saving these notes did not call Altium or mutate the PCB.

## Guards and recovery

Use native MCP/application APIs for ordinary work. Extend a missing native tool, validate it on a protected copy, then apply it. Windows UI menu operations are not a native design API, even when wrapped by MCP. The owner's emergency exception permits only a verified, process-bound blocking service error handler: inspect exact PID/creation identity, dialog text/type/button and effects; verify closure and executor recovery separately. It does not authorize routine design UI, unknown Save/Discard responses or unrelated windows. REA is restricted to SOLIDWORKS Plastics and must not be used here.

Bind each write to a fresh exact process/document identity, expected current geometry and transaction depths. A saved-file SHA does not prove that an open document has no unsaved changes. Preserve unexpected live changes before deciding what to restore. Allow one writer; never alter the production Pascal module or shared Sandbox while a dispatch may still be executing.

A transport timeout does not cancel DelphiScript. The 972-vertex spring keepout request timed out in the client at 120 seconds, while Altium kept computing; the operation finished roughly 9 minutes 38 seconds after contour factory entry. We did not kill, Stop or replay it. The unique late response, completed trace and saved contour independently confirmed success. Evidence: `routing/spring_top_42/keepout_late_native_response.json`, `late_keepout_saved_verified.json` and native receipts. Observe the exact pending operation before any recovery or new write; CPU activity alone does not prove completion.

The fast keepout parser is only a project draft at `routing/spring_top_42/integration/pcb_primitives_fast_keepout_draft.pas`. It is not installed or qualified. Before adopting it, remove reliance on short-circuit evaluation in bounds checks such as `(j < Fields.Count) and (Fields[j] <> '')`, then qualify on a disposable copy and verify the resulting contour. Do not silently install it as part of restoring the working PCB.

## Model placement and persistence

`LoadGenericModelFromFile` plus embedding/component ownership was qualified on copies before applying 35 models. Locale-sensitive `StrToFloat` caused a real pre-transaction EConvertError; use the qualified locale-safe conversion. Native `MoveToXY` positioned a model bounding corner; `MoveByXY` was qualified for translation preserving the STEP's original center. Validate bottom-side projection and Z placement, not just XY.

Altium may reorder embedded model storage after moving a component. In this case moving C62 by +0.15 mm Y moved its model record to index 0 and shifted other stream numbers. Comparing `Models/N` by index falsely reported changed models. Match `Models/Data` identity/name to payload SHA256 and verify component ownership and placement independently. All 35 payloads were preserved; 34 native bounds stayed fixed and C62 translated correctly (maximum discrepancy 3e-6 mm).

Do not mix native model bounding approximations with exact STEP bounds at an excessively tight tolerance. The spring differed by about 0.0047 mm between those representations. Compare native before/after with native, STEP before/after with STEP, and state the tolerance.

There are 35 physical models (33 engineering envelopes, a manufacturer connector and a project spring) and 42 bare testpads with no invented bodies. Model presence is not proof of manufacturer geometry or complete mechanical qualification.

## Copper and topology verification

Inspect saved physical cutouts, tracks, arcs, vias, pads and actual poured copper. Display visibility or keepout outlines alone do not establish clearance. The bottom support and top spring are different contact areas on opposite sides. At core opacity 0.5 top tracks were visible through the PCB; native opacity 1.0 fixed that display ambiguity without modifying copper. Hiding silk does not fix silk DRC.

Offline topology based on clipped old pours is insufficient. After native repour the spring iteration 42 split GND into groups of 39/14/2 pads, losing 652 formerly connected pairs; iteration 43 still had 53/2 and 106 missing pairs. Both intermediate results were retained as failed checkpoints. The final local repair used C62 +0.15 mm Y, a local DRDY via/track adjustment and two GND vias with a real copper bridge. After actual repour all 1965 baseline pad pairs and all 55 GND pads were connected. Do not replay earlier plans or failed offline alternatives.

Preserve baseline connected pairs as a set (net, pad A, pad B); new intended connections may increase its size. Counts alone can hide loss elsewhere. Check geometric clearances and exact mechanical constraints separately from connectivity. Pad/body/courtyard constraints all matter, particularly around MEMS components.

Native coordinate unit is 2.54e-6 mm. This board's observed origin was 100.00000066 mm. Placement and routing tools may accept different units (placement mils, routing/keepouts mm relative to origin); read each current schema and verify a native readback. Never assume absolute model coordinates and board-relative editor coordinates are interchangeable.

## DRC and remaining qualification

The final native report `4dfa532cb24f427faca11cb82ea62002` is fresh and completed; the collector returned all 447 listed findings, none waived. Counts: 20 UnRoutedNet, 3 NetAntennae, 33 text-related BoardOutlineClearance, 185 SilkToSilkClearance, 159 SilkToSolderMaskClearance, 47 MinimumSolderMaskSliver. No enabled-rule/stop-limit qualification was completed: `full_drc_verified=false`, `native_limit_verified=false`, `native_check_complete=null`. Collector completeness and report freshness do not prove full native checking coverage.

The preserved `saved_drc_options.py` can read saved settings, but is not proof of the live DRC configuration. Qualify exact enabled batch/online checks and stop-after-N through the supported native interface before claiming a full check. Do not waive or weaken rules merely to report green.

A native 3D view rebuild/save changed the same 24 bytes within the 65..109 block of 154 `Texts6` records, while text strings and other bytes remained unchanged. The original strict byte audit remains a historical FAIL; the serialization meaning and manufacturing impact are still unqualified. Do not erase that limitation from the handoff.

## Preserved Situs work

See [worktree-preservation-20261111.md](worktree-preservation-20261111.md) for the separate preservation commit. Its 61 offline tests and PowerShell parse passed; this does not certify native routing, rule binding or UI compliance. The menu prototype is not an allowed ordinary-work fallback. Keep native integration qualification separate from completing the D44 electrical work.
