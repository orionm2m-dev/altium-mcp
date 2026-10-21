# Native Situs module: verified read-only contract

The compiled `NativeSitusReadOnly` extension was installed, loaded and checked on
Altium Designer 26.9.1 on 2026-10-21. Three read-only commands passed with fresh,
correlated receipts. This is a limited runtime qualification, not full Situs support.

The qualified binary comes from source commit `f03ded1` and has SHA-256
`cf4f117c9b8c3cafca29d4a09c4a4149ea99ec809cdf7c847f75ede1242d6b64`.
The installed INS/RCS registration and native bootstrap were exercised after a
normal restart; the known server was loaded through the native client API.
Hot registration, unloading and other Altium versions remain unqualified.

## Verified commands

- `NativeSitusReadOnly:Heartbeat`: execution inside the loaded module and receipt
  creation passed for the pinned process and exact active PCB.
- `NativeSitusReadOnly:ReadCommandState`: native state queries for
  `PCB:Autoroute` actions Start, Setup, Pause, Stop and Reset passed. In the
  observed context, Start/Setup/Reset were enabled and Pause/Stop were disabled;
  all were visible and unchecked. These flags are observations, not a substitute
  for routing lifecycle or progress evidence. No action was launched.
- `NativeSitusReadOnly:ReadBoardCapabilities`: safe typed acquisition of
  `PCBInterfaces.IPCB_Board_SaveLoadParameters` succeeded, and
  `SitusStrategyValid()` returned true. This qualifies that acquisition and
  query in the observed context only. It does not qualify strategy contents,
  setters, or all members of the installed EDP interface.

All three receipts reported success, no routing dispatch and no document save.
PID, exact creation FILETIME, request ID and PCB path were checked against the
request. The retained local audit contains the full receipts; private paths and
process identifiers are intentionally omitted from this generic package.

## Input and evidence boundaries

Each command accepts an exact absolute PCB path, a fresh 32-character lowercase
hexadecimal request ID, an existing absolute receipt directory, an expected PID
and an exact decimal process-creation FILETIME, separated by `|`. The module
checks current/context PCB identity and process identity before the query and
again before writing. Receipts use exclusive creation; an existing ID is refused.

The unchanged prototype writes `runtime_qualified: false` in every payload.
This is its conservative self-report, not a live qualification database. The
external qualification in [qualification.json](../../dev/native_situs/qualification.json)
records only the three verified command paths, version and binary hash.
It does not reinterpret that payload field as a universal success claim.

## Work that remains unqualified

Native strategy read/write, pass order and repetition, via effort, orthogonal
preference, layer-direction settings, rip-up settings, router start/pause/resume/
stop/reset execution, and progress reporting require their own implementation
and runtime checks. A true `SitusStrategyValid()` value does not close these gaps.
The separately verified global Lock All Pre-routes tool retains its own
[narrow contract](native-settings-contract.md); it is not attributed to this DLL.

Missing operations must use application-native interfaces through MCP or the
native module. Windows UI Automation, focus manipulation, synthesized input and
window/button messages are not fallback transports, including for modal dialogs.
No capability-matrix counts are promoted by this documentation update.
