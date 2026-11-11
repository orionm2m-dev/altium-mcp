# Changes

## 2026-11-09 — Recover after an unresolved pre-close inventory latch

close_altium_normally leaves the latch in orderly_close_inventory_pending when its
dedicated inventory launcher never returns a verified receipt. Once that Altium
process is gone the latch could not be resolved: fresh_start_after_pending_close
accepts only orderly_close_pending, and manual Stop confirmation refuses because
the current process is not the pending operation's. Added
fresh_start_after_pending_inventory in altium_orderly_close, routed from
_recover_emergency_serialized. It requires the exact checkpoint on disk, a
complete previous identity under the current user, exactly one recorded
inventory launcher that is owned-and-exited, absent, exited, or whose PID is now
an unrelated image, the exact previous main proven exited by creation time, a
newer successful public normal-start receipt and that same ready process; it
then authorizes only the usual read-only probe and records the evidence. No
quit, Stop, latch deletion, save or mutation replay. The inventory itself is
never retroactively completed. Tests cover absence, PID reuse, a live configured
launcher, exit-code recording, a still-running previous main, wrong phase, an
older start and the recovery routing.

## 2026-10-21 — Bind windowless error acknowledgment to explicit visual evidence

The observed Error dialog exposes only empty TXPExtPanel controls and its OK
button; its Undeclared identifier text is visible only in the image. Added an
explicit caller visual confirmation path for that case. The exact public
screenshot receipt, PNG SHA256, configured process creation/SID/executable and
window identity must match; a fresh pinned same-method PrintWindow capture must
reproduce the full PNG before one gesture. Readable conflicting text cannot be
overridden. Classification is labeled caller attestation, never OCR. A timed-out
capture remains read-only with its own resources, and no input is permitted.
Tests cover evidence/pixel tampering, foreign identity, absent classification,
PNG encoding/GDI cleanup, worker DPI cleanup and no-input refusals. Native
qualification remains the caller's next separate step.

## 2026-10-21 — Explicit acknowledgment of an inspected script identifier error

Added public acknowledge_script_error with inspect/acknowledge only. It pins the
configured process and exact Error/TMessageForm plus main title, reads child text
with bounded WM_GETTEXT, and accepts only Undeclared identifier with one supported
OK control. Acknowledgment requires the observed text and button HWND, repeated
identity/content checks, and at most one bounded gesture. No Stop, new script,
termination, save, activation or latch mutation occurs. Partial/uncertain delivery
is retained; closure and executor recovery are separate observations. Mocked tests
cover exact binding, text/class/button refusal, edit-content reads, both locks and
partial delivery. Native qualification has not been performed by the developer.

References: [GetWindowText](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getwindowtextw),
[WM_GETTEXT](https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-gettext).

## 2026-10-21 — Read and explicitly enable the Situs Rip-up checkbox

Inspect reports the exact Rip-up Violations After Routing control and check state.
The new enable_rip_up action requires its observed HWND and expected current bool,
checks the pinned process/dialog/main and supported checkbox class/style and binary check state,
and sends at most one bounded BM_CLICK. Already enabled is a no-input success;
stale state, unsupported controls and ambiguity refuse input. Readback verifies
the checkbox only, not dialog acceptance or routing. No visual-only BM_SETCHECK,
retry, other setting change or native call was used during development. Mocked
tests cover readback, idempotence, stale/unknown state, identity/style refusal and
uncertain delivery. Native qualification remains separate.

