# Native settings transport candidate

The separate `NativeSitusControl` bundle exposes only `ExecuteRequest`; its JSON actions are `read_settings` and `set_settings`. No route start, UI dialog operation, script source, arbitrary process name or save command is accepted. The existing `NativeSitusReadOnly` bundle is unchanged.

Production pure helpers are `server/native_situs_protocol.py`: `build_request(request, request_path)` returns exact UTF-8 request bytes, SHA256, one native command-line argument, and fixed attempt/response paths. `classify_response(request, request_sha256, response, launcher_exit_code=None)` does not promote launcher exit 0 to completion and preserves nested action failure/unknown state.

The request envelope is exactly `{schema_version:1, request_id, action, pcb_path, pid, creation_filetime, exe, payload}`. PID is a positive integer; FILETIME is an exact decimal string; the nonce is 32 lowercase hex characters. Paths are canonical absolute local Windows paths. The filename must be `<nonce>/request.json`. The native handler rejects oversized JSON (64 KiB), duplicate keys, unknown envelope keys, reparse-point exchange paths and mismatching SHA256.

Delivery is one argv argument: `-RNativeSitusControl:ExecuteRequest(Request=<base64url UTF-8 request path>|Sha256=<64 lowercase hex>)`. The managed handler uses the installed `DXP.ParameterList(string)`, `Count`, and `GetParameterValue(string)` API. No shell, keyboard, window automation, focus operation or per-operation PAS file is used by this candidate. The public backend still must pin the exact running configured process and persist its own dispatch intent before launching once.

Only the winner of the exclusive `attempt.json` creation may publish `response.json`, including known refusals. The claim is flushed before checking exact native PID/creation/executable and active/context PCB immediately before the typed dispatcher. The same native identity is checked after the dispatcher. Partial final output uses `response.json.pending` and is atomically renamed without overwrite. Existing attempt, pending or final output forbids replay. A missing receipt, lost callback, identity change or setter exception remains unresolved; no automatic retry or rollback is authorized.

`success=true` at the envelope means the native handler returned; the action result is in `data`. Consumers must check nested `success`, `readback_verified`, and `outcome_unknown`. Completion does not prove save/persistence or global routing qualification. The native handler never saves documents.

The current build is compile-only. `transport_qualification.json` records exact artifacts and qualification boundaries. Installation, Altium restart and the first harmless read request are separate manual steps. The bootstrap/INS/RCS shapes follow the previously qualified separate read-only bundle; this new bundle has not yet been loaded in Altium.

References: [official process parameter documentation](https://www.altium.com/documentation/altium-designer/scripting/running-scripts), [official system API](https://www.altium.com/documentation/altium-dxp-developer/system-api). These establish the process/parameter model, not a new promise about arbitrary command-line dispatch. The local bridge already uses `-R...(...)`; this new process remains to be qualified.
