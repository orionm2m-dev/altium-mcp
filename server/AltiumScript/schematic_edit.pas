// schematic_edit.pas
// Editing schematic sheets and projects by data, not by hand-written script:
//   * edit_schematic_sheet  - apply a pipe-delimited spec (parts from a SchLib,
//                             wires, net labels, text, parameters, deletions)
//                             to one or more sheets and save them
//   * get_schematic_sheet   - dump every object of a sheet to a JSON file
//   * compile_project       - compile a project, report violations and nets
//   * open_project          - open a project of any kind, list its documents
//   * save_documents        - save open documents by path
//
// The spec format mirrors build_circuit (same PART/WIRE/NETLABEL/... records) so
// a circuit description written for a new sheet also applies to an existing one.
// Every target document is checked to be a schematic (ObjectID 32) before any
// object is registered: opening a library makes it current, and registering into
// a library would corrupt it.

const
    SCH_DOC_ID = 32;
    SCH_LIB_ID = 33;

var
    EditSheet    : ISch_Document;
    EditDoc      : IServerDocument;
    EditLib      : ISch_Document;
    EditLibPath  : String;
    EditLastPart : ISch_Component;
    EditLastSymbol : ISch_SheetSymbol;   // target of SHEETENTRY records
    EditProject    : IProject;           // target of NEWSHEET / ADDTOPROJECT
    EditWarnings : TStringList;
    EditPinMap   : TStringList;
    EditCounts   : TStringList;   // name=value counters

function JSONStr(S: String): String;
begin
    Result := '"' + JSONEscapeString(S) + '"';
end;

function JSONBool(B: Boolean): String;
begin
    if B then Result := 'true' else Result := 'false';
end;

// The document iterator descends into components and templates; only objects
// whose container is the sheet itself (ObjectId 32) belong to the sheet.
function OwnedBySheet(Obj: ISch_GraphicalObject): Boolean;
var
    Owner : IDispatch;   // the sheet (ISch_Document) or a component - not a graphical object
begin
    Result := False;
    Owner := Obj.Container;
    if Owner <> nil then Result := (Owner.ObjectId = SCH_DOC_ID);
end;

procedure EditWarn(Msg: String);
begin
    EditWarnings.Add('"' + JSONEscapeString(Msg) + '"');
end;

procedure EditCount(Name: String);
var
    Idx : Integer;
begin
    Idx := EditCounts.IndexOfName(Name);
    if Idx < 0 then
        EditCounts.Add(Name + '=1')
    else
        EditCounts[Idx] := Name + '=' + IntToStr(StrToInt(EditCounts.ValueFromIndex[Idx]) + 1);
end;

function MilsPoint(XMils, YMils: Integer): TPoint;
begin
    Result := Point(MilsToCoord(XMils), MilsToCoord(YMils));
end;

function FieldInt(Rec: String; Index: Integer; Fallback: Integer): Integer;
var
    S : String;
begin
    S := GetFieldFromPipeString(Rec, Index);
    if S = '' then Result := Fallback else Result := StrToInt(S);
end;

// Open (or focus) a schematic sheet and make it the edit target.
function EditOpenSheet(Path: String): Boolean;
begin
    Result := False;
    // OpenDocument on a path that does not exist hands back a NEW empty sheet,
    // and every record that follows would quietly land there.
    if not FileExists(Path) then
    begin
        EditWarn('sheet file not found: ' + Path);
        Exit;
    end;
    EditDoc := Client.OpenDocument('SCH', Path);
    if EditDoc = nil then
    begin
        EditWarn('cannot open sheet ' + Path);
        Exit;
    end;
    Client.ShowDocument(EditDoc);
    EditSheet := SchServer.GetCurrentSchDocument;
    if (EditSheet = nil) or (EditSheet.ObjectID <> SCH_DOC_ID) then
    begin
        EditWarn('not a schematic document: ' + Path);
        EditSheet := nil;
        Exit;
    end;
    SchServer.ProcessControl.PreProcess(EditSheet, '');
    Result := True;
end;

procedure EditSaveSheet;
begin
    if EditSheet = nil then Exit;
    SchServer.ProcessControl.PostProcess(EditSheet, '');
    EditSheet.GraphicallyInvalidate;
    EditDoc.Modified := True;
    EditDoc.DoFileSave('Advanced Schematic binary');
    EditCount('sheets_saved');
    EditSheet := nil;
end;

// PROJECT|path : open the project, creating the .PrjPcb first when missing.
procedure EditOpenProject(Path: String);
var
    Ini : TStringList;
begin
    if not FileExists(Path) then
    begin
        // A .PrjPcb is an INI file. These keys are the ones the sheet-entry /
        // port hierarchy depends on; Altium fills in the rest on save.
        Ini := TStringList.Create;
        Ini.Add('[Design]');
        Ini.Add('Version=1.0');
        Ini.Add('HierarchyMode=0');
        Ini.Add('AllowPortNetNames=0');
        Ini.Add('AllowSheetEntryNetNames=1');
        Ini.Add('NetlistSinglePinNets=1');
        Ini.SaveToFile(Path);
        Ini.Free;
        EditCount('projects_created');
    end;
    EditProject := GetWorkspace.DM_GetProjectFromPath(Path);
    if EditProject = nil then EditProject := GetWorkspace.DM_OpenProject(Path, True);
    if EditProject = nil then
        EditWarn('PROJECT: cannot open ' + Path)
    else
        EditProject.DM_SetAsCurrentProject;
end;

// ADDTOPROJECT|path : add a saved sheet to the PROJECT unless it is already in.
procedure EditAddToProject(Path: String);
var
    i : Integer;
begin
    if EditProject = nil then
    begin
        EditWarn('ADDTOPROJECT before any PROJECT: ' + Path);
        Exit;
    end;
    for i := 0 to EditProject.DM_LogicalDocumentCount - 1 do
        if SameText(EditProject.DM_LogicalDocuments(i).DM_FullPath, Path) then Exit;
    EditProject.DM_AddSourceDocument(Path);
    EditCount('documents_added');
end;

// SAVEPROJECT : save the PROJECT file through Altium's own Save process,
// then close and reopen the project. Saves the current sheet first, so a
// SHEET record is needed afterwards.
//
// The reopen is not cosmetic: the compiler judges "object within sheet
// boundaries" by the size a NEWSHEET document had when the project first saw
// it (A4), and reports every object on a resized sheet as off-sheet until the
// project is loaded again.
procedure EditSaveProject;
var
    ProjectPath : String;