References: [BM_GETCHECK](https://learn.microsoft.com/en-us/windows/win32/controls/bm-getcheck),
[BM_CLICK](https://learn.microsoft.com/en-us/windows/win32/controls/bm-click),
[SendMessageTimeout](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendmessagetimeoutw).

## 2026-10-21 — Address observed TXPBitBtn controls with one client message pair

A native Situs OK BM_CLICK was queued but left the dialog open. TXPBitBtn now
uses one control-client-center WM_LBUTTONDOWN/UP pair; standard buttons retain
BM_CLICK. Exact button, pinned dialog/main and client bounds are rechecked before
each message. There is no global input, screen positioning or automatic retry.
Attempted/posted counts and partial delivery remain separate from completion.
Mocked tests cover the pair, unchanged standard buttons, invalid bounds, partial
post failure and identity change between messages.

## 2026-10-21 — Narrow inspection and button control for Situs Setup

Added `control_situs_setup` with inspect/OK/Cancel/Route All only. Process identity,
exact dialog and main title, same-process ancestry and unique live button caption
are revalidated before one BM_CLICK post. Foreign embedded descendants are
skipped. Main title proves only the filename, not document path. Posting is not
reported as completed routing or a closed dialog. Mocked tests cover identity,
button refusal, single dispatch and both serialization locks.

## 2026-10-21 — Use physical DPI coordinates for exact-window capture

Exact screenshot workers now establish per-monitor-v2 thread DPI awareness
before window bounds are read and restore the prior context in finally. This
avoids mixing DPI-virtualized bounds with physical PrintWindow pixels, without
moving or resizing Altium. Read-only descendant metadata uses the same context
and pinned parent identity. Tests cover context restoration/refusal, ordering,
capture failure cleanup and child-window ownership.

References: [GetWindowRect](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getwindowrect),
[SetThreadDpiAwarenessContext](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setthreaddpiawarenesscontext).

## 2026-10-21 — Capture an exact visible Altium window without activation

Added optional `window_handle` to current-mode screenshots, with zoom forbidden.
The exact window uses the existing PrintWindow path with a pinned query-only
process handle and identity checks before and after capture. Rejected targets
never fall back to another window or editor dispatch. Mocked tests cover exact
dialog capture, invalid modes, foreign/hidden/changed targets and resource release.

## 2026-10-21 — Public read-only Altium environment inspection

Added `get_altium_environment()` as a direct wrapper around the existing process,
window and dialog inventory check. Results retain its readiness limitations and
include `no_new_dispatch: true`; no recovery, focus or checkpoint action occurs.
An isolated wrapper test verifies both ready and blocked inventory responses.

## 2026-10-21 — Reconcile an existing probe through a dispatch-free public tool

Added `reconcile_script_probe(recovery_token)` as a narrow, locked wrapper around
the existing late-result verifier. It requires the exact pending checkpoint and
has no recovery-dispatch or fresh-start path. Focus and all new dispatches remain
outside this tool. Mocked tests cover token/phase refusal, both locks, successful
reconciliation and lock failure.

## 2026-10-21 — Resume a completed Stop without dispatching it again

Added `resume_completed_stop` for an exact `blocked_after_stop` token with one
persisted successful Stop-launch exit. Successful normal-start evidence is
matched to the pinned current process, while old PID-only identity/delivery
limits remain explicit. Under both recovery locks, guarded focus is followed by
one private single-use capability and the existing unique probe path. Unknown,
stale, changed or active-launcher evidence is refused; no manual-stop claim,
latch deletion, repeated Stop or document mutation is introduced. Mocked tests
cover one probe, invalid/stale identities, focus and receipt races, active PIDs,
capability reuse, cancellation and failed-probe latching.


## 2026-10-21 — Accept the observed unowned AD26 framework shell

AD26 also presents an unowned `TDocumentForm` alongside one unowned, empty-title
`TApplication`. Focus now accepts that exact framework arrangement on the same
PID/thread, in addition to the direct-owner case. Modal/other forms, named or
foreign-thread framework windows and multiple framework candidates remain
blocking. Results include the framework identity; refusals include observed
owner/PID/thread values. Regression tests cover the observed arrangement and
preserved refusal cases. No generic window-class or ownership relaxation.


## 2026-10-21 — Recognize the verified Delphi framework owner during focus

A visible empty-title `TApplication` no longer blocks focus when it is the exact
direct owner of the unique `TDocumentForm`, on the same process/thread. Window
ownership and the framework title are revalidated with the existing identity
checks. Named, unrelated, disabled, foreign-thread and cyclic owner forms remain
blocking; the framework window itself is never restored or activated. Mocked
regression tests cover the allowed owner and refusal/race cases.


## 2026-10-21 — Explicit process-scoped main window focus

Added public `focus_altium(target_pid)` for restoring and foregrounding the exact
configured Altium main window without script execution or input injection.
Process/user/creation identity, unique main window, responsiveness and absence
of other visible forms are checked before and after actions. Asynchronous
restoration and foreground verification are bounded; Windows focus denial is
reported without retries. Recovery state and documents are untouched. Bridge
and cross-process locks prevent overlapping recovery, including cancellation
while a focus worker is finishing. Mocked tests cover identity/refusal, window
changes, bounded restore, foreground denial, public schema and cancellation.
Native window activation remains to be qualified through the public tool.


## 2026-10-20 — Exact import-wizard inspection and cancellation

Added `control_import_wizard` as a public diagnostic/cancel tool that remains
available while script execution is latched. It binds a single Cancel gesture to
a freshly inspected process creation time, configured executable, window and
button. A persistent authenticated observation expires after 120 seconds and is
consumed before action. The bridge lock and cross-process emergency claim
serialize inspection/cancellation with other recovery operations.

No recovery latch clearing, script dispatch, save, process termination, launcher,
mutation retry or generic dialog answer is performed. Wizard origin remains
unknown. Offline tests cover the policy, persistence, stale identities and native
callback failures with mocked adapters. Native cancellation is not qualified by
those tests.

## 2026-10-20 — Block recovery dispatch while an import wizard is visible

The recovery preflight now recognizes `TPCBImportWizard` as a blocking modal,
without classifying it as a debugger failure. A visible wizard prevents Stop or
probe dispatch; a stale hidden wizard does not block the ready main window.
No dialog is answered and no existing recovery latch is cleared by this check.

The public window handle parameter uses an integer schema. FastMCP 1.5 decodes
numeric strings before validation, so a string-only schema rejected those calls
before entering the tool. Internal identity records retain decimal strings. A
mock FastMCP `list_tools`/`call_tool` test covers the actual transport conversion.


## 2026-10-20 — Resume a captured Stop after wizard cancellation

Added `cancel_import_wizard_and_resume_pending_stop`. It captures and verifies the
currently pending exact Stop process before cancelling the inspected wizard,
waits on the same query/synchronize-only handle, then permits one unique
read-only probe after successful exit and ready-main checks. No Stop is repeated
and no manual-stop claim is fabricated. The internal one-use proof cannot be
supplied through public JSON. Default unresolved-recovery blocking is unchanged.
Historical PID-only launch identity is explicitly not claimed as verified.

## 2026-10-20 — Observe ended process handles without token lookup

Added a query-only exit observation using exact PID, raw creation FILETIME and
`GetExitCodeProcess`. Emergency recovery and confirmed-plan TTL checks no longer
query a terminated process's unavailable token or image path just to prove exit.
Invalid-PID absence is accepted only when opening the process; unknown/read-time
errors remain blocking. `STILL_ACTIVE` is unconfirmed. No error alone proves exit,
no recovery latch is cleared, and no process is launched or terminated by this
check. Focused fake-kernel tests cover ended handles and unavailable SID queries.

## 2026-10-20 — Reconcile a late unique probe result

Recovery can reconcile one recorded `probe_pending` operation after its launcher
has exited and its exact unique result arrives late. It verifies the recorded
main process identity, configured executable, user, ready native main window,
unchanged checkpoint and token-bound result. A live/unknown launcher or changed
main session remains blocked. No new Stop/probe is dispatched, no manual-stop
claim is introduced, and native PCB transaction idle is not certified. PID-only
historical launcher creation identity remains explicitly unverified.

## 2026-10-20 — Probe a verified normal restart after a pending old probe

After a completed token-bound probe belongs to a now-ended main session, recovery
can validate a separately recorded successful `start_altium` identity and run one
new unique probe. The previous exact main must have exited; the old launcher must
have exited; the new configured executable/user/PID/raw creation time must match
the unchanged startup receipt and be newer. A single ready native main window is
required. Startup screens, import wizards, unknown identities and changed receipts
remain blocked. The old probe result never certifies the new session. No Stop,
manual-stop claim, automatic restart or mutation replay is added.

## 2026-10-21 — Mandatory bounded DelphiScript preflight

Added dispatch-free `lint_altium_script` and mandatory preflight in the sandbox
Run path, before native inspection, sandbox writes or launch. File declarations
are resolved once and the checked text is the text injected. Reports include
source locations, warnings, input hashes and explicit native-unverified limits.
The lexer ignores comments/quoted strings; direct typed receivers respect
routine scope and case. A versioned, source-qualified negative API catalog
rejects the observed unavailable `IPCB_Board.GetState_DesignRuleCheckerOptions`
accessor without treating .NET Board2+ metadata as DelphiScript support. Simple
declaration collisions are rejected; unknown members/helpers remain warnings.

Offline tests cover actual failure, lexical shielding, scoped shadowing,
case-insensitivity, duplicate names, file precedence/encoding, rejection before
any native/write/dispatch access, accepted single dispatch with a checked file
snapshot, and unchanged sandbox error handling. No Altium execution was used to
qualify these changes; this remains a bounded linter, not a compiler.


## 2026-10-21 — Expose curated API reference through MCP

Added offline `search_altium_api(query, limit)` for interface/member/signature and
source-title lookup. Results retain documented/unverified status, official links,
exact observed incompatibilities, match counts and explicit missing-file errors.
No native access or documentation-based allowlist is introduced. Lint reports
optionally attach matching documentation without changing their verdicts; absent
documentation does not disable negative compatibility checks. Packaging examples
now include the reference directory. Offline tests cover lookup, limits, failure
evidence, missing files, unchanged lint severity and real FastMCP registration and
call transport without importing the native application server.

## 2026-11-10 — Guarded mechanical primitive control

Added native inspection and exact-key deletion for free NPTH pads and named straight-edge keepout regions. Batch preflight, ownership/path/process guards and absence readback gate saving. Offline tests pass; native qualification is tracked in dev/PCB_MECHANICAL_CONTROL.md.


## 2026-11-11 — Preserve Situs drafts and verified ECAD workflow knowledge

The separate preservation commit `461e9c6` records the previously uncommitted Situs lifecycle, generated-native-command, routing-rule and saved-DRC modules, isolated probes, required JSON dependencies and offline evidence. All 61 focused tests pass; PowerShell syntax parsing passes. Native routing/rule execution is not certified. The menu module uses Windows UI and is not an ordinary-work fallback under VYBRO policy. Exact old Sandbox and stackdump bytes are archived without resetting the live originals.

The verified D44 native implementation is already in `5c39bb4`, `2764b26` and `ef0fefb`. `docs/situs-reference/pcb-native-lessons-20261111.md` records timeout reconciliation, locale-safe model import, model identity-based persistence, native geometry checks, actual-repour connectivity gates and explicit DRC/Texts6 qualification limits. This documentation change dispatches no application operation.
