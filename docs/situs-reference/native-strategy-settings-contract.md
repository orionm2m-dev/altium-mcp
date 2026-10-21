# Native Situs active-board settings

`control_situs_strategy` uses the separately registered `NativeSitusControl`
module. Each call sends bounded JSON to one fixed native command and reads a
correlated response. It generates no Pascal source and issues no Windows UI
input. This batch is compiled and offline-tested; runtime qualification is
pending. It does not claim complete Situs support.

The module provides `read_settings` and `set_settings`. Read returns the active
strategy, all native signal-layer directions, the global LockPreRoutes value
and a complete snapshot hash. Set accepts that hash and explicit changes to
name, description, orthogonal preference and layer directions only. A stale
snapshot refuses mutation. Writes and full readback occur inside Altium.

See [typed settings contract](../../dev/native_situs_next/SETTINGS_CONTRACT.md)
and [transport contract](../../dev/native_situs_next/TRANSPORT.md) for schemas,
API ownership, limits, native identity checks and capability boundaries.

The MCP service retains the shared command lock and cross-process claim through
bounded observation, including caller cancellation. The request, native attempt,
response and service result are retained under one operation ID. Repeating the
ID returns historical evidence without execution. A completed launcher is not
proof of native completion. Both transport and inner settings results must pass.
Unknown or partial outcomes retain the recovery latch; no automatic retry,
rollback, Stop or UI fallback is performed. File or preference persistence,
undo and atomicity are not claimed.

The reviewed installer supports `bundle="native_control"` alongside the default
`readonly` bundle. Inspect first, then install with that exact plan hash. Module
registration requires a separately managed Altium restart; installation alone
is not runtime qualification. Existing modules are never overwritten.

Pass editing, ViaBias writes, global preset CRUD, Rip-up Violations, nonmodal
whole-board start, pause/resume/stop/reset and progress are not implemented by
this command. The previously qualified LockPreRoutes setter remains separate.
