# Ordinary Altium shutdown

`close_altium_normally` requests the installed `Client:QuitFromEDAClient`
process once. This is an ordinary application exit; it never force-terminates a
process, answers prompts, saves documents, or restarts the application.

Inputs are the exact current PCB path, target PID, `ft:<decimal FILETIME>` process
creation token, optional unique 32-character lowercase hexadecimal operation ID,
and a 1–30 second exit-observation timeout. The configured executable and exchange
directory come from the MCP server, not tool arguments.

The backend holds the existing bridge lock and cross-process recovery claim. It
pins a query/synchronize-only process handle and requires one matching configured
instance with a responsive main window and no modal blocker. It runs a new copy
of the fixed read-only document inventory in a unique standalone script project;
the shared Sandbox is not involved. The returned request ID, current PCB guard,
complete stable inventory, and every document's `modified=false` value must pass.
External process identity is checked before and after that inventory. Inventory
receipt acceptance is not a claim that Pascal independently read the native PID.

Immediately before the exit request, the backend checks process identity,
readiness, recovery state and inventory age again. It writes a pending recovery
record before launching the configured executable with the fixed
`-RClient:QuitFromEDAClient` argument. The installed process name and absence of
parameters were observed in `System/dxp.ins` and `System/dxp.rcs`; normal exit is
only verified when the pinned main process and owned launcher both exit with zero
codes. The native exit process retains normal Altium behavior, including any
application-generated save prompts; the backend never responds to those prompts.

A dialog, timeout, cancellation, launch failure, changed identity, missing receipt
or pending launcher leaves the operation blocked or uncertain. Repeating an
operation ID returns its historical evidence without dispatch. It is not a retry
or current-state check. No automatic retry, recovery, or restart follows.

A clean inventory describes the observed loaded document buffers at that time;
it does not grant permission to discard subsequent edits. The normal exit flow
continues to provide Altium's own prompts if a document changes after inspection.
Disk hashes are not used as substitutes for native modified flags.

Offline validation:

```
python -B -m unittest server.tests.test_altium_orderly_close -q
```

The standalone inventory was runtime-qualified separately. The new combined
shutdown service remains unqualified until its first explicit root-controlled
native invocation and receipt review.
