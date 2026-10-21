# Typed active-board Situs settings

This candidate compiles against the installed Altium AD26 interfaces. Its native getters and setters require separate qualification. Offline tests do not establish native behavior, persistence, undo or atomicity. It does not install or alter the loaded `NativeSitusReadOnly` module.

## Dispatch contract

`SitusSettings.Dispatch(string action, JsonElement request, DXP.IServerDocumentView context)` receives the complete transport envelope. The transport owns native PID/creation/executable checks, exact current/context PCB identity, a durable single-attempt marker and response files. Settings code performs no file I/O, process launch, UI operation, routing, save, preset enumeration or arbitrary parameter import/export.

- `read_settings`: `payload` must be `{}`. Returns `snapshot` and `snapshot_hash`.
- `set_settings`: `payload` must contain `expected_snapshot_hash` and a nonempty `changes` object. Reuse the lowercase hash returned by this same module as an opaque comparison token. Do not construct it from a partial client projection.

```json
{
  "expected_snapshot_hash": "<64 lowercase hexadecimal characters>",
  "changes": {
    "name": "Current scratch strategy",
    "description": "Qualification only",
    "prefer_orthogonal": true,
    "layer_directions": [{"layer_id": 1001, "direction": 11}]
  }
}
```

The example layer ID is illustrative. Use only IDs returned by the current native snapshot with `layer_used: true`; no legacy numeric layer mapping is assumed. Omit unchanged fields. Native signal layers are enumerated and sorted by their native IDs.

Name is bounded by this tool to 1..256 characters, nonblank; description to 0..4096. Both reject NUL. These are transport limits, not documented native string limits. Boolean fields require JSON booleans. Duplicate keys, duplicate layer IDs, unknown fields, unused/nonexistent layers and unknown enum values are rejected.

The snapshot protects PCB path and native board identity, strategy validity, all five active-strategy fields, each enumerated signal layer's ID/name/used flag/direction, and global LockPreRoutes. `passes` is returned as an opaque string; `via_bias` is returned as the observed integer without assuming a range. Setters for these two values are deliberately excluded. Global LockPreRoutes is read-only here; its separately qualified native tool remains responsible for writes. The board-owned strategy interface is used directly; this does not prove that native preset-library objects are independent or writable.

## Mutation and readback

All requested fields are parsed and validated before any setter. The initial complete snapshot must match `expected_snapshot_hash`. Before every effective setter, the complete snapshot is reacquired and compared with the last expected state. Each setter is attempted at most once, followed by a fresh complete readback. The module stops at the first mismatch or exception. A final complete read verifies the requested result, including unchanged protected fields. An already-satisfied request calls no setter.

A throwing setter may have taken effect. A fresh read is attempted for evidence; failure to read produces a null `after` snapshot. Partial failure returns `success: false`, `outcome_unknown: true` if any setter was attempted, and the attempted field list. There is no automatic retry or rollback, and no atomicity/undo/persistence claim. A full snapshot comparison cannot provide a native transaction or exclude all reentrant external changes. The caller must hold the shared application-operation lock and retain the durable attempt/response receipts. Transport completion and inner settings `success` must both be checked.

The getter-only path and every native setter reacquire current/context PCB identity, the same native board object and zero Client/PCB process-control depth. No board save or dirty-state setter is called. Saving/persistence remains a separate authorized operation.

## Grounded installed API surface

These are installed .NET metadata declarations, not proof that similarly named members are exposed to DelphiScript. SDK references are `Altium.SDK.dll`, `Altium.SDK.Interfaces.dll`, `Altium.Edp.Interfaces.dll` and `Altium.Dxp.Interfaces.dll`.

| Declaring interface | Members used |
| --- | --- |
| `PCBInterfaces.IPCB_Board_SaveLoadParameters` | `GetState_SitusStrategy(): IPCB_SitusStrategy`; `SitusStrategyValid(): bool`; `GetState_RoutingLayerDirections(Basic.TV7_Layer): RT_PCB.TRouteLayer`; `SetState_RoutingLayerDirections(Basic.TV7_Layer, RT_PCB.TRouteLayer): void` |
| `PCBInterfaces.IPCB_SitusStrategy` | `GetState_Name/Description/Passes(): string`; `GetState_ViaBias(): int`; `GetState_PreferOrthogonal(): bool`; `SetState_Name/Description(string)`; `SetState_PreferOrthogonal(bool)` |
| `RT_PCB.IPCB_Board` | `I_ObjectAddress(): IntPtr`; `GetState_FileName(): string`; `SignalLayerIterator(): IPCB_LayerIterator`; `LayerName(Basic.TV7_Layer): string`; `GetState_LayerIsUsed(Basic.TV7_Layer): bool` |
| `RT_PCB.IPCB_LayerIterator` | `First/Next(): bool`; `Layer(): Basic.TV7_Layer` |
| `PCB.IPCB_ServerInterface` | `Internal_GetCurrentPCBBoard(): object`; `Internal_GetState_SystemOptions(): object` |
| `PCB.IPCB_SystemOptions` | `GetState_LockPreRoutes(): bool` |
| `DXP.IProcessControl` | `GetProcessDepth(): int`, obtained from Client and PCB server module |

`ValidatePasses()` is not used as a validator: its mutation semantics are unqualified. Arbitrary import/export parameter objects and paths are excluded.

All 12 installed `RT_PCB.TRouteLayer` enum values are recognized:

| Value | Native constant |
| --- | --- |
| 0 | `eRLLayerNotUsed` |
| 1 | `eRLRouteHorizontal` |
| 2 | `eRLRouteVertical` |
| 3 | `eRLRouteSingleLayer` |
| 4 | `eRLRoute_1_OClock` |
| 5 | `eRLRoute_2_OClock` |
| 6 | `eRLRoute_4_OClock` |
| 7 | `eRLRoute_5_OClock` |
| 8 | `eRLRoute_45_Up` |
| 9 | `eRLRoute_45_Down` |
| 10 | `eRLRouteFanout` |
| 11 | `eRLRouteAuto` |

## Offline verification and next native qualification

`run_settings_tests.ps1` compiles only the settings source and fake-surface test entry point against installed SDK/runtime references, then runs the fake tests in an ordinary .NET host. It never instantiates a native Altium object. Current coverage is 32 tests, including all enum values, stale state, external changes, hidden side effects, partial failure, unknown post-failure state and invalid input. The compile uses AD26's .NET 8.0.15 reference assemblies; the test host may use an installed compatible .NET 8 patch release.

After the separate Control module is installed and a normal restart loads it, first qualify `read_settings` on an exact scratch document. Existing runtime evidence proves only the typed board-save/load interface and `SitusStrategyValid` on the earlier read-only module; the RT board cast, complete getter set, iterator and depth guards in this module still need qualification. Then perform one bounded scratch change and restoration with fresh full hashes and native readback. Check native strategy validity, unrelated fields, effective layer directions and persistence separately. Passes, Via Bias, preset CRUD and Rip-up Violations remain unsupported by this module.
