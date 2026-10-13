// multiboard.pas
// Multi-board Design projects. The Multi-board Schematic editor has no
// scripting interface, so the server writes the document itself; these
// commands give it what only Altium knows and run the checks only Altium can:
//   * get_project_connectors - compile a child project and report its
//                              connectors (parameter System = Connector) with
//                              the compiled net and the unique id of every pin
//   * run_multiboard_erc     - reload a schematic from disk, run its ERC and
//                              report the Messages panel
//   * open_project_group     - open a .DsnWrk in place of the current group
// JSONStr / JSONBool and the JSON list helpers come from schematic_edit.pas
// and json_utils.pas.

// ---------------------------------------------------------------------------
// get_project_connectors: compile a project and report every component that
// carries the parameter System = Connector, with what a Multi-board Schematic
// keeps for its module entries: the component's unique id, physical path and
// owner document, and for every pin its number, name, compiled net and unique
// id. The compiled model does not expose a pin's unique id, so it is read
// from the pin on its source sheet (ISch_Pin.UniqueId). Nothing is modified.
// ---------------------------------------------------------------------------

function SchComponentByUniqueId(SchDoc: ISch_Document; UniqueId: String): ISch_Component;
var
    Iter : ISch_Iterator;
    Comp : ISch_Component;
begin
    Result := nil;
    Iter := SchDoc.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
    Comp := Iter.FirstSchObject;
    while Comp <> nil do
    begin
        if Comp.UniqueId = UniqueId then Result := Comp;
        Comp := Iter.NextSchObject;
    end;
    SchDoc.SchIterator_Destroy(Iter);
end;

// Collects the designator and the unique id of the pins of the component
// with the given unique id on the sheet DocPath, in two parallel lists. Only
// the pins of the placed part in its current display mode are taken: a symbol
// also holds the pins of its other parts and display modes.
procedure CollectPinUniqueIds(DocPath: String; CompUniqueId: String; Designators, Ids: TStringList);
var
    Opened : IServerDocument;
    SchDoc : ISch_Document;
    Comp   : ISch_Component;
    Iter   : ISch_Iterator;
    Pin    : ISch_Pin;
begin
    // A sheet opened here is closed again, so the open documents stay as found.
    Opened := nil;
    if not Client.IsDocumentOpen(DocPath) then
        Opened := Client.OpenDocument('SCH', DocPath);
    SchDoc := SchServer.GetSchDocumentByPath(DocPath);
    if SchDoc <> nil then
    begin
        Comp := SchComponentByUniqueId(SchDoc, CompUniqueId);
        if Comp <> nil then
        begin
            Iter := Comp.SchIterator_Create;
            Iter.AddFilter_ObjectSet(MkSet(ePin));
            Pin := Iter.FirstSchObject;
            while Pin <> nil do
            begin
                if ((Pin.OwnerPartId = Comp.CurrentPartId) or (Pin.OwnerPartId <= 0)) and
                   (Pin.OwnerPartDisplayMode = Comp.DisplayMode) then
                begin
                    Designators.Add(Pin.Designator);
                    Ids.Add(Pin.UniqueId);
                end;
                Pin := Iter.NextSchObject;
            end;
            Comp.SchIterator_Destroy(Iter);
        end;
    end;
    if Opened <> nil then Client.CloseDocument(Opened);
end;

// The unique id collected for a pin designator. The match is exact, and each
// record is handed out once, so pins sharing a designator get their ids in
// sheet order.
function TakePinUniqueId(Designators, Ids: TStringList; Designator: String): String;
var
    i : Integer;
begin
    Result := '';
    for i := 0 to Designators.Count - 1 do
        if Designators[i] = Designator then
        begin
            Result := Ids[i];
            Designators[i] := #1;
            Exit;
        end;
end;

function ProjectConnectorsReport(ProjectPath: String; OutPath: String): String;
var
    Prj   : IProject;
    Doc   : IDocument;
    Comp, Pin, Param;
    IsConnector : Boolean;
    Conns, Pins, PinNames, PinIds, CompProps, Props, OutList : TStringList;
    i, j, k : Integer;
    NetName : String;