begin
    if EditProject = nil then
    begin
        EditWarn('SAVEPROJECT before any PROJECT');
        Exit;
    end;
    EditSaveSheet;
    ProjectPath := EditProject.DM_ProjectFullPath;
    EditProject.DM_SetAsCurrentProject;
    ResetParameters;
    AddStringParameter('ObjectKind', 'Project');
    AddStringParameter('SaveMode', 'Standard');
    RunProcess('WorkspaceManager:SaveObject');
    EditCount('projects_saved');
    ResetParameters;
    AddStringParameter('ObjectKind', 'ProjectAndDocuments');
    RunProcess('WorkspaceManager:CloseObject');
    EditProject := GetWorkspace.DM_OpenProject(ProjectPath, True);
    if EditProject = nil then
        EditWarn('SAVEPROJECT: project did not reopen: ' + ProjectPath)
    else
        EditProject.DM_SetAsCurrentProject;
end;

// NEWSHEET|path|width|height : create a sheet, size it, save it under Path
// and make it the edit target.
function EditNewSheet(Path: String; WidthMils, HeightMils: Integer): Boolean;
var
    Focused : IProject;
begin
    Result := False;
    if FileExists(Path) then
    begin
        EditWarn('NEWSHEET: file exists, opened instead of created: ' + Path);
        Result := EditOpenSheet(Path);
        Exit;
    end;
    GetWorkspace.DM_CreateNewDocument('SCH');
    EditDoc := Client.GetCurrentView.OwnerDocument;
    if EditDoc = nil then
    begin
        EditWarn('NEWSHEET: Altium did not create a sheet for ' + Path);
        Exit;
    end;
    // DM_CreateNewDocument attaches the sheet to the FOCUSED project under its
    // temporary name, and a Save As on such a member leaves a second entry
    // behind. Detach it now; it joins the PROJECT of this spec once it has a
    // file, and never silently joins whatever the user has focused.
    Focused := GetWorkspace.DM_FocusedProject;
    if (Focused <> nil) and (Pos('Free Documents', Focused.DM_ProjectFileName) = 0) then
        Focused.DM_RemoveSourceDocument(EditDoc.DocumentName);
    Client.ShowDocument(EditDoc);
    EditSheet := SchServer.GetCurrentSchDocument;
    if (EditSheet = nil) or (EditSheet.ObjectID <> SCH_DOC_ID) then
    begin
        EditWarn('NEWSHEET: new document is not a schematic: ' + Path);
        EditSheet := nil;
        Exit;
    end;
    SchServer.ProcessControl.PreProcess(EditSheet, '');
    EditSheet.SheetStyle := eSheetA3;
    EditSheet.UseCustomSheet := True;
    EditSheet.CustomX := MilsToCoord(WidthMils);
    EditSheet.CustomY := MilsToCoord(HeightMils);
    EditSheet.SnapGridSize := MilsToCoord(50);
    EditSheet.ReferenceZonesOn := False;
    EditSheet.BorderOn := False;
    EditSheet.TitleBlockOn := False;
    SchServer.ProcessControl.PostProcess(EditSheet, '');
    // Save under the final name at once: only a sheet with a file can be a
    // project member, and the records that follow draw on a named sheet.
    EditDoc.DoSafeChangeFileNameAndSave(Path, 'Advanced Schematic binary');
    if EditProject <> nil then EditAddToProject(Path);
    SchServer.ProcessControl.PreProcess(EditSheet, '');
    EditCount('sheets_created');
    Result := True;
end;

procedure EditRegister(Obj: ISch_GraphicalObject);
begin
    EditSheet.RegisterSchObjectInContainer(Obj);
    SchServer.RobotManager.SendMessage(EditSheet.I_ObjectAddress, c_BroadCast,
        SCHM_PrimitiveRegistration, Obj.I_ObjectAddress);
end;

function CountPins(Comp: ISch_Component): Integer;
var
    Iter : ISch_Iterator;
    Pin  : ISch_GraphicalObject;
begin
    Result := 0;
    Iter := Comp.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(ePin));
    Pin := Iter.FirstSchObject;
    while Pin <> nil do
    begin
        Result := Result + 1;
        Pin := Iter.NextSchObject;
    end;
    Comp.SchIterator_Destroy(Iter);
end;

// Replicate drops the pins of some library components (seen on a one-pin
// test-point symbol while a 30-pin connector copied intact). When that
// happens, rebuild the component from its primitives one by one.
function CopyLibraryComponent(Src: ISch_Component): ISch_Component;
var
    Dup   : ISch_Component;
    Iter  : ISch_Iterator;
    Child : ISch_GraphicalObject;
begin
    Dup := Src.Replicate;
    if CountPins(Dup) = CountPins(Src) then
    begin
        Result := Dup;
        Exit;
    end;
    Dup := SchServer.SchObjectFactory(eSchComponent, eCreate_Default);
    Dup.LibReference := Src.LibReference;
    Dup.ComponentDescription := Src.ComponentDescription;
    Dup.CurrentPartID := 1;
    Dup.DisplayMode := 0;
    Dup.Location := Src.Location;
    Iter := Src.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(ePin, eRectangle, eLine, eEllipse, eArc, ePolyline, ePolygon,
                                   eLabel, eRoundRectangle, eBezier, eEllipticalArc));
    Child := Iter.FirstSchObject;
    while Child <> nil do
    begin
        Dup.AddSchObject(Child.Replicate);
        Child := Iter.NextSchObject;
    end;
    Src.SchIterator_Destroy(Iter);
    EditWarn('Replicate lost pins of ' + Src.LibReference + '; rebuilt from primitives');
    Result := Dup;
end;

function EditFindLibraryComponent(LibRef: String): ISch_Component;
var
    Iter : ISch_Iterator;
    Comp : ISch_Component;
begin
    Result := nil;
    if EditLib = nil then Exit;
    Iter := EditLib.SchLibIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
    Comp := Iter.FirstSchObject;
    while Comp <> nil do
    begin
        if Comp.LibReference = LibRef then Result := Comp;
        Comp := Iter.NextSchObject;
    end;
    EditLib.SchIterator_Destroy(Iter);
end;

function EditFindPart(Designator: String): ISch_Component;
var
    Iter : ISch_Iterator;
    Comp : ISch_Component;
begin
    Result := nil;
    Iter := EditSheet.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
    Comp := Iter.FirstSchObject;
    while Comp <> nil do
    begin
        if Comp.Designator.Text = Designator then Result := Comp;
        Comp := Iter.NextSchObject;
    end;
    EditSheet.SchIterator_Destroy(Iter);
end;

procedure EditSetParam(Comp: ISch_Component; PName, PVal: String);
var
    Iter  : ISch_Iterator;
    Param : ISch_Parameter;
    Found : Boolean;
