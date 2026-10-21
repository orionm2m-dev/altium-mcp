# Refresh and qualification procedure

Edition: 2026-10-21. This procedure does not launch Altium or change a PCB.

1. Inspect the existing catalog, negative compatibility catalog and local dev
   findings. Add a missing documented fact without erasing contradictory native
   evidence. Do not create a second overlapping corpus elsewhere.
2. Open the exact official URLs in `sources.json`. Record retrieval date, page
   title, visible update date, final URL and SHA256 of the downloaded response.
   A response hash includes changing page markup; it is provenance, not proof
   that an API signature changed. An inaccessible page is an unresolved source,
   not permission to substitute a search snippet or third-party claim.
3. Compare the relevant interface/member section. Retain only concise paraphrases
   and technical declarations needed here. Do not copy complete articles. Preserve
   the original declaring interface, spelling, parameters, return type and access
   qualifiers. Record source errors or omissions; do not silently repair them.
4. Keep `status=DOCUMENTED` and `runtime_verified=false`. A property declaration
   that mentions a getter/setter does not separately prove those accessor names
   are exposed to scripts. Add an accessor as a callable entry only with its own
   documented evidence. A missing member stays unknown. Keep .NET metadata in a
   separate platform-specific record; do not merge Board2+ into IPCB_Board.
5. Update topic summaries and the coverage counts in README. Run
   `python docs/altium-api-reference/check_catalog.py` for JSON structure, source
   references, uniqueness, status and local navigation. Review the diff and commit
   only intended reference files. No native application access is required.

## Separate runtime evidence

A future runtime qualification should record exact product version, receiver
creation path, member and argument values, document identity, initial state,
dispatch/result receipts and verified readback. Native failures belong in the
negative catalog only with bounded evidence. A changed file or an outer script
success alone is insufficient. Read-only probing is preferable where it answers
the question; mutations need a disposable target and explicit tool safeguards.

The current lint does not consume `catalog.json`. Future integration must retain
its unknown-member warnings and negative evidence. Matching this catalog must not
automatically suppress argument/type/runtime diagnostics or permit a dispatch.

## Known gaps in this edition

- Complete current-version DelphiScript exported-member inventory: not published
  by the reviewed legacy pages; this is a selected reference, not that inventory.
- Selective keepout restriction masks and current DRC option access: not certified
  by the legacy Boolean keepout property or .NET interfaces.
- IPCB_Contour versus Pgpc_vertex_list acceptance by `SetOutlineContour`: requires
  binding/version qualification; the historical declaration is preserved.
- All rule subtypes, modern layer-stack interfaces, Schematic, Workspace Manager,
  integrated libraries and model APIs: only navigation to official references,
  no purported complete member coverage here.
- Official example ZIP: located through the index but not downloaded or executed.
- Native command parameter completeness and asynchronous completion: separate
  from a documented `RunProcess`/`SendMessage` call form.
