# Changes

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