begin
    Found := False;
    Iter := Comp.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eParameter));
    Param := Iter.FirstSchObject;
    while Param <> nil do
    begin
        if UpperCase(Param.Name) = UpperCase(PName) then
        begin
            Param.Text := PVal;
            Found := True;
        end;
        Param := Iter.NextSchObject;
    end;
    Comp.SchIterator_Destroy(Iter);
    if not Found then
    begin
        Param := SchServer.SchObjectFactory(eParameter, eCreate_Default);
        Param.Name := PName;
        Param.Text := PVal;
        Param.ParamType := eParameterType_String;
        Param.ReadOnlyState := eReadOnly_None;
        Param.IsHidden := True;
        Comp.AddSchObject(Param);
        SchServer.RobotManager.SendMessage(Comp.I_ObjectAddress, c_BroadCast,
            SCHM_PrimitiveRegistration, Param.I_ObjectAddress);
    end;
end;

procedure EditAddFootprint(Comp: ISch_Component; Model: String);
var
    Impl : ISch_Implementation;
begin
    Impl := Comp.AddSchImplementation;
    Impl.ModelName := Model;
    Impl.ModelType := 'PCBLIB';
    Impl.IsCurrent := True;
    Impl.UseComponentLibrary := True;
end;

// Record the absolute connection point of every pin of a component.
procedure EditRecordPins(Comp: ISch_Component);
var
    Iter : ISch_Iterator;
    Pin  : ISch_Pin;
    HotX, HotY : Integer;
begin
    Iter := Comp.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(ePin));
    Pin := Iter.FirstSchObject;
    while Pin <> nil do
    begin
        GetPinHotEnd(Pin, HotX, HotY);
        EditPinMap.Add('PIN|' + Comp.Designator.Text + '|' + Pin.Designator + '|' +
                       IntToStr(HotX) + '|' + IntToStr(HotY));
        Pin := Iter.NextSchObject;
    end;
    Comp.SchIterator_Destroy(Iter);
end;

// PART|designator|symbol|x|y|orientation|mirror
procedure EditPlacePart(Rec: String);
var
    Src, Comp : ISch_Component;
begin
    EditLastPart := nil;
    if EditLib = nil then
    begin
        EditWarn('PART before LIBRARY: ' + Rec);
        Exit;
    end;
    Src := EditFindLibraryComponent(GetFieldFromPipeString(Rec, 2));
    if Src = nil then
    begin
        EditWarn('symbol not found in library: ' + GetFieldFromPipeString(Rec, 2));
        Exit;
    end;
    Comp := CopyLibraryComponent(Src);
    Client.ShowDocument(EditDoc);
    Comp.Designator.Text := GetFieldFromPipeString(Rec, 1);
    Comp.UniqueId := GetWorkspace.DM_GenerateUniqueID;
    Comp.Orientation := FieldInt(Rec, 5, 0);
    EditRegister(Comp);
    if GetFieldFromPipeString(Rec, 6) = '1' then Comp.Mirror(Comp.Location);
    // MoveByXY so designator and comment travel with the body
    Comp.MoveByXY(MilsToCoord(FieldInt(Rec, 3, 0) - CoordToMils(Comp.Location.X)),
                  MilsToCoord(FieldInt(Rec, 4, 0) - CoordToMils(Comp.Location.Y)));
    EditLastPart := Comp;
    EditRecordPins(Comp);
    EditCount('parts_placed');
end;

// Remove the first object of a kind whose Location is (x, y); wires match on
// their first vertex.
function EditDeleteAt(Kind: String; X, Y: Integer): Boolean;
var
    Iter : ISch_Iterator;
    Obj, Hit : ISch_GraphicalObject;
    ObjKind : Integer;
    ObjX, ObjY : Integer;
begin
    Result := False;
    if      Kind = 'wire'     then ObjKind := eWire
    else if Kind = 'netlabel' then ObjKind := eNetLabel
    else if Kind = 'label'    then ObjKind := eLabel
    else if Kind = 'noerc'    then ObjKind := eNoERC
    else if Kind = 'junction' then ObjKind := eJunction
    else if Kind = 'port'     then ObjKind := ePort
    else if Kind = 'power'    then ObjKind := ePowerObject
    else
    begin
        EditWarn('DELETE_AT: unknown kind ' + Kind);
        Exit;
    end;
    Hit := nil;
    Iter := EditSheet.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(ObjKind));
    Obj := Iter.FirstSchObject;
    while (Obj <> nil) and (Hit = nil) do
    begin
        if ObjKind = eWire then
        begin
            ObjX := CoordToMils(Obj.Vertex[1].X);
            ObjY := CoordToMils(Obj.Vertex[1].Y);
        end
        else
        begin
            ObjX := CoordToMils(Obj.Location.X);
            ObjY := CoordToMils(Obj.Location.Y);
        end;
        if (ObjX = X) and (ObjY = Y) and OwnedBySheet(Obj) then Hit := Obj;
        Obj := Iter.NextSchObject;
    end;
    EditSheet.SchIterator_Destroy(Iter);
    if Hit <> nil then
    begin
        EditSheet.RemoveSchObject(Hit);
        Result := True;
    end;
end;

procedure EditApplyRecord(Rec: String);
var
    Kind : String;
    Obj  : ISch_GraphicalObject;
    Comp : ISch_Component;
    Iter : ISch_Iterator;
    Lbl  : ISch_GraphicalObject;
    Text : String;
    N, fld, vtx : Integer;
