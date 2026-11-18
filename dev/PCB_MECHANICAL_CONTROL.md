# Mechanical PCB primitive control

`control_pcb_mechanical_objects` supports read-only `inspect` and explicitly targeted `delete` on the already active exact PCB path. The client and PCB process depths must both be zero.

Eligible pads are free, unconnected, round, simple, non-plated through holes with no copper extending beyond the hole. Eligible regions are free, unconnected, named, straight-edge copper-kind keepouts without holes. Component primitives, routing, polygons and board cutouts are excluded.

Deletion requires `{kind, name, expected_key}` returned by native inspection. The complete batch is preflighted, then each target is checked again immediately before removal. Any stale/ambiguous/foreign target refuses preflight. All targets must read back absent and process depths must return to zero before save. No automatic retry, rollback, UI action or document opening is implemented. A partial native failure may leave unsaved in-memory changes.

Validation: 9 contract tests and 22 existing primitive tests passed on 2026-11-10. Static DelphiScript preflight found no known blockers. Native inspection, stale-key refusal, deletion and reopen verification are pending; do not infer runtime qualification from these offline tests.

Native qualification 2026-11-10, AD26.9.1: inspect returned 21 eligible objects; stale key refused before mutation with unchanged file hash. Valid batch removed exactly four free NPTH pads and eight named keepouts; native readback/save succeeded. Independent saved-file comparison with a no-edit native save control verified all retained pads/regions, routes, components and rules. Ordinary save causes identical via/first-rule serialization changes; polygon invalidation and derived net statistics are separately accounted for. Original strict byte comparison is retained. Full close/reopen was not performed because the close API requires foreground confirmation; no focus/UI workaround used. The semantic comparison passed; its result and the baseline and edited file hashes are kept outside the repository. Qualification covers this exact batch, not arbitrary object types.
