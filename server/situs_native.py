"""Dedicated Situs script/receipt adapter, independent of the shared Sandbox.

Only the application's registered, guarded executor may supply the callbacks.
This module does not import main, launch processes, or send window messages.
Generated DelphiScript remains unqualified until tested in the target Altium.
"""
import copy
import hashlib
import json
from pathlib import Path, PureWindowsPath
import time

from situs_lifecycle import CONTROL_ACTIONS, SCOPE_ACTIONS, build_start_request, normalize_binding

DIRECT_SCOPES = {"all", "selected_components", "between_selected_components"}


def process_plan(scope):
    """Installed AdvPcb.rcs command mapping, without invented target parameters."""
    if scope not in SCOPE_ACTIONS:
        raise ValueError("unknown routing scope")
    return {"process": "PCB:Autoroute", "parameters": {"Action": SCOPE_ACTIONS[scope]},
            "interaction_required": scope not in DIRECT_SCOPES,
            "target_parameters_supported": False,
            "target_binding": "exact_selected_components" if "selected_components" in scope else
                              ("whole_board" if scope == "all" else "native_interactive_picker"),
            "runtime_qualified": False}


def _identifier(value, label):
    if not isinstance(value, str) or len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{label} must be a 32-character lowercase hexadecimal id")
    return value


def _literal(value):
    if not isinstance(value, str) or any(c in value for c in "\r\n\0"):
        raise ValueError("unsafe Pascal literal")
    return "'" + value.replace("'", "''") + "'"


def _receipt_path(value):
    path = PureWindowsPath(str(value))
    if not path.is_absolute() or ".." in path.parts or path.suffix.casefold() != ".json":
        raise ValueError("receipt_path must be an absolute JSON path without traversal")
    return str(path)