begin
    Kind := GetFieldFromPipeString(Rec, 0);
    if (Kind = '') or (Copy(Kind, 1, 1) = '#') then Exit;

    if Kind = 'SHEET' then
    begin
        EditSaveSheet;
        EditOpenSheet(GetFieldFromPipeString(Rec, 1));
        Exit;
    end;

    if Kind = 'LIBRARY' then
    begin
        EditLibPath := GetFieldFromPipeString(Rec, 1);
        Client.ShowDocument(Client.OpenDocument('SchLib', EditLibPath));
        Sleep(600);
        EditLib := SchServer.GetCurrentSchDocument;
        if (EditLib = nil) or (EditLib.ObjectID <> SCH_LIB_ID) then
        begin
            EditWarn('cannot open library ' + EditLibPath);
            EditLib := nil;
        end;
        if EditDoc <> nil then Client.ShowDocument(EditDoc);
        Exit;
    end;

    if Kind = 'PROJECT' then
    begin
        EditOpenProject(GetFieldFromPipeString(Rec, 1));
        Exit;
    end;

    if Kind = 'ADDTOPROJECT' then
    begin
        EditAddToProject(GetFieldFromPipeString(Rec, 1));
        Exit;
    end;

    if Kind = 'SAVEPROJECT' then
    begin
        EditSaveProject;
        Exit;
    end;

    if Kind = 'NEWSHEET' then
    begin
        EditSaveSheet;
        EditNewSheet(GetFieldFromPipeString(Rec, 1), FieldInt(Rec, 2, 16535), FieldInt(Rec, 3, 11693));
        Exit;
    end;

    if EditSheet = nil then
    begin
        EditWarn('record before any SHEET: ' + Rec);
        Exit;
    end;

    if Kind = 'PART' then
        EditPlacePart(Rec)

    else if Kind = 'COMMENT' then
    begin
        if EditLastPart <> nil then EditLastPart.Comment.Text := GetFieldFromPipeString(Rec, 1);
    end

    else if Kind = 'DESCRIPTION' then
    begin
        if EditLastPart <> nil then EditLastPart.ComponentDescription := GetFieldFromPipeString(Rec, 1);
    end

    else if Kind = 'FOOTPRINT' then
    begin
        if EditLastPart <> nil then EditAddFootprint(EditLastPart, GetFieldFromPipeString(Rec, 1));
    end

    else if Kind = 'PARAM' then
    begin
        if EditLastPart <> nil then
            EditSetParam(EditLastPart, GetFieldFromPipeString(Rec, 1), GetFieldFromPipeString(Rec, 2));
    end

    // DESIGNATOR_AT|designator|x|y  and  COMMENT_AT|x|y  position the text of the last part
    else if Kind = 'DESIGNATOR_AT' then
    begin
        if EditLastPart <> nil then EditLastPart.Designator.Location := MilsPoint(FieldInt(Rec, 1, 0), FieldInt(Rec, 2, 0));
    end
    else if Kind = 'COMMENT_AT' then
    begin
        if EditLastPart <> nil then EditLastPart.Comment.Location := MilsPoint(FieldInt(Rec, 1, 0), FieldInt(Rec, 2, 0));
    end
    else if Kind = 'COMMENT_HIDDEN' then
    begin
        if EditLastPart <> nil then EditLastPart.Comment.IsHidden := (GetFieldFromPipeString(Rec, 1) = '1');
    end

    // SET_PARAM|designator|name|value ; SET_COMMENT|designator|text ; SET_FOOTPRINT|designator|model
    else if (Kind = 'SET_PARAM') or (Kind = 'SET_COMMENT') or (Kind = 'SET_FOOTPRINT') then
    begin
        Comp := EditFindPart(GetFieldFromPipeString(Rec, 1));
        if Comp = nil then
            EditWarn(Kind + ': designator not found ' + GetFieldFromPipeString(Rec, 1))
        else
        begin
            if Kind = 'SET_PARAM' then EditSetParam(Comp, GetFieldFromPipeString(Rec, 2), GetFieldFromPipeString(Rec, 3))
            else if Kind = 'SET_COMMENT' then Comp.Comment.Text := GetFieldFromPipeString(Rec, 2)
            else EditAddFootprint(Comp, GetFieldFromPipeString(Rec, 2));
            EditCount('parts_updated');
        end;
    end

    // DELETE_PART|designator
    else if Kind = 'DELETE_PART' then
    begin
        Comp := EditFindPart(GetFieldFromPipeString(Rec, 1));
        if Comp = nil then
            EditWarn('DELETE_PART: designator not found ' + GetFieldFromPipeString(Rec, 1))
        else
        begin
            EditSheet.RemoveSchObject(Comp);
            EditCount('parts_deleted');
        end;
    end

    // DELETE_AT|kind|x|y
    else if Kind = 'DELETE_AT' then
    begin
        if EditDeleteAt(GetFieldFromPipeString(Rec, 1), FieldInt(Rec, 2, 0), FieldInt(Rec, 3, 0)) then
            EditCount('objects_deleted')
        else
            EditWarn('DELETE_AT: nothing at ' + Rec);
    end

    // WIRE|x|y|x|y[...]
    else if Kind = 'WIRE' then
    begin
        Obj := SchServer.SchObjectFactory(eWire, eCreate_GlobalCopy);
        Obj.Location := MilsPoint(FieldInt(Rec, 1, 0), FieldInt(Rec, 2, 0));
        fld := 1;
        vtx := 0;
        while GetFieldFromPipeString(Rec, fld) <> '' do
        begin
            vtx := vtx + 1;
            Obj.InsertVertex := vtx;
            Obj.SetState_Vertex(vtx, MilsPoint(FieldInt(Rec, fld, 0), FieldInt(Rec, fld + 1, 0)));
            fld := fld + 2;
        end;
        EditRegister(Obj);
        EditCount('wires');
    end

    else if Kind = 'JUNCTION' then
    begin
        Obj := SchServer.SchObjectFactory(eJunction, eCreate_GlobalCopy);
        Obj.Location := MilsPoint(FieldInt(Rec, 1, 0), FieldInt(Rec, 2, 0));
        EditRegister(Obj);
        EditCount('junctions');
    end

    // NETLABEL|x|y|orientation|text
    else if Kind = 'NETLABEL' then
    begin
        Obj := SchServer.SchObjectFactory(eNetLabel, eCreate_GlobalCopy);
        Obj.Location := MilsPoint(FieldInt(Rec, 1, 0), FieldInt(Rec, 2, 0));
        Obj.Orientation := FieldInt(Rec, 3, 0);
        Obj.Text := GetFieldFromPipeString(Rec, 4);
        EditRegister(Obj);
        EditCount('net_labels');
    end

    // POWER|x|y|orientation|style|text|show_net_name
    else if Kind = 'POWER' then
    begin
        Obj := SchServer.SchObjectFactory(ePowerObject, eCreate_GlobalCopy);
        Obj.Location := MilsPoint(FieldInt(Rec, 1, 0), FieldInt(Rec, 2, 0));
        Obj.Orientation := FieldInt(Rec, 3, 3);
        Obj.Style := FieldInt(Rec, 4, 5);
        Obj.ShowNetName := (GetFieldFromPipeString(Rec, 6) = '1');
        Obj.Text := GetFieldFromPipeString(Rec, 5);
        EditRegister(Obj);
        EditCount('power_ports');
    end

    // SPORT|x|y|name|iotype|style|width
    else if Kind = 'SPORT' then
    begin
        Obj := SchServer.SchObjectFactory(ePort, eCreate_GlobalCopy);
        Obj.Location := MilsPoint(FieldInt(Rec, 1, 0), FieldInt(Rec, 2, 0));
        Obj.Name := GetFieldFromPipeString(Rec, 3);
        Obj.IOType := FieldInt(Rec, 4, 0);
        Obj.Style := FieldInt(Rec, 5, 0);
        Obj.Width := MilsToCoord(FieldInt(Rec, 6, 1000));
        EditRegister(Obj);
        EditCount('ports');
    end

    // NOERC|x|y
    else if Kind = 'NOERC' then
    begin
        Obj := SchServer.SchObjectFactory(eNoERC, eCreate_GlobalCopy);
        Obj.Location := MilsPoint(FieldInt(Rec, 1, 0), FieldInt(Rec, 2, 0));
        EditRegister(Obj);
        EditCount('no_erc');
    end

    // NOTE|x|y|text[|font_size[|font_name]]
    else if Kind = 'NOTE' then
    begin
        Obj := SchServer.SchObjectFactory(eLabel, eCreate_GlobalCopy);
        Obj.Location := MilsPoint(FieldInt(Rec, 1, 0), FieldInt(Rec, 2, 0));
        Obj.Text := GetFieldFromPipeString(Rec, 3);
        if GetFieldFromPipeString(Rec, 4) <> '' then
        begin
            Text := GetFieldFromPipeString(Rec, 5);
            if Text = '' then Text := 'Arial';
            Obj.FontID := SchServer.FontManager.GetFontID(FieldInt(Rec, 4, 10), 0, False, False, False, False, Text);
        end;
        EditRegister(Obj);
        EditCount('notes');
    end

    // TEXT_REPLACE|old|new  (every label whose text equals old)
    else if Kind = 'TEXT_REPLACE' then
    begin
        N := 0;
        Iter := EditSheet.SchIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eLabel));
        Lbl := Iter.FirstSchObject;
        while Lbl <> nil do
        begin
            if OwnedBySheet(Lbl) and (Lbl.Text = GetFieldFromPipeString(Rec, 1)) then
            begin
                Lbl.Text := GetFieldFromPipeString(Rec, 2);
                N := N + 1;
            end;
            Lbl := Iter.NextSchObject;
        end;
        EditSheet.SchIterator_Destroy(Iter);
        if N = 0 then EditWarn('TEXT_REPLACE: no label reads ' + GetFieldFromPipeString(Rec, 1))
        else EditCount('texts_replaced');
    end

    // TEXT_MOVE|text|x|y
    else if Kind = 'TEXT_MOVE' then
    begin
        N := 0;
        Iter := EditSheet.SchIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eLabel));
        Lbl := Iter.FirstSchObject;
        while Lbl <> nil do
        begin
            if OwnedBySheet(Lbl) and (Lbl.Text = GetFieldFromPipeString(Rec, 1)) then
            begin
                Lbl.Location := MilsPoint(FieldInt(Rec, 2, 0), FieldInt(Rec, 3, 0));
                N := N + 1;
            end;
            Lbl := Iter.NextSchObject;
        end;
        EditSheet.SchIterator_Destroy(Iter);
        if N = 0 then EditWarn('TEXT_MOVE: no label reads ' + GetFieldFromPipeString(Rec, 1))
        else EditCount('texts_moved');
    end

    // TEXT_DELETE|text
    else if Kind = 'TEXT_DELETE' then
    begin
        N := 0;
        Iter := EditSheet.SchIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eLabel));
        Lbl := Iter.FirstSchObject;
        Obj := nil;
        while Lbl <> nil do
        begin
            if OwnedBySheet(Lbl) and (Lbl.Text = GetFieldFromPipeString(Rec, 1)) and (Obj = nil) then Obj := Lbl;
            Lbl := Iter.NextSchObject;
        end;
        EditSheet.SchIterator_Destroy(Iter);
        if Obj = nil then EditWarn('TEXT_DELETE: no label reads ' + GetFieldFromPipeString(Rec, 1))
        else
        begin
            EditSheet.RemoveSchObject(Obj);
            EditCount('texts_deleted');
        end;
    end

    // SHEETSYMBOL_RENAME|old|new
    else if Kind = 'SHEETSYMBOL_RENAME' then
    begin
        N := 0;
        Iter := EditSheet.SchIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eSheetSymbol));
        Lbl := Iter.FirstSchObject;
        while Lbl <> nil do
        begin
            if Lbl.SheetName.Text = GetFieldFromPipeString(Rec, 1) then
            begin
                Lbl.SheetName.Text := GetFieldFromPipeString(Rec, 2);
                N := N + 1;
            end;
            Lbl := Iter.NextSchObject;
        end;
        EditSheet.SchIterator_Destroy(Iter);
        if N = 0 then EditWarn('SHEETSYMBOL_RENAME: no sheet symbol named ' + GetFieldFromPipeString(Rec, 1))
        else EditCount('sheet_symbols_renamed');
    end

    // LINE|x1|y1|x2|y2   (graphical line - a frame or a divider, not a wire)
    else if Kind = 'LINE' then
    begin
        Obj := SchServer.SchObjectFactory(eLine, eCreate_GlobalCopy);
        Obj.Location := MilsPoint(FieldInt(Rec, 1, 0), FieldInt(Rec, 2, 0));
        Obj.Corner := MilsPoint(FieldInt(Rec, 3, 0), FieldInt(Rec, 4, 0));
        Obj.LineWidth := eSmall;
        EditRegister(Obj);
        EditCount('lines');
    end

    // SHEETSYMBOL|x|y|x_size|y_size|name|file[|area_color|line_color]
    // x, y is the top-left corner; colors are BGR integers.
    else if Kind = 'SHEETSYMBOL' then
    begin
        Obj := SchServer.SchObjectFactory(eSheetSymbol, eCreate_GlobalCopy);
        Obj.Location := MilsPoint(FieldInt(Rec, 1, 0), FieldInt(Rec, 2, 0));
        Obj.XSize := MilsToCoord(FieldInt(Rec, 3, 2000));
        Obj.YSize := MilsToCoord(FieldInt(Rec, 4, 2000));
        Obj.SheetName.Text := GetFieldFromPipeString(Rec, 5);
        Obj.SheetName.Location := MilsPoint(FieldInt(Rec, 1, 0), FieldInt(Rec, 2, 0) + 100);
        Obj.SheetFileName.Text := GetFieldFromPipeString(Rec, 6);
        Obj.SheetFileName.Location := MilsPoint(FieldInt(Rec, 1, 0), FieldInt(Rec, 2, 0) - FieldInt(Rec, 4, 2000) - 180);
        Obj.IsSolid := True;
        if GetFieldFromPipeString(Rec, 7) <> '' then Obj.AreaColor := FieldInt(Rec, 7, 0);
        if GetFieldFromPipeString(Rec, 8) <> '' then Obj.Color := FieldInt(Rec, 8, 0);
        Obj.UniqueId := GetWorkspace.DM_GenerateUniqueID;
        EditRegister(Obj);
        EditLastSymbol := Obj;
        EditCount('sheet_symbols');
    end

    // SHEETENTRY|name|side|distance|iotype   (on the last SHEETSYMBOL;
    // side 0 left, 1 right, 2 top, 3 bottom; distance from the top/left edge)
    else if Kind = 'SHEETENTRY' then
    begin
        if EditLastSymbol = nil then
            EditWarn('SHEETENTRY before any SHEETSYMBOL: ' + Rec)
        else
        begin
            Obj := SchServer.SchObjectFactory(eSheetEntry, eCreate_GlobalCopy);
            Obj.Name := GetFieldFromPipeString(Rec, 1);
            Obj.Side := FieldInt(Rec, 2, 0);
            N := MilsToCoord(FieldInt(Rec, 3, 200));
            Obj.DistanceFromTop := N;
            Obj.IOType := FieldInt(Rec, 4, 0);
            EditLastSymbol.AddSchObject(Obj);
            if Obj.Side = 1 then
                Obj.Location := Point(EditLastSymbol.Location.X + EditLastSymbol.XSize, EditLastSymbol.Location.Y - N)
            else if Obj.Side = 2 then
                Obj.Location := Point(EditLastSymbol.Location.X + N, EditLastSymbol.Location.Y)
            else if Obj.Side = 3 then
                Obj.Location := Point(EditLastSymbol.Location.X + N, EditLastSymbol.Location.Y - EditLastSymbol.YSize)
            else
                Obj.Location := Point(EditLastSymbol.Location.X, EditLastSymbol.Location.Y - N);
            EditCount('sheet_entries');
        end;
    end

    else
        EditWarn('unknown record: ' + Rec);