begin
    if not FileExists(ProjectPath) then
    begin
        Result := 'ERROR: project file not found: ' + ProjectPath;
        Exit;
    end;
    Prj := GetWorkspace.DM_GetProjectFromPath(ProjectPath);
    if Prj = nil then Prj := GetWorkspace.DM_OpenProject(ProjectPath, True);
    if Prj = nil then
    begin
        Result := 'ERROR: cannot open project ' + ProjectPath;
        Exit;
    end;
    Prj.DM_SetAsCurrentProject;
    Prj.DM_Compile;
    // Without compiled documents there is nothing to report; saying "no
    // connectors" instead would make the caller drop the ones it has.
    if Prj.DM_PhysicalDocumentCount = 0 then
    begin
        Result := 'ERROR: project has no compiled documents: ' + ProjectPath;
        Exit;
    end;

    Conns := TStringList.Create;
    for i := 0 to Prj.DM_PhysicalDocumentCount - 1 do
    begin
        Doc := Prj.DM_PhysicalDocuments(i);
        for j := 0 to Doc.DM_ComponentCount - 1 do
        begin
            Comp := Doc.DM_Components(j);
            IsConnector := False;
            for k := 0 to Comp.DM_ParameterCount - 1 do
            begin
                Param := Comp.DM_Parameters(k);
                if (Param.DM_Name = 'System') and (Param.DM_Value = 'Connector') then IsConnector := True;
            end;
            if not IsConnector then Continue;

            PinNames := TStringList.Create;
            PinIds := TStringList.Create;
            CollectPinUniqueIds(Doc.DM_FullPath, Comp.DM_UniqueIdName, PinNames, PinIds);
            Pins := TStringList.Create;
            for k := 0 to Comp.DM_PinCount - 1 do
            begin
                Pin := Comp.DM_Pins(k);
                NetName := Pin.DM_FlattenedNetName;
                if NetName = '?' then NetName := '';
                Pins.Add('{"number": ' + JSONStr(Pin.DM_PinNumber) + ', "name": ' + JSONStr(Pin.DM_PinName) +
                         ', "net": ' + JSONStr(NetName) +
                         ', "unique_id": ' + JSONStr(TakePinUniqueId(PinNames, PinIds, Pin.DM_PinNumber)) + '}');
            end;
            CompProps := TStringList.Create;
            AddJSONProperty(CompProps, 'designator', Comp.DM_PhysicalDesignator);
            AddJSONProperty(CompProps, 'comment', Comp.DM_Comment);
            AddJSONProperty(CompProps, 'unique_id', Comp.DM_UniqueId);
            AddJSONProperty(CompProps, 'physical_path', Comp.DM_PhysicalPath);
            AddJSONProperty(CompProps, 'document', Doc.DM_FullPath);
            CompProps.Add(BuildJSONArray(Pins, 'pins', 2));
            Conns.Add(BuildJSONObject(CompProps, 1));
            CompProps.Free;
            Pins.Free;
            PinIds.Free;
            PinNames.Free;
        end;
    end;

    Props := TStringList.Create;
    AddJSONBoolean(Props, 'success', True);
    AddJSONProperty(Props, 'project', Prj.DM_ProjectFullPath);
    Props.Add(BuildJSONArray(Conns, 'connectors', 1));
    OutList := TStringList.Create;
    OutList.Text := BuildJSONObject(Props);
    OutList.SaveToFile(OutPath);
    Result := '{"success": true, "connectors": ' + IntToStr(Conns.Count) + ', "file": ' + JSONStr(OutPath) + '}';
    OutList.Free;
    Props.Free;
    Conns.Free;
end;

// ---------------------------------------------------------------------------
// run_multiboard_erc: reload a Multi-board Schematic from disk, run the ERC
// the Design » Run ERC menu item runs, and report the Messages panel.
// ---------------------------------------------------------------------------
function MultiboardErcReport(DocPath: String; OutPath: String): String;
var
    Doc  : IServerDocument;
    MM, Msg;
    Msgs, Props, OutList : TStringList;
    i, Errors, Warnings : Integer;
begin
    if not FileExists(DocPath) then
    begin
        Result := 'ERROR: document not found: ' + DocPath;
        Exit;
    end;
    // Close and reopen so that a file rewritten on disk is what gets checked;
    // edits that exist only in memory would be lost, so they stop the run.
    Doc := Client.GetDocumentByPath(DocPath);
    if (Doc <> nil) and Doc.Modified then
    begin
        Result := 'ERROR: document has unsaved changes, save or discard them first: ' + DocPath;
        Exit;
    end;
    if Doc <> nil then Client.CloseDocument(Doc);
    Doc := Client.OpenDocument('SdDoc', DocPath);
    if Doc = nil then
    begin
        Result := 'ERROR: cannot open ' + DocPath;
        Exit;
    end;
    Client.ShowDocument(Doc);
    Doc.Focus;
    MM := GetWorkspace.DM_MessagesManager;
    MM.ClearMessages;
    ResetParameters;
    AddStringParameter('ObjectKind', 'Document');
    RunProcess('WorkspaceManager:RunERC');

    Msgs := TStringList.Create;
    Errors := 0;
    Warnings := 0;
    for i := 0 to MM.MessagesCount - 1 do
    begin
        Msg := MM.Messages(i);
        Msgs.Add('{"class": ' + JSONStr(Msg.MsgClass) + ', "text": ' + JSONStr(Msg.Text) +
                 ', "source": ' + JSONStr(Msg.Source) + ', "document": ' + JSONStr(ExtractFileName(Msg.Document)) + '}');
        // '[Error]' and '[Fatal Error]' both count as errors
        if Pos('Error]', Msg.MsgClass) > 0 then Errors := Errors + 1
        else if Pos('[Warning]', Msg.MsgClass) > 0 then Warnings := Warnings + 1;
    end;
    Props := TStringList.Create;
    AddJSONBoolean(Props, 'success', True);
    AddJSONProperty(Props, 'document', DocPath);
    AddJSONInteger(Props, 'errors', Errors);
    AddJSONInteger(Props, 'warnings', Warnings);
    Props.Add(BuildJSONArray(Msgs, 'messages', 1));
    OutList := TStringList.Create;
    OutList.Text := BuildJSONObject(Props);
    OutList.SaveToFile(OutPath);
    Result := '{"success": true, "messages": ' + IntToStr(Msgs.Count) + ', "file": ' + JSONStr(OutPath) + '}';
    OutList.Free;
    Props.Free;
    Msgs.Free;
