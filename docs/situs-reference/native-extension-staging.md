# Native Situs extension staging

`stage_situs_native_extension(action, expected_plan_sha256="", operation_id="")`
is a filesystem-only public MCP tool. It stages the reviewed read-only qualification
prototype; it does not provide full native Situs support.

Call `inspect` first. The result identifies the configured `X2.exe/System`,
the three bundled sources, their exact sizes and SHA-256 hashes, target absence,
and a `plan_sha256`. No caller-supplied installation path or binary is accepted.

Call `install` with that exact `expected_plan_sha256`. The tool refreshes the
plan and refuses stale evidence or any existing target. Its fixed payload is:

- `dev/native_situs/build/NativeSitusReadOnly.dll`
- `dev/native_situs/NativeSitusReadOnly.ins`
- `dev/native_situs/NativeSitusReadOnly.rcs`

The installer pins their reviewed hashes in source; a rebuilt or missing payload
requires a new review. Deployments that expose this optional tool must include
these artifacts at their stated relative paths. Missing artifacts fail closed.

Each target uses exclusive creation and exact readback. An operation ledger under
the configured exchange directory records intent, progress and installed hashes.
An optional `operation_id` is 32 lowercase hexadecimal characters. Empty creates
a fresh ID; an existing ID is refused and never replays installation. Partial
exceptions trigger rollback only of unchanged file identities and exact bytes
created by that operation. Changed or competing files are preserved and reported.
A process crash may leave an incomplete ledger and files: inspect the evidence;
do not delete files or retry automatically.

Both the shared bridge lock and cross-process emergency claim cover hashing and
file operations. Work runs in a background thread; caller cancellation retains
both locks until that worker completes. The tool does not request privileges,
replace files, edit the registry, copy dependencies, launch/restart Altium, dispatch
scripts, or change a PCB.

Successful `installed: true` means that the three files were staged and verified.
It does not prove discovery, loading, command registration, or native qualification.
Restart is separate and must follow an inventory of unsaved documents. The legacy
System placement mechanism is a candidate for a controlled load test; current
version compatibility must still be observed.

Validation: `python -B -m unittest server.tests.test_native_situs_install -v`.
Filesystem tests use temporary fake application directories. Public-wrapper tests
register the actual decorated function with FastMCP without importing the server
entry point or opening an Altium transport.