end;

function EditSchematicSheet(SpecPath: String; PinMapPath: String): String;
var
    Spec  : TStringList;
    Props : TStringList;
    i     : Integer;
begin
    if not FileExists(SpecPath) then
    begin
        Result := 'ERROR: spec file not found: ' + SpecPath;
        Exit;
    end;
    Spec := TStringList.Create;
    Spec.LoadFromFile(SpecPath);
    EditWarnings := TStringList.Create;
    EditPinMap := TStringList.Create;
    EditCounts := TStringList.Create;
    EditSheet := nil;
    EditDoc := nil;
    EditLib := nil;
    EditLibPath := '';
    EditLastPart := nil;
    EditLastSymbol := nil;
    EditProject := nil;
    try
        for i := 0 to Spec.Count - 1 do
            EditApplyRecord(Spec[i]);
        EditSaveSheet;
        EditPinMap.SaveToFile(PinMapPath);

        Props := TStringList.Create;
        AddJSONBoolean(Props, 'success', True);
        AddJSONInteger(Props, 'records', Spec.Count);
        for i := 0 to EditCounts.Count - 1 do
            AddJSONInteger(Props, EditCounts.Names[i], StrToInt(EditCounts.ValueFromIndex[i]));
        AddJSONInteger(Props, 'pins_recorded', EditPinMap.Count);
        Props.Add(BuildJSONArray(EditWarnings, 'warnings'));
        Result := BuildJSONObject(Props);
        Props.Free;
    finally
        Spec.Free;
        EditWarnings.Free;
        EditPinMap.Free;
        EditCounts.Free;
    end;
