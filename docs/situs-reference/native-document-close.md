# Close one clean non-active PCB document

`close_clean_altium_document` calls the documented native `Client.CloseDocument`
through one fixed standalone script. It is intended to remove an obsolete test
PCB from the loaded session before the ordinary workspace save on exit. It does
not remove projects, edit files, save documents or modify startup preferences.

The caller identifies the exact active PCB, a different target PCB, configured
process PID and ft: creation token. The service pins the process and holds the
shared command lock and cross-process claim through bounded completion, including
cancellation. The fixed native program requires zero Client/PCB process depths,
a stable active PCB and two complete inventories. Exactly one target must be
loaded, kind PCB, unmodified and non-active. All checks precede the sole close.

Two fresh inventories verify target absence and unchanged module/path/kind/
Modified descriptors and counts for other documents. This is not a byte-level
proof of other documents' contents. Native attempt/result and service receipts
are retained; no uncertain operation is retried. An unresolved result retains
the recovery latch. A completed close does not alone establish that future
workspace restoration is repaired; check the saved restore entries and a later
normal start separately.

This command does not cancel a modal import wizard. It refuses dispatch when a
modal is present. No window-button action, keyboard input, focus operation or
Windows UI Automation is included. Compile/runtime qualification of the fixed
DelphiScript remains separate from the offline builder/service tests.
