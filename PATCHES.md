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