end;

// ---------------------------------------------------------------------------
// get_schematic_sheet: every object of a sheet as JSON, written to OutPath
// ---------------------------------------------------------------------------

function PinJSON(Pin: ISch_Pin): String;
var
    HotX, HotY : Integer;
begin
    GetPinHotEnd(Pin, HotX, HotY);
    Result := '{"number": ' + JSONStr(Pin.Designator) + ', "name": ' + JSONStr(Pin.Name) +
              ', "electrical": ' + IntToStr(Pin.Electrical) +
              ', "x": ' + IntToStr(CoordToMils(Pin.Location.X)) + ', "y": ' + IntToStr(CoordToMils(Pin.Location.Y)) +
              ', "hot_x": ' + IntToStr(HotX) + ', "hot_y": ' + IntToStr(HotY) + '}';
end;

function ComponentJSON(Comp: ISch_Component): String;
var
    Iter   : ISch_Iterator;
    Child  : ISch_GraphicalObject;
    Impl   : ISch_Implementation;
    Pins, Params, Models : TStringList;
    Props  : TStringList;
begin
    Pins := TStringList.Create;
    Params := TStringList.Create;
    Models := TStringList.Create;
    Iter := Comp.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(ePin));
    Child := Iter.FirstSchObject;
    while Child <> nil do
    begin
        Pins.Add(PinJSON(Child));
        Child := Iter.NextSchObject;
    end;
    Comp.SchIterator_Destroy(Iter);
    Iter := Comp.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eParameter));
    Child := Iter.FirstSchObject;
    while Child <> nil do
    begin
        Params.Add(JSONPairStr(Child.Name, Child.Text, True));
        Child := Iter.NextSchObject;
    end;
    Comp.SchIterator_Destroy(Iter);
    Iter := Comp.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eImplementation));
    Impl := Iter.FirstSchObject;
    while Impl <> nil do
    begin
        Models.Add('{"type": ' + JSONStr(Impl.ModelType) + ', "name": ' + JSONStr(Impl.ModelName) +
                   ', "current": ' + JSONBool(Impl.IsCurrent) + '}');
        Impl := Iter.NextSchObject;
    end;
    Comp.SchIterator_Destroy(Iter);
    Props := TStringList.Create;
    AddJSONProperty(Props, 'designator', Comp.Designator.Text);
    AddJSONProperty(Props, 'lib_reference', Comp.LibReference);
    AddJSONProperty(Props, 'comment', Comp.Comment.Text);
    AddJSONProperty(Props, 'description', Comp.ComponentDescription);
    AddJSONInteger(Props, 'x', CoordToMils(Comp.Location.X));
    AddJSONInteger(Props, 'y', CoordToMils(Comp.Location.Y));
    AddJSONInteger(Props, 'orientation', Comp.Orientation);
    AddJSONBoolean(Props, 'mirrored', Comp.IsMirrored);
    Props.Add('"parameters": ' + BuildJSONObject(Params, 1));
    Props.Add(BuildJSONArray(Models, 'models', 1));
    Props.Add(BuildJSONArray(Pins, 'pins', 1));
    Result := BuildJSONObject(Props, 1);
    Props.Free;
    Pins.Free;
    Params.Free;
    Models.Free;
end;

function GetSchematicSheet(Path: String; OutPath: String): String;
var
    Doc    : IServerDocument;
    Sheet  : ISch_Document;
    Iter, EntryIter : ISch_Iterator;
    Obj, Entry : ISch_GraphicalObject;
    Comps, Labels, Nets, Ports, Symbols, Wires, Misc, Entries : TStringList;
    OutList, Props : TStringList;
    S : String;
    v : Integer;
