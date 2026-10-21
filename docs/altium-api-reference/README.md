# Altium scripting API reference

Retrieved and reviewed **2026-10-21**. This is a curated local reference from
official Altium documentation, containing API declarations and short usage notes.
It is not a mirror of complete articles or a list of everything callable in PAS.

## Start here

- [PCB server, board and iteration](pcb-server-board.md)
- [Primitives, geometry and rules](pcb-primitives-rules.md)
- [Client, documents and processes](client-process.md)
- [Units and constants](units-constants.md)
- [DelphiScript language and examples](delphiscript.md)
- [Source manifest](sources.json): official URLs, page titles, retrieval dates,
  published update dates and retrieval hashes.
- [Member catalog](catalog.json): interface, member, declaration, source and status.
- [Refresh and qualification](refresh.md): maintain this reference without turning
  documentation into an unsupported runtime claim.

## Evidence contract

Every catalog entry has `status: DOCUMENTED` and `runtime_verified: false`.
`evidence_kind` distinguishes a declaration from a name/example only. A missing
declaration is `null`, never reconstructed from a similarly named interface.
Declarations are normalized for whitespace only; known source inconsistencies
are preserved and noted. Getter/setter fields reproduce names in declarations;
they are not independent export evidence. These are technical API declarations,
not copied prose. Response hashes identify fetched bytes; without a stored HTML
mirror they are not an independently replayable offline copy of the source.

The historical PCB/System reference covers the Delphi SDK. Altium explicitly
states that scripting exposes a subset of the SDK. The published PCB references
retain their legacy/work-in-progress warning. A documented signature establishes
what the manual says, not installed-version availability or argument compatibility.

The separate [negative compatibility catalog](../../server/delphiscript_compatibility.json)
records observed failures. The current linter applies its conservative exact
receiver/member rule without automatically detecting the installed runtime version.
The observation itself remains version-specific. In particular, .NET
`PCBInterfaces.IPCB_Board2` and later interfaces do not
prove that `IPCB_Board.GetState_DesignRuleCheckerOptions` exists in DelphiScript.
No .NET-only member is promoted into this catalog.

This reference is **not wired into the current lint allowlist**. Future consumers
must keep unknown members as unknown, resolve inheritance and receiver identity
explicitly, and distinguish readable properties, writable properties and methods.
Documentation absence is not evidence of incompatibility. A matching name alone
does not validate an overload, parameter, transaction, document or execution state.

## Coverage

This edition stores **105 entries** across **15 named interfaces plus global
functions**, from **11 official pages**. Of these, 103 carry a declaration and
2 carry only documented usage/example evidence. No entry is runtime verified.

## Boundaries

This first edition focuses on PCB access, iteration, geometry, keepouts, rule
scope/clearance, client documents and process dispatch. Schematic, Workspace
Manager, library/model, layer-stack generations and complete rule families are
outside this edition. Selective keepout restriction APIs and modern DRC-option
accessors require separate version-specific evidence. Example snippets are original
illustrations, not runtime qualification and not authorization to change a board.

Existing implementation findings remain in [PCB primitives](../../dev/PCB_PRIMITIVES.md),
[routing](../../dev/PCB_ROUTING.md), [polygons](../../dev/PCB_POLYGONS.md) and the
[development guide](../../dev/README.md). They are not silently merged into
official-documentation evidence.
