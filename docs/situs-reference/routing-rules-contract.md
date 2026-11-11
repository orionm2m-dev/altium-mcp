# Routing-rule backend contract

Status: isolated implementation candidate, offline tests only; new accessors are not native-qualified. The committed production `server/AltiumScript/pcb_document.pas` is restored unchanged. The complete Pascal candidate lives in `dev/probes/routing_rules/pcb_document.candidate.pas` and must not be added to the production script project before isolated qualification.
Reviewed on 2026-10-21. This document does not assert complete Situs support.

`server/routing_rules.py` builds an entire spec before dispatch. `build_rule_spec(pcb_path, rules)` returns the existing pipe format. The public handler must use it, write with strict encoding, and dispatch only after validation succeeds. Calling the old permissive serializer does not provide these checks.

The isolated candidate function `GetPcbRoutingRulesReport(PcbPath, Extended, KindFilter)` reads the exact loaded/current PCB without opening, focusing, saving, or changing it. `KindFilter` defaults to empty (all rules) and supports isolated qualification of one kind. Coordinates include both native integers and millimetres. Metadata includes name, actual kind, priority, enabled flag, scopes and comment. Per-rule enabled flags are not the DRC batch/online rule-kind selection.

## Supported value input

All dimensional numbers are millimetres; impedance numbers are ohms. Omitted fields remain omitted. The backend does not renumber other rules. A new rule requires an explicit unique priority. Existing omitted priorities remain unchanged; a batch may explicitly swap priorities. Conflicts with the final requested state of existing rules are rejected before the mutation transaction.

| Kind | Scalar fields | Structured fields |
|---|---|---|
| Width | min, preferred, max; impedance_driven (boolean); min_impedance, preferred_impedance, max_impedance | layer_widths: list of objects with layer_id and any min/preferred/max |
| RoutingViaStyle | hole_min, hole_preferred, hole_max; pad_min, pad_preferred, pad_max; via_style | none |
| RoutingLayers | none | routing_layers: list of objects with layer_id and allowed (boolean) |
| FanoutControl | fanout_style, fanout_direction, bga_direction, bga_via_mode, via_grid | none |
| Clearance | gap, ignore_pad_to_pad (boolean), matrix_enabled (boolean) | clearance_matrix: list of first, second, gap |
| BoardOutlineClearance | gap | clearance_matrix: list of first, second, gap |

Layer ids are the legacy signal-layer ids 1..32. Indexed updates affect only explicitly listed layers. Scalar Width limits precede explicit layer overrides in the candidate; whether a scalar write changes omitted indexed limits must be qualified before mutation. Offline checks alone do not establish that setter relationship. Clearance cells are written symmetrically, and duplicate/reversed pairs are rejected. Electrical cells currently use primitive ids 0..9; board-outline cells pair a primitive 0..9 with an edge 10..12. A global gap and specific cells must be separate operations because a global setter can replace matrix values. Matrix mode is changed only when explicitly requested.

Installed enum values (candidates, not DelphiScript qualification):

- via_style: 0 through-hole; 1 blind/buried pair; 2 blind/buried any; 3 none.
- fanout_style: 0 auto; 1 rows; 2 staggered; 3 BGA; 4 under pads.
- fanout_direction: 0 none; 1 inward; 2 outward; 3 inward then outward; 4 outward then inward; 5 alternating.
- bga_direction: 0 outward; 1 NE; 2 SE; 3 SW; 4 NW; 5 inward.
- bga_via_mode: 0 closest; 1 centred.

Primitive ids: 0 arc, 1 track, 2 SMD pad, 3 through-hole pad, 4 via, 5 fill, 6 polygon, 7 region, 8 text, 9 hole. Edge ids: 10 outline, 11 cavity, 12 cutout.

Example input (the caller supplies its exact PCB path):

```json
{
  "name": "SelectedSignalWidth",
  "kind": "Width",
  "priority": 1,
  "scope1": "InNetClass('DigitalSignals')",
  "values": {
    "min": 0.15,
    "preferred": 0.15,
    "max": 2.0,
    "layer_widths": [{"layer_id": 32, "preferred": 0.2}]
  }
}
```

## Readback and qualification limits

With `Extended=false`, readback uses existing scalar Width/ViaStyle/Clearance members and the board-outline matrix. `Extended=true` additionally requests per-layer Width/allowed layers, impedance fields, fanout fields, via style, and electrical clearance matrix/mode. Qualify one accessor family at a time using the nine standalone `RunProbe` templates and `probe_manifest.json` in `dev/probes/routing_rules/`. They require no external helper units. Use the pure `render_probe.py:render_probe(name, pcb_path, report_path, probe_id)` helper to render an exact current PCB path, a new JSON report path, and a unique lowercase UUID-hex probe id. The helper only returns source; it never dispatches or writes a PCB. A matching completed receipt with `success=true` and `matched_rules>0` is required; a checkpoint, file existence, or no matching rule is not qualification. Run them only through a dedicated script project or the guarded public runner with helpers disabled. An exception is a failed probe, not permission to retry a mutation.

`complete_readback` remains false. Substack-specific width overrides, impedance profile bindings and via-template contents are not enumerated. Effective object/pair rule resolution is not inferred from scope strings or priorities. Batch and online DRC check sets remain null: the documented options interface has those properties, but a callable legacy-board acquisition path has not been qualified. The known failed `IPCB_Board.GetState_DesignRuleCheckerOptions` must not be reintroduced from a newer .NET board interface.

Native acceptance requires: successful readback; exact intended numeric/enum values; all unrelated rules, priorities, copper and pads unchanged; save/reopen persistence for mutations; independent DRC with explicitly verified enabled-check coverage. Offline test success is not DelphiScript compilation or native acceptance.

## Saved DRC options readback

`server/saved_drc_options.py:read_saved_drc_options(pcb_path)` reads the saved CFB stream `Design Rule Checker Options6/Data` from an in-memory copy opened read-only with `olefile`. It returns exact batch/online rule-kind ids, the saved maximum violation count, known flags, unrecognized fields, source SHA-256 and stream SHA-256. The installed enum mapping is retained as metadata provenance; future unknown ids remain visible.

This requires the `olefile` dependency. It does not inspect unsaved native options, select checks, run DRC, or establish complete DRC coverage. The response therefore reports `reflects_unsaved_native_state=false`, `enabled_check_coverage_live_verified=false`, and `full_drc_verified=false`. The public wrapper must describe the saved-file boundary explicitly. Native options acquisition remains a separate unqualified interface problem; no CFB writes are used.

## Evidence

The [legacy design-object API reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-design-objects-interfaces-reference) documents the routing-rule properties. The [system-interface reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-system-interfaces-reference) documents DRC options, but does not establish their availability through this installed script receiver. Both are historical, evolving documentation.

Additional exact signatures and enum values were read without native calls from installed `Altium.Edp.Interfaces.dll`, SHA256 `66a2c1da130dbb316f8e112b7a76b6774bbd438533d2d42ab5e8e79647fd1009`. This hash is recorded in the captured metadata receipt and `routing-rule-enums.json`. It is .NET metadata evidence only. It does not establish DelphiScript exposure. No Board2+ methods were copied into the legacy board path.