begin
    if not FileExists(Path) then
    begin
        Result := 'ERROR: sheet file not found: ' + Path;
        Exit;
    end;
    Doc := Client.OpenDocument('SCH', Path);
    if Doc = nil then
    begin
        Result := 'ERROR: cannot open sheet ' + Path;
        Exit;
    end;
    Client.ShowDocument(Doc);
    Sheet := SchServer.GetCurrentSchDocument;
    if (Sheet = nil) or (Sheet.ObjectID <> SCH_DOC_ID) then
    begin
        Result := 'ERROR: not a schematic document: ' + Path;
        Exit;
    end;

    Comps := TStringList.Create;  Labels := TStringList.Create; Nets := TStringList.Create;
    Ports := TStringList.Create;  Symbols := TStringList.Create; Wires := TStringList.Create;
    Misc := TStringList.Create;

    Iter := Sheet.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eSchComponent, eLabel, eNetLabel, ePort, eSheetSymbol, eWire,
                                   eNoERC, eJunction, ePowerObject));
    Obj := Iter.FirstSchObject;
    while Obj <> nil do
    begin
        if OwnedBySheet(Obj) then
        begin
        if Obj.ObjectId = eSchComponent then
            Comps.Add(ComponentJSON(Obj))
        else if Obj.ObjectId = eLabel then
            Labels.Add('{"x": ' + IntToStr(CoordToMils(Obj.Location.X)) + ', "y": ' + IntToStr(CoordToMils(Obj.Location.Y)) +
                       ', "text": ' + JSONStr(Obj.Text) + ', "font_id": ' + IntToStr(Obj.FontID) + '}')
        else if Obj.ObjectId = eNetLabel then
            Nets.Add('{"x": ' + IntToStr(CoordToMils(Obj.Location.X)) + ', "y": ' + IntToStr(CoordToMils(Obj.Location.Y)) +
                     ', "text": ' + JSONStr(Obj.Text) + ', "orientation": ' + IntToStr(Obj.Orientation) + '}')
        else if Obj.ObjectId = ePort then
            Ports.Add('{"x": ' + IntToStr(CoordToMils(Obj.Location.X)) + ', "y": ' + IntToStr(CoordToMils(Obj.Location.Y)) +
                      ', "name": ' + JSONStr(Obj.Name) + ', "io_type": ' + IntToStr(Obj.IOType) +
                      ', "style": ' + IntToStr(Obj.Style) + ', "width": ' + IntToStr(CoordToMils(Obj.Width)) + '}')
        else if Obj.ObjectId = eSheetSymbol then
        begin
            Entries := TStringList.Create;
            EntryIter := Obj.SchIterator_Create;
            EntryIter.AddFilter_ObjectSet(MkSet(eSheetEntry));
            Entry := EntryIter.FirstSchObject;
            while Entry <> nil do
            begin
                Entries.Add('{"name": ' + JSONStr(Entry.Name) + ', "side": ' + IntToStr(Entry.Side) +
                            ', "distance": ' + IntToStr(CoordToMils(Entry.DistanceFromTop)) + ', "io_type": ' + IntToStr(Entry.IOType) + '}');
                Entry := EntryIter.NextSchObject;
            end;
            Obj.SchIterator_Destroy(EntryIter);
            Symbols.Add('{"name": ' + JSONStr(Obj.SheetName.Text) + ', "file": ' + JSONStr(Obj.SheetFileName.Text) +
                        ', "x": ' + IntToStr(CoordToMils(Obj.Location.X)) + ', "y": ' + IntToStr(CoordToMils(Obj.Location.Y)) +
                        ', "x_size": ' + IntToStr(CoordToMils(Obj.XSize)) + ', "y_size": ' + IntToStr(CoordToMils(Obj.YSize)) +
                        ', "entries": ' + BuildJSONArray(Entries, '', 2) + '}');
            Entries.Free;
        end
        else if Obj.ObjectId = eWire then
        begin
            S := '';
            for v := 1 to Obj.VerticesCount do
            begin
                if S <> '' then S := S + ', ';
                S := S + '[' + IntToStr(CoordToMils(Obj.Vertex[v].X)) + ', ' + IntToStr(CoordToMils(Obj.Vertex[v].Y)) + ']';
            end;
            Wires.Add('[' + S + ']');
        end
        else if Obj.ObjectId = eNoERC then
            Misc.Add('{"kind": "no_erc", "x": ' + IntToStr(CoordToMils(Obj.Location.X)) + ', "y": ' + IntToStr(CoordToMils(Obj.Location.Y)) + '}')
        else if Obj.ObjectId = eJunction then
            Misc.Add('{"kind": "junction", "x": ' + IntToStr(CoordToMils(Obj.Location.X)) + ', "y": ' + IntToStr(CoordToMils(Obj.Location.Y)) + '}')
        else if Obj.ObjectId = ePowerObject then
            Misc.Add('{"kind": "power", "x": ' + IntToStr(CoordToMils(Obj.Location.X)) + ', "y": ' + IntToStr(CoordToMils(Obj.Location.Y)) +
                     ', "text": ' + JSONStr(Obj.Text) + ', "style": ' + IntToStr(Obj.Style) + ', "orientation": ' + IntToStr(Obj.Orientation) + '}');
        end;
        Obj := Iter.NextSchObject;
    end;
    Sheet.SchIterator_Destroy(Iter);

    Props := TStringList.Create;
    AddJSONProperty(Props, 'sheet', Doc.FileName);
    AddJSONInteger(Props, 'width_mils', CoordToMils(Sheet.SheetSizeX));
    AddJSONInteger(Props, 'height_mils', CoordToMils(Sheet.SheetSizeY));
    Props.Add(BuildJSONArray(Comps, 'components', 1));
    Props.Add(BuildJSONArray(Labels, 'labels', 1));
    Props.Add(BuildJSONArray(Nets, 'net_labels', 1));
    Props.Add(BuildJSONArray(Ports, 'ports', 1));
    Props.Add(BuildJSONArray(Symbols, 'sheet_symbols', 1));
    Props.Add(BuildJSONArray(Wires, 'wires', 1));
    Props.Add(BuildJSONArray(Misc, 'other', 1));
    OutList := TStringList.Create;
    OutList.Text := BuildJSONObject(Props);
    OutList.SaveToFile(OutPath);

    Result := '{"success": true, "file": ' + JSONStr(OutPath) +
              ', "components": ' + IntToStr(Comps.Count) + ', "labels": ' + IntToStr(Labels.Count) +
              ', "net_labels": ' + IntToStr(Nets.Count) + ', "ports": ' + IntToStr(Ports.Count) +
              ', "sheet_symbols": ' + IntToStr(Symbols.Count) + ', "wires": ' + IntToStr(Wires.Count) +
              ', "other": ' + IntToStr(Misc.Count) + '}';

    OutList.Free; Props.Free; Comps.Free; Labels.Free; Nets.Free; Ports.Free; Symbols.Free; Wires.Free; Misc.Free;
