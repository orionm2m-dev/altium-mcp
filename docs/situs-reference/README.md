# Situs reference and capability review

Reviewed **2026-10-21**. This package contains a compact, searchable reference
from seven official Altium pages, plus a separate review of this server's
implementation. Full articles and private PCB/session receipts are not included.

## Public read-only tools

- `search_situs_reference(query, limit=10)` searches documentation and capability
  records together. Results retain their source links and qualification limits.
- `get_situs_capabilities()` returns all 38 reviewed capabilities, status counts,
  the reviewed commit and observed runtime version. It does not query Altium.
- `search_altium_api(query)` remains the separate interface/member reference.

Examples: `Preferred`, `Layer Directions`, `situs.strategy.select`,
`situs.rip_up.custom_checkbox`, and `situs.run.stop`.

The reference has 78 entries: passes, strategies, directions, controls, rules
and important operating limits. Use [catalog.json](catalog.json) and
[sources.json](sources.json) for exact IDs, links and retrieval dates.

## Important distinctions

The [official Situs guide](https://www.altium.com/documentation/altium-designer/pcb/routing/situs-topological-autorouter)
explains that Preferred dimensions govern autorouting. A user strategy's ordered
passes apply to whole-board routing; partial scopes use Main. Changing a
preference does not itself rewrite existing copper. Situs needs a closed keepout
boundary and does not implement the Routing Corners rule. Review applicable
rules before routing and run DRC on the result.

Situs Stop waits for the current pass to finish. Pause/Resume and Reset belong to
the router. The scripting debugger's Stop is a different operation; executor
recovery must never be presented as a qualified router lifecycle control.

## What the current tools cover

The [capability matrix](capabilities.json) records **11 available, 10 partial,
13 missing, 3 probe-only and 1 blocked** capabilities. Available is deliberately
narrow: read the supported actions and qualification boundary of each entry.
It does not mean that every field of the corresponding dialog is controllable.

The principal gaps are typed setup/start and job state, strategy selection and
editing, pass ordering, Layer Directions, structured Setup
Report/progress, fanout execution and native router Stop/Pause/Reset. Generic
script execution and installed command names do not fill these gaps. In the
observed custom Rip-up checkbox, the native check query did not distinguish the
two visual states; the current tool refuses automatic mutation on that control.

Generic rule tools cover selected families and fields. They do not provide all
RoutingLayers/Fanout values or complete effective-rule resolution. A complete
DRC list is distinct from proof that all native checks were enabled and ran.

## Evidence and maintenance

Documentation entries are `DOCUMENTED` and `runtime_verified: false`. The
capability review is a dated snapshot, not a live capability detector. A true
runtime flag applies only to the explicitly named observed action, version and
boundary; it does not qualify another branch or arbitrary input. No lookup
starts a process, modifies a board, clears a script error, or authorizes action.

Implementation references and source hashes identify the reviewed commit
`9e5ed77`; line numbers are for that revision. Opaque observation IDs identify
retained local evidence; private evidence is intentionally outside this generic
package. Re-review affected records after implementation/runtime changes. Do not
promote a missing, blocked or probe-only feature because a similar API appears
in documentation. Unknown search terms mean unknown, not unsupported.

## Qualified native Lock All Pre-routes command

`control_situs_native_settings` reads or sets the global PCB `LockPreRoutes` option without UI Automation or foreground activation. The dedicated public transport was verified on AD26.9.1: inspect, set false to true, and restore true to false, each with exact process/board guards and correlated readback. Forty-five offline tests cover validation, precision, state comparison, background readiness, cancellation and no replay. No board save, routing completion or persistence across restarts is implied. See [native-settings-contract.md](native-settings-contract.md).

The capability matrix now includes this narrow incremental review at commit `a8b4955`. Other entries retain their earlier reviewed commits and boundaries; they have not been promoted because a new prototype compiles. The C# extension in `dev/native_situs` is installed and loaded on AD26.9.1. Heartbeat, command-state readback and typed board-capability readback passed with correlated native receipts. Strategy read/write and progress remain unqualified; see [native-module-contract.md](native-module-contract.md).

## Native extension staging tool

`stage_situs_native_extension` provides filesystem-only `inspect` and `install`
for the fixed reviewed DLL/INS/RCS prototype. It requires a fresh matching plan,
refuses existing targets, and records verified hashes with partial-failure rollback.
Staging does not load or qualify the extension. See
[native-extension-staging.md](native-extension-staging.md).

The compiled active-board settings candidate has a dedicated [native strategy contract](native-strategy-settings-contract.md). Its runtime qualification remains separate from offline tests.

## Native selection contract

The [native selection review](native-selection-contract.md) separates the documented
`Selected` property from indexed board selection and installed collection methods.
It records metadata evidence and the remaining qualification boundary; no routing
or selection mutation capability is promoted by this review.