def build_native_program(job, operation_id, receipt_path):
    """Build a no-save procedure; native guards run again immediately before start.

    PID/create-time/executable checks are performed by the Python adapter. They
    are not falsely presented as a native check: the native script verifies the
    exact active full document path and board object identity before and after.
    Interactive scopes require a separately qualified picker driver and are
    refused here. Selection is read/verified, never silently changed.
    """
    request = job["request"]
    request = build_start_request(request["binding"], request["expected_sha256"],
                                  request["scope"], request["targets"], request["options"])
    scope = request["scope"]
    if scope not in DIRECT_SCOPES:
        raise ValueError("this scope requires a qualified native interactive target driver")
    job_id = _identifier(job["job_id"], "job_id")
    operation_id = _identifier(operation_id, "operation_id")
    if operation_id != job["start_operation_id"]:
        raise ValueError("native program must match the start operation")
    metadata = dict(schema_version=1, job_id=job_id, operation_id=operation_id,
                    pcb_path=request["binding"]["pcb_path"], no_save=True,
                    pid_verified_by_native=False, completion_qualified=False)
    prefix = json.dumps(metadata, separators=(",", ":"), ensure_ascii=True)[:-1]
    selection = ""
    if scope in {"selected_components", "between_selected_components"}:
        additions = "\n".join("        Wanted.Add(" + _literal(name) + ");" for name in request["targets"])
        selection = """
    Wanted := TStringList.Create;
    try
ADDITIONS
        SelectedCount := 0;
        SelectionMatches := True;
        Iter := Board.BoardIterator_Create;
        try
            Iter.AddFilter_ObjectSet(MkSet(eComponentObject));
            Iter.AddFilter_LayerSet(AllLayers);
            Iter.AddFilter_Method(eProcessAll);
            Component := Iter.FirstPCBObject;
            while Component <> nil do
            begin
                if Component.Selected then
                begin
                    SelectedCount := SelectedCount + 1;
                    if Wanted.IndexOf(Component.Name.Text) < 0 then SelectionMatches := False;
                end;
                Component := Iter.NextPCBObject;
            end;
        finally
            Board.BoardIterator_Destroy(Iter);
        end;
        if (SelectedCount <> Wanted.Count) or not SelectionMatches then
        begin WriteSitusReceipt('guard_failed', 'selected target set changed'); Exit; end;
    finally
        Wanted.Free;
    end;
""".replace("ADDITIONS", additions)
    source = """// Dedicated Situs operation. No document open, save, selection edits, or retry.
procedure WriteSitusReceipt(Phase, ErrorText: String);
var Lines: TStringList;
begin
    Lines := TStringList.Create;
    try
        Lines.Add(PREFIX + ',"phase":"' + Phase + '","error":"' + ErrorText + '"}');
        Lines.SaveToFile(RECEIPT);
    finally
        Lines.Free;
    end;
end;

procedure Run;
const ExpectedPath = PCBPATH;
var
    Board, CurrentBoard: IPCB_Board;
    Doc: IServerDocument;
    Module: IDispatch;
    Iter: IPCB_BoardIterator;
    Component: IPCB_Component;
    Wanted: TStringList;
    SelectedCount: Integer;
    SelectionMatches: Boolean;
begin
    // Existing receipt means this operation has already been entered. Never replay.
    if FileExists(RECEIPT) then Exit;
    WriteSitusReceipt('entered', '');
    if not FileExists(ExpectedPath) then
    begin WriteSitusReceipt('guard_failed', 'PCB file missing'); Exit; end;
    if Client.CurrentView = nil then
    begin WriteSitusReceipt('guard_failed', 'no current view'); Exit; end;
    Doc := Client.CurrentView.OwnerDocument;
    if Doc = nil then
    begin WriteSitusReceipt('guard_failed', 'no current document'); Exit; end;
    if not SameText(Doc.FileName, ExpectedPath) or not SameText(Doc.Kind, 'PCB') then
    begin WriteSitusReceipt('guard_failed', 'exact PCB document mismatch'); Exit; end;
    if PCBServer = nil then
    begin WriteSitusReceipt('guard_failed', 'PCB server unavailable'); Exit; end;
    Board := PCBServer.GetPCBBoardByPath(ExpectedPath);
    CurrentBoard := PCBServer.GetCurrentPCBBoard;
    if (Board = nil) or (CurrentBoard = nil) then
    begin WriteSitusReceipt('guard_failed', 'PCB unavailable'); Exit; end;
    if Board.I_ObjectAddress <> CurrentBoard.I_ObjectAddress then
    begin WriteSitusReceipt('guard_failed', 'active PCB object mismatch'); Exit; end;
    Module := Client.ServerModuleByName('PCB');
    if Module = nil then
    begin WriteSitusReceipt('guard_failed', 'PCB module unavailable'); Exit; end;
    if (Client.ProcessControl.ProcessDepth <> 0) or (Module.ProcessControl.ProcessDepth <> 0) then
    begin WriteSitusReceipt('guard_failed', 'native process not idle'); Exit; end;
SELECTION
    WriteSitusReceipt('dispatch_attempted', '');
    ResetParameters;
    AddStringParameter('Action', ACTION);
    AddStringParameter('UpdateCaption', 'False');
    RunProcess('PCB:Autoroute');
    // A process return is not evidence of completed routing or clean DRC.
    WriteSitusReceipt('process_returned', '');
    if Client.CurrentView = nil then
    begin WriteSitusReceipt('return_unverified', 'no current view after return'); Exit; end;
    Doc := Client.CurrentView.OwnerDocument;
    if Doc = nil then
    begin WriteSitusReceipt('return_unverified', 'no current document after return'); Exit; end;
    if not SameText(Doc.FileName, ExpectedPath) then
    begin WriteSitusReceipt('return_unverified', 'active document changed after return'); Exit; end;
    CurrentBoard := PCBServer.GetCurrentPCBBoard;
    if CurrentBoard = nil then
    begin WriteSitusReceipt('return_unverified', 'active PCB missing after return'); Exit; end;
    if Board.I_ObjectAddress <> CurrentBoard.I_ObjectAddress then
    begin WriteSitusReceipt('return_unverified', 'PCB object changed after return'); Exit; end;
    if (Client.ProcessControl.ProcessDepth <> 0) or (Module.ProcessControl.ProcessDepth <> 0) then
    begin WriteSitusReceipt('return_unverified', 'native process still active after return'); Exit; end;
    WriteSitusReceipt('returned_identity_verified', '');
end;
"""
    # Replace whole tokens, avoiding substitution into user-supplied strings.
    substitutions = {"PREFIX": _literal(prefix), "RECEIPT": _literal(_receipt_path(receipt_path)),
                     "PCBPATH": _literal(request["binding"]["pcb_path"]),
                     "SELECTION": selection, "ACTION": _literal(SCOPE_ACTIONS[scope])}
    import re
    return re.sub(r"\b(PREFIX|RECEIPT|PCBPATH|SELECTION|ACTION)\b",
                  lambda match: substitutions[match.group()], source)