end;

// ---------------------------------------------------------------------------
// open_project: open a project of any kind (.PrjPcb, .PrjMbd, ...) and list
// its logical documents, so a project written by a tool can be checked
// through Altium's own loader.
// ---------------------------------------------------------------------------
function OpenProjectReport(ProjectPath: String; OutPath: String): String;
var
    Prj   : IProject;
    Doc   : IDocument;
    Docs, Props, OutList : TStringList;
    i : Integer;
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
    Docs := TStringList.Create;
    for i := 0 to Prj.DM_LogicalDocumentCount - 1 do
    begin
        Doc := Prj.DM_LogicalDocuments(i);
        Docs.Add('{"kind": ' + JSONStr(Doc.DM_DocumentKind) + ', "path": ' + JSONStr(Doc.DM_FullPath) +
                 ', "exists": ' + JSONBool(FileExists(Doc.DM_FullPath)) + '}');
    end;
    Props := TStringList.Create;
    AddJSONBoolean(Props, 'success', True);
    AddJSONProperty(Props, 'project', Prj.DM_ProjectFileName);
    AddJSONProperty(Props, 'kind', ExtractFileExt(Prj.DM_ProjectFullPath));
    AddJSONInteger(Props, 'logical_documents', Prj.DM_LogicalDocumentCount);
    Props.Add(BuildJSONArray(Docs, 'documents', 1));
    OutList := TStringList.Create;
    OutList.Text := BuildJSONObject(Props);
    OutList.SaveToFile(OutPath);
    Result := '{"success": true, "documents": ' + IntToStr(Docs.Count) + ', "file": ' + JSONStr(OutPath) + '}';
    OutList.Free;
    Props.Free;
    Docs.Free;
end;

// ---------------------------------------------------------------------------
// compile_project: compile, then report violations and (optionally) every pin's net
// ---------------------------------------------------------------------------
function CompileProjectReport(ProjectPath: String; OutPath: String; IncludePins: Boolean): String;
var
    Prj   : IProject;
    Doc   : IDocument;
    Part, Pin, Viol;          // IPart / IPin / IViolation are not script type names
    Compiled : Boolean;
    Viols, Pins, Props : TStringList;
    OutList   : TStringList;
    i, j, k : Integer;
begin
    // DM_OpenProject on a missing path silently creates a new empty project.
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
    Compiled := Prj.DM_Compile;

    Viols := TStringList.Create;
    Pins := TStringList.Create;
    for i := 0 to Prj.DM_ViolationCount - 1 do
    begin
        Viol := Prj.DM_Violations(i);
        Viols.Add('{"level": ' + IntToStr(Viol.DM_ErrorLevel) + ', "kind": ' + JSONStr(Viol.DM_DescriptorString) +
                  ', "detail": ' + JSONStr(Viol.DM_DetailString) + '}');
    end;
    if IncludePins then
    begin
        Doc := Prj.DM_DocumentFlattened;
        if Doc <> nil then
            for j := 0 to Doc.DM_PartCount - 1 do
            begin
                Part := Doc.DM_Parts(j);
                for k := 0 to Part.DM_PinCount - 1 do
                begin
                    Pin := Part.DM_Pins(k);
                    Pins.Add('{"designator": ' + JSONStr(Part.DM_LogicalDesignator) + ', "pin": ' + JSONStr(Pin.DM_PinNumber) +
                             ', "net": ' + JSONStr(Pin.DM_NetName) + '}');
                end;
            end;
    end;

    Props := TStringList.Create;
    AddJSONBoolean(Props, 'compiled', Compiled);
    AddJSONProperty(Props, 'project', Prj.DM_ProjectFileName);
    AddJSONInteger(Props, 'logical_documents', Prj.DM_LogicalDocumentCount);
    AddJSONInteger(Props, 'physical_documents', Prj.DM_PhysicalDocumentCount);
    AddJSONInteger(Props, 'violation_count', Prj.DM_ViolationCount);
    Props.Add(BuildJSONArray(Viols, 'violations', 1));
    Props.Add(BuildJSONArray(Pins, 'pins', 1));
    OutList := TStringList.Create;
    OutList.Text := BuildJSONObject(Props);
    OutList.SaveToFile(OutPath);

    Result := '{"success": true, "compiled": ' + JSONBool(Compiled) + ', "violation_count": ' + IntToStr(Prj.DM_ViolationCount) +
              ', "pins": ' + IntToStr(Pins.Count) + ', "file": ' + JSONStr(OutPath) + '}';
    OutList.Free; Props.Free; Viols.Free; Pins.Free;
end;

// ---------------------------------------------------------------------------
// save_documents: save already-open documents by path (SchDoc, SchLib, PcbLib, PcbDoc)
// ---------------------------------------------------------------------------

function SaveDocumentsFromList(Paths: TStringList): String;
var
    Doc  : IServerDocument;
    Ext, Format : String;
    Saved, Missing : TStringList;
    i    : Integer;
    Ok   : Boolean;
begin
    Saved := TStringList.Create;
    Missing := TStringList.Create;
    for i := 0 to Paths.Count - 1 do
    begin
        Doc := Client.GetDocumentByPath(Paths[i]);
        if Doc = nil then
        begin
            Missing.Add(JSONStr(Paths[i]));
            Continue;
        end;
        Ext := LowerCase(ExtractFileExt(Paths[i]));
        if      Ext = '.schdoc' then Format := 'Advanced Schematic binary'
        else if Ext = '.schlib' then Format := 'Advanced Schematic binary library'
        else if Ext = '.pcblib' then Format := 'PCB Library File'
        else if Ext = '.pcbdoc' then Format := 'PCB Binary File'
        else Format := '';
        Client.ShowDocument(Doc);
        Doc.Modified := True;
        Ok := Doc.DoFileSave(Format);
        if Ok then Saved.Add(JSONStr(Paths[i])) else Missing.Add(JSONStr(Paths[i] + ' (save returned false)'));
    end;
    Result := '{"success": true, ' + BuildJSONArray(Saved, 'saved') + ', ' + BuildJSONArray(Missing, 'not_saved') + '}';
    Saved.Free;
    Missing.Free;
end;
