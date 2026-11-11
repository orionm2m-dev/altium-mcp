# Situs worktree preservation — 2026-11-11

The owner explicitly requested preserving these separate, previously uncommitted changes along with the ECAD handoff. This commit preserves development work; it does not certify native execution or activate new routing on the working PCB. Baseline HEAD was ef0fefba3d34907519339c21c4c7ee39e2776a36 on schematic-editing.

## Included work and limits

- `server/situs_lifecycle.py`: process/document-bound jobs, lifecycle and receipt handling. Callback tests are mocked; production/native qualification remains open.
- `server/situs_native.py`: guarded generated DelphiScript for direct autorouting scopes. Generated source is not proof of execution in Altium. This prototype is separate from the existing production native Situs service.
- `server/routing_rules.py`, the routing contract and enum catalog: batch validation and rule specifications. The candidate Pascal implementation stays isolated at `dev/probes/routing_rules/pcb_document.candidate.pas`; it is not the production `pcb_document.pas`.
- `server/saved_drc_options.py`: reads saved PCB DRC settings. Saved flags and limits do not prove live enabled coverage or a complete native DRC run.
- `dev/probes/open_documents`, `routing_rules`, `situs_native`: templates, static fixtures and qualification receipts. In particular the open-document probe is PREPARED_NOT_DISPATCHED. Metadata and lint success do not prove DelphiScript interface binding.
- `server/situs_route_menu.py` and the narrow change to `situs_uia_worker.ps1`: Windows UI Automation prototype for Route/Auto Route menu inspection/actions. Despite terminology in old source comments, this is UI control, not a native Altium design API. Under the VYBRO owner's policy it MUST NOT be used for ordinary work or wired as an automatic fallback. The emergency exception for a verified blocking service error does not authorize opening/running this routing menu.

The five new Python modules above plus the menu module were not wired into public handlers by this preservation task. No native routing, menu action, rule mutation or PCB save was performed here. Preserving source is not approval to execute every capability it contains.

## Verification

Six focused unittest modules passed: **61 tests, 0 failures, 0 errors, 0 skipped**. PowerShell parser check of `situs_uia_worker.ps1` passed with zero syntax errors; the worker was not executed. Full logs and JSON receipts are in `dev/probes/session_archive_20261111/`. These checks validate offline behavior/source contracts only. Native qualification and integration remain separate tasks on protected copies.

The JSON enum catalog, probe manifest and qualification receipts are explicitly tracked because the repository-wide `*.json` ignore would otherwise omit dependencies required to reproduce the tests. They have not been regenerated or declared runtime evidence.

## Runtime artifacts retained separately

`dev/probes/session_archive_20261111/sandbox_view3d.pas.txt` is an exact archived copy of the previously modified shared Sandbox script. It belongs to the prior ECAD 3D visibility qualification, not Situs. The text suffix prevents presenting it as the active script. The live `server/SandboxScript/Sandbox.pas` is intentionally not reset or included as a production code change.

`bash_stackdump.txt` preserves the existing `server/bash.exe.stackdump`; its cause is unknown. The original is left untouched. These two original runtime files may therefore still appear dirty/untracked after the commit; their exact bytes are preserved here and in the project snapshot.

The project-side inventory, SHA256 values, pre-commit patch and source backup are under `Info/Research/PCB_D44_ECAD_20261110/handoff_20261111/`. Refer to the project handoff for the actual current PCB checkpoint; none of these prototypes supersedes it.
