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
