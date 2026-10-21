# Development harness

For official declarations and language boundaries, start with the local
[Altium scripting API reference](../docs/altium-api-reference/README.md).
Its documented catalog is separate from native qualification and the lint's
negative compatibility evidence.

## Safety rules

- **Never save anything on the N: drive.** It hosts the shared corporate
  symbol/footprint libraries and the component database. Reading is fine
  (config, symbol scans, SELECT queries); writing is not.
- A script that opens a library to read a symbol makes that library the
  *current document*. Never register or add objects while a library is
  current - verify the target is a schematic (`ObjectID` 32 = `eSchDoc`;
  33 = `eSchLib`) and abort otherwise. `place_part_from_spec.pas` does this.

Tools for developing new Altium API functionality. Not part of the MCP server.

## Why

Altium has no headless test mode for DelphiScript, and its failure modes are
hostile to automation:

- A runtime error **kills the script and wedges the script executor** - every
  later `RunScript` silently does nothing until Altium restarts.
- Plain `try/except` does **not** catch runtime conversion errors; the error
  escapes to the enclosing block and still kills the script.
- Compile/runtime errors surface only as modal dialogs, with no log or
  traceback, and their text is often unreadable programmatically.

## Recovering a wedged executor

Use the public `recover_script_executor` MCP tool. It holds the shared command
lock, tracks the direct Stop launcher, and proves availability using a unique
read-only probe. It does not close dialogs, kill X2, save production documents,
or replay the failed operation. See the [public recovery contract](../README.md#scripting--development)
for time limits, the persistent blocked checkpoint, and the manual Ctrl+F3 path.
A matching probe proves executor availability only, not a balanced PCB transaction.

Visible `TShowException` native crash reporters block Stop/probe and normal bridge/
sandbox dispatch without accepting a dialog or submitting a report. Capture retains
available exception text; a missing readable label is reported explicitly. Visibility
is rechecked after reading children so a hidden form is not reported as a blocker.
The presence of windowless old command clients is an independent blocking condition.

Run only the explicit offline guard modules (all native APIs are mocked):

```sh
python -B -m unittest server.tests.test_script_guard server.tests.test_script_recovery server.tests.test_sandbox_script_guard server.tests.test_screenshot_dispatch
```

Do not use a broad `test_*script*.py` discovery pattern for this check: it also
selects the interactive Altium integration harness. Offline results do not qualify
the live native executor or establish that the cause of a DLL crash is fixed.

The older `dev/unwedge.py` / runner recovery code is historical development
support, not the bounded public recovery path. Its broad dialog dismissal,
blind launch delay and optional process restart must not be used as a fallback
for this tool. Native recovery still needs a separate public-tool qualification;
offline mock tests do not establish that Altium accepted the command.

## Screenshots are a standing TOP priority

**SOLVED - use dev/render_sheet.py.** Headless render via Select All +
Sch:Copy: Altium puts a properly framed metafile on the clipboard (the same
mechanism that pastes schematics into Word); the script extracts and
rasterizes it to PNG. No window, no foreground, no paint state - always
CURRENT content. Overwrites the user clipboard; avoid mid copy-paste.

    python dev/render_sheet.py <sheet-path> <out.png> [width]

Everything below documents why window capture cannot work here and remains
only for capturing Altium UI state (dialogs, panels).

If schematic screenshots break, fixing them comes before whatever else is in
flight. They are the only check that sees TEXT OVERLAP - net labels,
parameters and designators colliding with wires and each other - which every
geometric audit here misses, and which otherwise puts the user back in the
review loop.

The verified recipe (`capture_window.capture_document`):

1. `Client.ShowDocument` the target sheet, then zoom to fit - this switches
   the visible tab AND retitles the owning frame to the document name.
2. Capture the frame whose TITLE CONTAINS THE DOCUMENT NAME - never the first
   "Altium Designer" window found. Several frames exist; the first match
   repeatedly produced convincing screenshots of the WRONG document while
   every API said the right one was current.

**Root cause, measured:** Altium renders a schematic view ONLY when it has
actually been displayed on screen. A document shown via Client.ShowDocument
while Altium is a background window gets its tab selected and the frame
retitled, but the view surface is never painted - PrintWindow then returns
either the previous document's pixels (convincing and WRONG - check the title
block inside the drawing, it names the file) or black. Every API-level signal
(GetCurrentSchDocument, tab highlight, frame title) says the right document is
current while the pixels are stale.

