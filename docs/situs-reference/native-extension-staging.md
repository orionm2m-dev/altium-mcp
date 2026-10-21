# Native Situs extension staging

`stage_situs_native_extension(action, expected_plan_sha256="", operation_id="", bundle="readonly")`
is a filesystem-only public MCP tool. It stages one fixed reviewed native bundle;
staging alone does not provide full native Situs support.

Call `inspect` first. The result identifies the configured `X2.exe/System`,
the three bundled sources, their exact sizes and SHA-256 hashes, target absence,
and a `plan_sha256`. No caller-supplied installation path or binary is accepted.

Call `install` with that exact `expected_plan_sha256` and the same `bundle`.
The selector is part of the hashed plan; a plan for another bundle is refused.
The tool refreshes the plan and refuses stale evidence or any existing target.
The default `readonly` payload is:

- `dev/native_situs/build/NativeSitusReadOnly.dll`
- `dev/native_situs/NativeSitusReadOnly.ins`
- `dev/native_situs/NativeSitusReadOnly.rcs`

The explicit `native_control` payload is:

- `dev/native_situs_next/build/NativeSitusControl.dll`
- `dev/native_situs_next/NativeSitusControl.ins`
- `dev/native_situs_next/NativeSitusControl.rcs`

The two sets have distinct target names and may coexist. Selecting a second
bundle never authorizes replacement of an existing module. No other selector,
caller-supplied source, destination, artifact name or hash is accepted.

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
System placement mechanism was observed with the read-only prototype on AD26.9.1;
loading and command behavior of a different bundle still require their own
qualification. See [native module scope](native-module-contract.md).

Validation: `python -B -m unittest server.tests.test_native_situs_install -v`.
Filesystem tests use temporary fake application directories. Public-wrapper tests
register the actual decorated function with FastMCP without importing the server
entry point or opening an Altium transport.
