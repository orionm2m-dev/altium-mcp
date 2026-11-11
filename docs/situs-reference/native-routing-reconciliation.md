# Native routing reconciliation and background executor recovery

Qualified on Altium Designer 26.9.1 during the lower PCB recovery on 2026-11-11.

`inspect_pcb_routing` reads exact free track/via geometry using the same request
format as `set_pcb_routing`. The operation fields describe expected final state;
they never cause an addition or deletion. It requires the exact already-current
PCB and both native process depths zero. Ambiguous or foreign ownership fails.
The result distinguishes existence counts, observed geometry/net/lock state and
expected-state matches. It neither opens/focuses a document, repairs a transaction,
rebuilds connectivity nor saves. A nonexistent target was refused and all 17
objects of an interrupted batch were successfully reconciled. Disk hash stayed
unchanged during inspection. A later save, repour and independent file audit were
separate operations. This is not DRC or general runtime qualification.

Initial `recover_script_executor` can now dispatch native EditScript:Stop from
the background after validating one configured process, creation time, user,
exact responsive main window and absence of dialogs. Identity is checked again
after Stop and the unique read-only probe. It does not activate windows or send
input. Existing unresolved operations remain latched.

One late Stop launcher exit can authorize only a fresh read-only probe when its
exact recorded main process is unchanged and the launcher is absent or exited.
PID absence is explicitly not a successful-exit or command-delivery proof. The
matching unique probe is required to qualify executor availability; it does not
prove PCB transaction idleness. One native PostProcess was separately applied
after confirming the abandoned PCB depth was exactly 1 and client depth 0.

Runtime recovery used one Stop, one probe and read-only reconciliation of its
late result. No repeated Stop, manual-Stop assertion, GUI input, process kill,
mutation replay or implicit save occurred. The original modal Error still
required the owner's OK; this change does not add native dialog dismissal.

Offline: 129 recovery/guard tests passed on a diagnostic rerun; two initial
legacy-test failures did not reproduce individually or on that rerun. Their
cause remains unestablished. The routing/primitive contract suite passed 35
tests. Runtime evidence is retained with the project ECAD checkpoint.