Getting a real paint requires real activation, and Windows refuses
SetForegroundWindow to background processes. Measured failures:
AttachThreadInput trick (refused), minimize/restore (detaches the document
into a floating window - do NOT do this, it mangles the user's layout),
SetWindowPos TOPMOST (window visible but canvas still unpainted),
SendInput click (foreground still refused).

Untried, ranked for next session:
1. Client:TileAllOpenDocuments - tiling may force every open view to paint
   at once; capture the frame afterwards.
2. Alt-key press via SendInput immediately before SetForegroundWindow (the
   classic foreground-lock release), then paint + capture.
3. Keep exactly ONE schematic open (close others via Modified:=False +
   CloseDocument - verified to work) so the only view MUST be the target;
   combine with 1 or 2.

Also: never name a generated free document like a project sheet. Fresh Altium
restarts number free docs from Sheet1, colliding with the project's
Sheet1..13 - GetDocumentByPath then resolves ambiguously. Dead ends measured
earlier: RedrawToDC blank at all nine combos; TMetafile records outside its
frame.

## Diagnosing failures

1. **Step log** (primary): the last logged line tells you which statement
   died - the one right after it.
2. **Altium window capture** (`dev/capture_window.py`): uses `PrintWindow`,
   which renders the window even when obscured or the desktop is not
   rendering. Screen-scraping (`CopyFromScreen`) returns blanks on
   remote/disconnected sessions - do not use it. When the script editor tab
   is active, its capture shows the paused line highlighted.
3. Silent debugger pauses produce **no dialog at all**; compile errors do.

## Gotcha: the sandbox is standalone

Experiments cannot use constants/helpers defined in the production units
(`REPLACEALL`, `TrimJSON`, `AddJSONProperty`, ...) - those live in
`server/AltiumScript/*.pas`, which this project does not include. Declare
what you need in `Sandbox.pas`. An undefined constant compiles fine and then
kills the script at runtime.

## sandbox_runner.py

Runs an experiment body inside `dev/sandbox/`, a **standalone script project**
that is deliberately separate from the production `Altium_API` project - a
broken experiment can never break the working MCP tooling.

```
python dev/sandbox_runner.py my_experiment.pas [timeout] [--auto-restart]
```

- Injects the body between the BEGIN/END EXPERIMENT markers of `Sandbox.pas`
- `SandboxLog()` flushes to disk after **every** call, so if the script dies
  silently the last logged step identifies the statement that killed it
- Detects and closes both dialog classes Altium uses (`#32770` task dialogs
  and Delphi `TMessageForm`), reporting their text when readable
- `--auto-restart`: on a wedge, force-restarts Altium (kills X2.EXE - unsaved
  Altium work is lost), waits for readiness, and retries the experiment once

Experiment body rules: assign findings to `ResultText`; Pascal has no inline
declarations, so reuse the scratch variables declared in `Sandbox.pas`
(`S1..S5`, `I1..I5`, `B1`, `Obj1..Obj7`, `List1`, `List2`, `IntMan`, `DbDoc`)
or add more there.

**These shared scratch variables are a hazard**, not a convenience. Reusing
one as a loop counter after storing a coordinate in it produced a silently
wrong anchor twice in one session. Track what each holds across a whole
experiment, and prefer distinct names for values that must survive a loop.

Put risky reads (enum properties, anything that might not exist on this
install) at the **end** of an experiment. `SandboxLog` flushes on every call,
so a wedge there still leaves everything useful already on disk.

## Findings

- [`DBLIB_FINDINGS.md`](DBLIB_FINDINGS.md) - placing database-linked
  components so they are indistinguishable from a GUI placement.
- [`SCHEMATIC_CONVENTIONS.md`](SCHEMATIC_CONVENTIONS.md) - **read before
  generating any schematic.** Pin connection points, tap gaps, ground length,
  rail placement, net-label attachment, parameter text, mirroring, and the
  verification rules. Every entry is a mistake that was made and corrected.
- [`SHEET_DUPLICATION.md`](SHEET_DUPLICATION.md) - copying a schematic sheet.
  The document iterator recurses, so `Container.ObjectId = 32` (not
  `ObjectId`) is what identifies the objects a sheet actually owns.


### Native qualification: custom checkbox state (2026-10-21)

In Altium Designer 26.9.1, the Situs `Rip-up Violations After Routing`
control was observed as `TXPCheckBox`, style low bits `0`. One exact
control-client down/up gesture visibly changed it from unchecked to checked.
`BM_GETCHECK` nevertheless returned `0` both before and after. A zero reply
must therefore **not** be interpreted as unchecked for this custom control.
The gesture was not repeated; the checked state was verified from a fresh
screenshot before starting Route All.

`control_situs_setup.inspect` now exposes `raw_bm_getcheck`,
`state_known: false` and `state_error`, with no `checked` Boolean for that
class/style. `enable_rip_up` refuses it before input, including repeated
requests. The prior custom gesture implementation is removed. A future
implementation needs a qualified native state getter or an explicit fresh
visual receipt bound to the exact process, dialog and control, with a new
post-action image; a blind retry is unsafe because it can toggle the option off.
Standard checkbox behavior remains supported. DRC custom checkbox inspection
likewise reports an unqualified raw reply and blocks limit/OK operations that
claim verification of every checkbox.

Evidence retained in the VYBRO project under
`Info/Research/Situs_qualification_2026-10-21/`:
`rules_setup_capture/01_get_screenshot.1.png` (before) and
`rules_route_all_started/00_get_screenshot.1.png` (checked after the single
gesture). These are external qualification artifacts, not API documentation.
The DRC numeric limit had separately been verified as 5000 after reopening;
that numeric result does not imply that custom checkbox state was machine-read.