def read_native_receipt(path, job, operation_id):
    """Validate a per-operation receipt; missing/partial data never means done."""
    path = Path(path)
    if not path.exists():
        return {"phase": "not_observed", "router_returned": False}
    if path.stat().st_size > 65536:
        raise ValueError("oversized native receipt")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    required = {"schema_version", "job_id", "operation_id", "pcb_path", "no_save",
                "pid_verified_by_native", "completion_qualified", "phase", "error"}
    if not isinstance(data, dict) or set(data) != required or type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("invalid native receipt schema")
    if (data["job_id"] != job["job_id"] or data["operation_id"] != operation_id or
            data["pcb_path"] != job["request"]["binding"]["pcb_path"] or data["no_save"] is not True or
            data["pid_verified_by_native"] is not False or data["completion_qualified"] is not False):
        raise ValueError("native receipt identity or qualification mismatch")
    if data["phase"] not in {"entered", "guard_failed", "dispatch_attempted", "process_returned",
                            "return_unverified", "returned_identity_verified"} or not isinstance(data["error"], str):
        raise ValueError("invalid native receipt phase")
    return dict(data, router_returned=data["phase"] == "returned_identity_verified")


class SitusNativeAdapter:
    """Executable adapter for the registered public tool integration.

    Async callbacks:
      identity(binding): independently inspect PID, creation time, exe and PCB;
      execute_project(project_path, procedure, binding): pinned guarded runner;
      observe_ui(job): independent fresh UI/native evidence (not title-only);
      control_ui(job, action, operation_id): independently pinned router control.
    execute_project may block; control_ui must use a separate qualified channel,
    not the long-running bridge lock. No callback is called at import time.
    """
    def __init__(self, directory, *, identity, execute_project, observe_ui, control_ui=None,
                 preflight=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.identity = identity
        self.execute_project = execute_project
        self.observe_ui = observe_ui
        self.control_ui = control_ui
        self.preflight = preflight
        self.supports_concurrent_control = control_ui is not None

    def operation_directory(self, job, operation_id):
        return self.directory / _identifier(job["job_id"], "job_id") / _identifier(operation_id, "operation_id")

    async def _identity(self, job):
        observed = await self.identity(copy.deepcopy(job["request"]["binding"]))
        if normalize_binding(observed) != job["request"]["binding"]:
            raise ValueError("pinned process identity or exact PCB changed")
        return observed

    async def dispatch(self, job, action, operation_id):
        before = await self._identity(job)
        directory = self.operation_directory(job, operation_id)
        directory.mkdir(parents=True, exist_ok=False)  # One attempt, including uncertain failures.
        (directory / "identity_before.json").write_text(json.dumps(before), encoding="utf-8")
        if action == "start":
            path = Path(job["request"]["binding"]["pcb_path"])
            if hashlib.sha256(path.read_bytes()).hexdigest() != job["request"]["expected_sha256"]:
                raise ValueError("saved PCB changed before dispatch")
            receipt_path = directory / "native.json"
            source = build_native_program(job, operation_id, receipt_path)
            if self.preflight is None:
                raise ValueError("dedicated native script preflight callback is required")
            checked = await self.preflight(source)
            if not isinstance(checked, dict) or checked.get("success") is not True:
                raise ValueError("dedicated native script preflight rejected input")
            script = directory / "SitusOperation.pas"
            project = directory / "SitusOperation.PrjScr"
            script.write_text(source, encoding="utf-8")
            project.write_text("[Design]\nVersion=1.0\n[Document1]\nDocumentPath=SitusOperation.pas\n", encoding="utf-8")
            await self._identity(job)
            result = await self.execute_project(str(project), "SitusOperation>Run", copy.deepcopy(job["request"]["binding"]))
        else:
            if action not in CONTROL_ACTIONS or self.control_ui is None:
                raise ValueError("no qualified independent router control channel")
            result = await self.control_ui(copy.deepcopy(job), action, operation_id)
        after = await self._identity(job)
        (directory / "identity_after.json").write_text(json.dumps(after), encoding="utf-8")
        return {"execution_returned": True, "identity_before": before, "identity_after": after,
                "executor_result": result, "completion_qualified": False, "no_save": True}

    async def observe(self, job):
        await self._identity(job)
        observed = await self.observe_ui(copy.deepcopy(job))
        if not isinstance(observed, dict):
            raise ValueError("independent observer must return an object")
        operation_id = job.get("start_operation_id")
        if operation_id:
            receipt = read_native_receipt(self.operation_directory(job, operation_id) / "native.json", job, operation_id)
            observed = dict(observed, native_receipt=receipt)
            # Only report native return if independent observation also confirms
            # this job. It never supplies a missing completed/paused/running state.
            observed["router_returned"] = receipt["router_returned"]
        await self._identity(job)
        return observed
