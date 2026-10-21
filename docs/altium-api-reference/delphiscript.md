# DelphiScript language and example selection

Retrieved 2026-10-21. Status: **DOCUMENTED**, runtime unverified.

## Language boundaries

Variables and parameters use Variant semantics at execution time; a declared
interface/type is useful documentation, not a compile-time binding guarantee.
Type casts do not establish SDK interface availability. User-defined records and
classes cannot be introduced as ordinary compiled Delphi types. Prefer standalone
helper routines. Use the scripting set helpers for API object/layer sets rather
than assuming Object Pascal set syntax is supported.
[Official differences reference](https://www.altium.com/documentation/altium-designer/scripting/delphiscript/delphi-differences)
(updated 2021-07-23).

`uses` is optional for imported API units, but a helper in a different script
project is not thereby imported. The keyword reference documents `try/finally`,
procedures/functions, array syntax, and control flow. This does not make arbitrary
Delphi RTL symbols available.
[Official keywords reference](https://www.altium.com/documentation/altium-designer/scripting/delphiscript/keywords)
(updated 2021-07-22).

The functions reference groups file, mathematical, string and scripting extension
routines. Its `Evaluate` facility executes an expression at runtime; using it
cannot statically prove that a member exists. Text-file examples use
`AssignFile`, `Rewrite`, `Writeln`, `CloseFile` and cleanup in `finally`.
[Official functions reference](https://www.altium.com/documentation/altium-designer/scripting/delphiscript/functions)
(updated 2021-07-14).

## Examples to retrieve for a specific implementation

Altium's example index was updated 2026-09-10, but the linked ZIP originated with
Altium Designer 10. The page warns that older examples can cease to work as the
API changes; a fresh index date is not a fresh qualification of every script.
Select a small relevant example before downloading an entire archive:

- `PCB Iterators / Count_pads`: board iterator lifecycle and filtering.
- `PCB Iterators / IterateRegions`: region enumeration.
- `PCB Iterators / SpatialIterator`: bounded spatial enumeration.
- `DXP / ClientServerInterfacesReport`: client/server command inventory.
- `PCB Layers / QueryLayerStack`: historical layer-stack access.

The list identifies official example projects; this edition does not bundle or
claim to execute their source.
[Official examples reference](https://www.altium.com/documentation/altium-designer/scripting/examples-reference).

## Repository-specific boundary

Read the [sandbox contract](../../dev/README.md#gotcha-the-sandbox-is-standalone)
and [preflight contract](../../README.md#delphiscript-preflight-before-sandbox-execution)
before developing a tool. Production helpers are not universally available to a
standalone project. Run the public lint first; a result without known blockers
still does not certify execution. Qualify new API use on an explicit disposable
document, with actual readback, separately from this documentation task.