end;

// ---------------------------------------------------------------------------
// open_project_group: open a project group (.DsnWrk) in place of the one in
// the workspace and list the projects it brought in.
// ---------------------------------------------------------------------------

// Paths of the open documents of all projects that have unsaved changes.
function ModifiedDocumentPaths: String;
var
    Prj  : IProject;
    Doc  : IServerDocument;
    DocPath : String;
    i, j : Integer;
begin
    Result := '';
    for i := 0 to GetWorkspace.DM_ProjectCount - 1 do
    begin
        Prj := GetWorkspace.DM_Projects(i);
        for j := 0 to Prj.DM_LogicalDocumentCount - 1 do
        begin
            DocPath := Prj.DM_LogicalDocuments(j).DM_FullPath;
            Doc := Client.GetDocumentByPath(DocPath);
            if (Doc <> nil) and Doc.Modified then
                Result := Result + DocPath + '; ';
        end;
    end;
end;

// True when the group in the workspace is a saved one whose list of projects
// no longer matches its file. Script projects count: Altium adds the project
// of every script it runs to the group.
function ProjectGroupChanged: Boolean;
var
    Lines, Listed : TStringList;
    GroupPath, PrjName : String;
    i, OpenCount : Integer;
begin
    Result := False;
    GroupPath := GetWorkspace.DM_WorkspaceFullPath;
    if not FileExists(GroupPath) then Exit;
    Lines := TStringList.Create;
    Listed := TStringList.Create;
    Lines.LoadFromFile(GroupPath);
    for i := 0 to Lines.Count - 1 do
        if Copy(Lines[i], 1, 12) = 'ProjectPath=' then
            Listed.Add(UpperCase(ExtractFileName(Copy(Lines[i], 13, Length(Lines[i])))));
    OpenCount := 0;
    for i := 0 to GetWorkspace.DM_ProjectCount - 1 do
    begin
        PrjName := UpperCase(GetWorkspace.DM_Projects(i).DM_ProjectFileName);
        if PrjName = 'FREE DOCUMENTS' then Continue;
        OpenCount := OpenCount + 1;
        if Listed.IndexOf(PrjName) < 0 then Result := True;
    end;
    if OpenCount <> Listed.Count then Result := True;
    Listed.Free;
    Lines.Free;
end;

function OpenProjectGroupReport(GroupPath: String; OutPath: String): String;
var
    Prjs, Props, OutList : TStringList;
    i : Integer;
    Opened : Boolean;
    Unsaved : String;
begin
    if not FileExists(GroupPath) then
    begin
        Result := 'ERROR: project group not found: ' + GroupPath;
        Exit;
    end;
    // Replacing the group closes every project. With unsaved documents, or a
    // saved group that has changed, Altium asks what to do in a dialog that
    // nothing could answer, so both stop the run.
    Unsaved := ModifiedDocumentPaths;
    if Unsaved <> '' then
    begin
        Result := 'ERROR: unsaved changes, save or discard them first: ' + Unsaved;
        Exit;
    end;
    if ProjectGroupChanged then
    begin
        Result := 'ERROR: the project group in the workspace has changed since it was saved, save or discard it in Altium first: ' +
                  GetWorkspace.DM_WorkspaceFullPath;
        Exit;
    end;
    ResetParameters;
    AddStringParameter('ObjectKind', 'Workspace');
    AddStringParameter('FileName', GroupPath);
    RunProcess('WorkspaceManager:OpenObject');

    Opened := UpperCase(GetWorkspace.DM_WorkspaceFullPath) = UpperCase(GroupPath);
    Prjs := TStringList.Create;
    for i := 0 to GetWorkspace.DM_ProjectCount - 1 do
        Prjs.Add(JSONStr(GetWorkspace.DM_Projects(i).DM_ProjectFileName));
    Props := TStringList.Create;
    AddJSONBoolean(Props, 'success', Opened);
    AddJSONProperty(Props, 'group', GetWorkspace.DM_WorkspaceFullPath);
    Props.Add(BuildJSONArray(Prjs, 'projects', 1));
    OutList := TStringList.Create;
    OutList.Text := BuildJSONObject(Props);
    OutList.SaveToFile(OutPath);
    Result := '{"success": ' + JSONBool(Opened) + ', "projects": ' + IntToStr(Prjs.Count) + ', "file": ' + JSONStr(OutPath) + '}';
    OutList.Free;
    Props.Free;
    Prjs.Free;
end;
