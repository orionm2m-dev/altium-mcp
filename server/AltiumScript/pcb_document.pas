// pcb_document.pas
// A PCB document from data, the way a schematic is built by edit_schematic_sheet:
//   * create_pcb_document     - a new .PcbDoc saved under a path and added to a
//                               project (an existing file is opened and added)
//   * set_pcb_layer_stack     - the copper layers and dielectrics of a board from
//                               a spec file, top to bottom
//   * set_board_outline       - the board shape from a list of vertices (mm)
//   * set_pcb_regions         - sub-stacks, split lines, board regions and
//                               bending lines of a rigid-flex board
//   * update_pcb_from_project - Design » Import Changes From <project>: the ECO
//                               that brings the components and nets of the
//                               schematics onto the board
//
// Every function returns a JSON string. The layer stack a tool wrote is read back
// from the board for the report, so the report shows what Altium holds, not what
// the spec asked for.

var
    PcbDocWarnings : TStringList;

procedure PcbDocWarn(Msg: String);
begin
    if PcbDocWarnings <> nil then PcbDocWarnings.Add('"' + JSONEscapeString(Msg) + '"');
end;

function PcbDocError(Msg: String): String;
begin
    Result := '{"success": false, "error": ' + JSONStr(Msg) + '}';
end;

// A number for JSON whatever the decimal separator of the locale.
function PcbDocNum(Fmt: String; V: Double): String;
begin
    Result := StringReplace(FormatFloat(Fmt, V), ',', '.', REPLACEALL);
end;

// Open (or focus) a PCB document and return its board. Opening a path that does
// not exist would hand back a new empty board, so a missing file is refused.
function OpenBoardAtPath(Path: String): IPCB_Board;
var
    Doc : IServerDocument;
begin
    Result := nil;
    if not FileExists(Path) then Exit;
    Doc := Client.GetDocumentByPath(Path);
    if Doc = nil then Doc := Client.OpenDocument('PCB', Path);
    if Doc = nil then Exit;
    Client.ShowDocument(Doc);
    Doc.Focus;
    if PCBServer = nil then Exit;
    Result := PCBServer.GetPCBBoardByPath(Path);
    if Result = nil then Result := PCBServer.GetCurrentPCBBoard;
end;

function SaveBoardDocument(Path: String): Boolean;
var
    Doc : IServerDocument;
begin
    Result := False;
    Doc := Client.GetDocumentByPath(Path);
    if Doc = nil then Exit;
    Doc.Modified := True;
    Result := Doc.DoFileSave('PCB Binary File');
end;

// Open a project (current afterwards) or return nil.
function OpenProjectAtPath(Path: String): IProject;
begin
    Result := nil;
    if not FileExists(Path) then Exit;
    Result := GetWorkspace.DM_GetProjectFromPath(Path);
    if Result = nil then Result := GetWorkspace.DM_OpenProject(Path, True);
    if Result <> nil then Result.DM_SetAsCurrentProject;
end;

function ProjectHasDocument(Project: IProject; Path: String): Boolean;
var
    i : Integer;
begin
    Result := False;
    for i := 0 to Project.DM_LogicalDocumentCount - 1 do
        if SameText(Project.DM_LogicalDocuments(i).DM_FullPath, Path) then Result := True;
end;

procedure SaveProjectFile(Project: IProject);
begin
    Project.DM_SetAsCurrentProject;
    ResetParameters;
    AddStringParameter('ObjectKind', 'Project');
    AddStringParameter('SaveMode', 'Standard');
    RunProcess('WorkspaceManager:SaveObject');
end;

function DielectricTypeName(T: Integer): String;
begin
    case T of
        eCore:            Result := 'core';
        ePrePreg:         Result := 'prepreg';
        eSurfaceMaterial: Result := 'surface';
    else
        Result := 'none';
    end;
end;


// The copper layers of the board top to bottom, each with the dielectric below
// it, and the surface dielectrics; the body of every report of this unit.
function IsCopperLayerId(Id: Integer): Boolean;
begin
    Result := ((Id >= 1) and (Id <= 32)) or ((Id >= 39) and (Id <= 54));
end;

// Whether a layer of the master stack is one of its dielectric-class layers
// (core, prepreg, surface finish). Overlay and other physical layers have no
// dielectric properties, and reading one would stop the script.
function IsDielectricLayer(Master: IPCB_MasterLayerStack; L: IPCB_LayerObject): Boolean;
var
    D : IPCB_LayerObject;
begin
    Result := False;
    D := Master.First(eLayerClass_Dielectric);
    while D <> nil do
    begin
        if D.I_ObjectAddress = L.I_ObjectAddress then Result := True;
        D := Master.Next(eLayerClass_Dielectric, D);
    end;
end;

// Every physical layer of the master layer stack top to bottom: copper with
// its thickness, dielectrics (cores, prepregs, surface finishes) with height,
// material and Dk, anything else by name. This is the stack Altium's Layer
// Stack Manager holds; the older LayerStack_V7 accessors see no dielectrics
// on a current board.
function PhysicalLayersJSON(Board: IPCB_Board): String;
var
    Master : IPCB_MasterLayerStack;
    L      : IPCB_LayerObject;
    Items  : TStringList;
    Total  : Double;
begin
    Items := TStringList.Create;
    Total := 0;
    Master := Board.MasterLayerStack;
    L := Master.First(eLayerClass_Physical);
    while L <> nil do
    begin
        if IsCopperLayerId(L.V6_LayerID) then
        begin
            Total := Total + CoordToMMs(L.CopperThickness);
            Items.Add('{"name": ' + JSONStr(L.Name) + ', "kind": "copper", "layer_id": ' + IntToStr(L.V6_LayerID) +
                      ', "copper_um": ' + PcbDocNum('0.#', CoordToMMs(L.CopperThickness) * 1000) + '}');
        end
        else if IsDielectricLayer(Master, L) then
        begin
            Total := Total + CoordToMMs(L.DielectricHeight);
            Items.Add('{"name": ' + JSONStr(L.Name) + ', "kind": ' + JSONStr(DielectricTypeName(L.DielectricType)) +
                      ', "height_um": ' + PcbDocNum('0.#', CoordToMMs(L.DielectricHeight) * 1000) +
                      ', "material": ' + JSONStr(L.DielectricMaterial) +
                      ', "dk": ' + PcbDocNum('0.###', L.DielectricConstant) + '}');
        end
        else
            Items.Add('{"name": ' + JSONStr(L.Name) + ', "kind": "other", "layer_id": ' + IntToStr(L.V6_LayerID) + '}');
        L := Master.Next(eLayerClass_Physical, L);
    end;
    Result := '"physical_count": ' + IntToStr(Items.Count) + ', "total_thickness_mm": ' + PcbDocNum('0.###', Total) +
              ', ' + BuildJSONArray(Items, 'physical');
    Items.Free;
end;

function LayerStackJSON(Board: IPCB_Board): String;
var
    Stack  : IPCB_LayerStack_V7;
    L      : IPCB_LayerObject_V7;
    Master : IPCB_MasterLayerStack;
    Layers, Subs : TStringList;
    i      : Integer;
begin
    Layers := TStringList.Create;
    Subs := TStringList.Create;
    Stack := Board.LayerStack_V7;
    L := Stack.FirstLayer;
    while L <> nil do
    begin
        Layers.Add('{"name": ' + JSONStr(L.Name) + ', "layer_id": ' + IntToStr(L.V6_LayerID) +
                   ', "copper_um": ' + PcbDocNum('0.#', CoordToMMs(L.CopperThickness) * 1000) + '}');
        L := Stack.NextLayer(L);
    end;
    Master := Board.MasterLayerStack;
    if Master <> nil then
        for i := 0 to Master.SubstackCount - 1 do
            Subs.Add('{"name": ' + JSONStr(Master.Substacks[i].Name) +
                     ', "is_flex": ' + JSONBool(Master.Substacks[i].IsFlex) + '}');
    Result := '"copper_layers": ' + IntToStr(Stack.LayersInStackCount) +
              ', ' + BuildJSONArray(Layers, 'layers') +
              ', ' + PhysicalLayersJSON(Board) +
              ', ' + BuildJSONArray(Subs, 'substacks');
    Layers.Free;
    Subs.Free;
end;

// ---------------------------------------------------------------------------
// create_pcb_document
// ---------------------------------------------------------------------------

function CreatePcbDocumentReport(ProjectPath, PcbPath, Units: String): String;
var
    Project, Focused : IProject;
    Doc     : IServerDocument;
    Board   : IPCB_Board;
    Existed, Added : Boolean;
begin
    Project := OpenProjectAtPath(ProjectPath);
    if Project = nil then
    begin
        Result := PcbDocError('project not found or cannot be opened: ' + ProjectPath);
        Exit;
    end;
    Existed := FileExists(PcbPath);
    if Existed then
    begin
        Doc := Client.GetDocumentByPath(PcbPath);
        if Doc = nil then Doc := Client.OpenDocument('PCB', PcbPath);
        if Doc = nil then
        begin
            Result := PcbDocError('cannot open ' + PcbPath);
            Exit;
        end;
    end
    else
    begin
        GetWorkspace.DM_CreateNewDocument('PCB');
        Doc := Client.GetCurrentView.OwnerDocument;
        if Doc = nil then
        begin
            Result := PcbDocError('Altium did not create a PCB document for ' + PcbPath);
            Exit;
        end;
        // DM_CreateNewDocument attaches the board to the FOCUSED project under
        // its temporary name, and a Save As on such a member leaves a second
        // entry behind. Detach it now; it joins the project once it has a file.
        Focused := GetWorkspace.DM_FocusedProject;
        if (Focused <> nil) and (Pos('Free Documents', Focused.DM_ProjectFileName) = 0) then
            Focused.DM_RemoveSourceDocument(Doc.FileName);
        Client.ShowDocument(Doc);
        Sleep(500);
        Doc.DoSafeChangeFileNameAndSave(PcbPath, 'PCB Binary File');
    end;
    Client.ShowDocument(Doc);
    Doc.Focus;
    Board := PCBServer.GetCurrentPCBBoard;
    if Board = nil then
    begin
        Result := PcbDocError('not a PCB document: ' + PcbPath);
        Exit;
    end;
    if LowerCase(Units) = 'mm' then Board.DisplayUnit := eMetric
    else Board.DisplayUnit := eImperial;
    Added := not ProjectHasDocument(Project, PcbPath);
    if Added then Project.DM_AddSourceDocument(PcbPath);
    SaveProjectFile(Project);
    SaveBoardDocument(PcbPath);
    Result := '{"success": true, "path": ' + JSONStr(PcbPath) + ', "project": ' + JSONStr(ProjectPath) +
              ', "existed": ' + JSONBool(Existed) + ', "added_to_project": ' + JSONBool(Added) +
              ', "units": ' + JSONStr(LowerCase(Units)) + ', ' + LayerStackJSON(Board) + '}';
end;

// ---------------------------------------------------------------------------
// set_pcb_layer_stack
//
// Spec (pipe-delimited, one record per line):
//   PCB|<path>                                   the board
//   LAYER|<name>|<copper_um>                     a copper layer, top to bottom
//   DIELECTRIC|<core|prepreg>|<height_um>|<material>|<dk>|<loss_tangent>
//                                                the dielectric below the last LAYER
//   MASK|<top|bottom>|<height_um>|<material>|<dk>  a surface dielectric
// ---------------------------------------------------------------------------

function MidLayerInStack(Stack: IPCB_LayerStack_V7; LayerId: Integer): Boolean;
var
    L : IPCB_LayerObject_V7;
begin
    Result := False;
    L := Stack.FirstLayer;
    while L <> nil do
    begin
        if L.V6_LayerID = LayerId then Result := True;
        L := Stack.NextLayer(L);
    end;
end;

procedure ApplyDielectricSpec(D: IPCB_DielectricLayer; Kind: String; Rec: String; HeightField: Integer);
var
    Tangent : Double;
begin
    if D = nil then
    begin
        PcbDocWarn('no dielectric object for ' + Rec);
        Exit;
    end;
    if Kind = 'core' then D.DielectricType := eCore
    else if Kind = 'prepreg' then D.DielectricType := ePrePreg
    else if Kind = 'surface' then D.DielectricType := eSurfaceMaterial
    else PcbDocWarn('unknown dielectric type "' + Kind + '" in ' + Rec);
    D.DielectricHeight := MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Rec, HeightField)) / 1000);
    if GetFieldFromPipeString(Rec, HeightField + 1) <> '' then
        D.DielectricMaterial := GetFieldFromPipeString(Rec, HeightField + 1);
    if GetFieldFromPipeString(Rec, HeightField + 2) <> '' then
        D.DielectricConstant := SafeStrToFloat(GetFieldFromPipeString(Rec, HeightField + 2));
    if GetFieldFromPipeString(Rec, HeightField + 3) <> '' then
    begin
        Tangent := SafeStrToFloat(GetFieldFromPipeString(Rec, HeightField + 3));
        if Tangent > 0 then D.DielectricLossTangent := Tangent;
    end;
end;

function SetPcbLayerStackReport(SpecPath: String): String;
var
    Spec    : TStringList;
    Names, Copper, Below, Masks : TStringList;
    PcbPath, Key, Rec : String;
    Board   : IPCB_Board;
    Stack   : IPCB_LayerStack_V7;
    Master  : IPCB_MasterLayerStack;
    L       : IPCB_LayerObject_V7;
    P       : IPCB_LayerObject;
    i, Count, Target, LayerId, Coppers, Gap : Integer;
begin
    if not FileExists(SpecPath) then
    begin
        Result := PcbDocError('spec file not found: ' + SpecPath);
        Exit;
    end;
    Spec := TStringList.Create;
    Spec.LoadFromFile(SpecPath);
    Names := TStringList.Create;
    Copper := TStringList.Create;
    Below := TStringList.Create;
    Masks := TStringList.Create;
    PcbDocWarnings := TStringList.Create;
    PcbPath := '';
    try
        for i := 0 to Spec.Count - 1 do
        begin
            Rec := Spec[i];
            Key := GetFieldFromPipeString(Rec, 0);
            if Key = 'PCB' then PcbPath := GetFieldFromPipeString(Rec, 1)
            else if Key = 'LAYER' then
            begin
                Names.Add(GetFieldFromPipeString(Rec, 1));
                Copper.Add(GetFieldFromPipeString(Rec, 2));
                Below.Add('');
            end
            else if Key = 'DIELECTRIC' then
            begin
                if Below.Count = 0 then PcbDocWarn('DIELECTRIC before any LAYER: ' + Rec)
                else Below[Below.Count - 1] := Rec;
            end
            else if Key = 'MASK' then Masks.Add(Rec)
            else if Rec <> '' then PcbDocWarn('unknown record: ' + Rec);
        end;
        Target := Names.Count;
        if Target < 2 then
        begin
            Result := PcbDocError('a stack needs at least two LAYER records');
            Exit;
        end;
        Board := OpenBoardAtPath(PcbPath);
        if Board = nil then
        begin
            Result := PcbDocError('PCB document not found or cannot be opened: ' + PcbPath);
            Exit;
        end;
        Stack := Board.LayerStack_V7;
        Master := Board.MasterLayerStack;
        PCBServer.PreProcess;
        // Bring the number of copper layers to the spec: mid layers are added
        // in index order and the unused ones removed from the bottom up.
        Count := Stack.LayersInStackCount;
        LayerId := eMidLayer1;
        while (Count < Target) and (LayerId <= eMidLayer30) do
        begin
            if not MidLayerInStack(Stack, LayerId) then
            begin
                Stack.InsertLayer(LayerId);
                Count := Count + 1;
            end;
            LayerId := LayerId + 1;
        end;
        while Count > Target do
        begin
            L := Stack.LastLayer;
            while (L <> nil) and ((L.V6_LayerID < eMidLayer1) or (L.V6_LayerID > eMidLayer30) or L.UsedByPrims) do
                L := Stack.PreviousLayer(L);
            if L = nil then
            begin
                PcbDocWarn('no free mid layer left to remove; the stack keeps ' + IntToStr(Count) + ' copper layers');
                Break;
            end;
            Stack.RemoveFromStack(L);
            Count := Count - 1;
        end;
        // Names and copper top to bottom.
        L := Stack.FirstLayer;
        i := 0;
        while (L <> nil) and (i < Names.Count) do
        begin
            L.Name := Names[i];
            L.CopperThickness := MMsToCoord(SafeStrToFloat(Copper[i]) / 1000);
            L := Stack.NextLayer(L);
            i := i + 1;
        end;
        // Dielectrics in the physical order of the master stack: the layers
        // above the first copper are the top surface, those below the last
        // copper the bottom surface, the rest the gap under copper N.
        Coppers := 0;
        Gap := 0;
        P := Master.First(eLayerClass_Physical);
        while P <> nil do
        begin
            if IsCopperLayerId(P.V6_LayerID) then
            begin
                Coppers := Coppers + 1;
                Gap := 0;
            end
            else if IsDielectricLayer(Master, P) then
            begin
                Gap := Gap + 1;
                if Coppers = 0 then
                begin
                    for i := 0 to Masks.Count - 1 do
                        if (GetFieldFromPipeString(Masks[i], 1) = 'top') and (Gap = 1) then
                            ApplyDielectricSpec(P, 'surface', Masks[i], 2);
                end
                else if Coppers >= Target then
                begin
                    for i := 0 to Masks.Count - 1 do
                        if (GetFieldFromPipeString(Masks[i], 1) = 'bottom') and (Gap = 1) then
                            ApplyDielectricSpec(P, 'surface', Masks[i], 2);
                end
                else if Below[Coppers - 1] <> '' then
                begin
                    if Gap = 1 then
                        ApplyDielectricSpec(P, GetFieldFromPipeString(Below[Coppers - 1], 1), Below[Coppers - 1], 2)
                    else
                        PcbDocWarn('a second dielectric layer "' + P.Name + '" sits under ' + Names[Coppers - 1] +
                                   '; the spec gives one, this one is left as it was');
                end
                else
                    PcbDocWarn('no DIELECTRIC under layer ' + Names[Coppers - 1] + '; "' + P.Name + '" is left as it was');
            end;
            P := Master.Next(eLayerClass_Physical, P);
        end;
        PCBServer.PostProcess;
        Board.ViewManager_UpdateLayerTabs;
        Board.ViewManager_FullUpdate;
        Board.GraphicallyInvalidate;
        SaveBoardDocument(PcbPath);
        Result := '{"success": true, "path": ' + JSONStr(PcbPath) + ', "layers_requested": ' + IntToStr(Target) +
                  ', ' + LayerStackJSON(Board) + ', ' + BuildJSONArray(PcbDocWarnings, 'warnings') + '}';
    finally
        Spec.Free;
        Names.Free;
        Copper.Free;
        Below.Free;
        Masks.Free;
        PcbDocWarnings.Free;
        PcbDocWarnings := nil;
    end;
end;

// ---------------------------------------------------------------------------
// set_board_outline
//
// Spec:
//   PCB|<path>
//   ORIGIN|<x_mm>|<y_mm>       optional: the board origin
//   POINT|<x_mm>|<y_mm>        vertices of the closed outline, in order
// ---------------------------------------------------------------------------

function SetBoardOutlineReport(SpecPath: String): String;
var
    Spec    : TStringList;
    Xs, Ys  : TStringList;
    PcbPath, Key, Rec, OriginRec : String;
    Board   : IPCB_Board;
    Seg     : TPolySegment;
    i       : Integer;
    MinX, MaxX, MinY, MaxY : Integer;
begin
    if not FileExists(SpecPath) then
    begin
        Result := PcbDocError('spec file not found: ' + SpecPath);
        Exit;
    end;
    Spec := TStringList.Create;
    Spec.LoadFromFile(SpecPath);
    Xs := TStringList.Create;
    Ys := TStringList.Create;
    PcbPath := '';
    OriginRec := '';
    try
        for i := 0 to Spec.Count - 1 do
        begin
            Rec := Spec[i];
            Key := GetFieldFromPipeString(Rec, 0);
            if Key = 'PCB' then PcbPath := GetFieldFromPipeString(Rec, 1)
            else if Key = 'ORIGIN' then OriginRec := Rec
            else if Key = 'POINT' then
            begin
                Xs.Add(GetFieldFromPipeString(Rec, 1));
                Ys.Add(GetFieldFromPipeString(Rec, 2));
            end;
        end;
        if Xs.Count < 3 then
        begin
            Result := PcbDocError('an outline needs at least three POINT records');
            Exit;
        end;
        Board := OpenBoardAtPath(PcbPath);
        if Board = nil then
        begin
            Result := PcbDocError('PCB document not found or cannot be opened: ' + PcbPath);
            Exit;
        end;
        PCBServer.PreProcess;
        if OriginRec <> '' then
        begin
            Board.XOrigin := MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(OriginRec, 1)));
            Board.YOrigin := MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(OriginRec, 2)));
        end;
        Board.BoardOutline.Invalidate;
        Board.BoardOutline.PointCount := Xs.Count;
        for i := 0 to Xs.Count - 1 do
        begin
            Seg := TPolySegment;
            Seg.Kind := ePolySegmentLine;
            Seg.vx := Board.XOrigin + MMsToCoord(SafeStrToFloat(Xs[i]));
            Seg.vy := Board.YOrigin + MMsToCoord(SafeStrToFloat(Ys[i]));
            Board.BoardOutline.Segments[i] := Seg;
        end;
        Board.BoardOutline.Validate;
        Board.BoardOutline.Rebuild;
        PCBServer.PostProcess;
        Board.GraphicallyInvalidate;
        Board.ViewManager_FullUpdate;
        SaveBoardDocument(PcbPath);
        // BoundingRectangle lags behind a rebuilt outline; the extent comes from the vertices.
        Xs.Clear;
        for i := 0 to Board.BoardOutline.PointCount - 1 do
        begin
            Seg := Board.BoardOutline.Segments[i];
            if (i = 0) or (Seg.vx < MinX) then MinX := Seg.vx;
            if (i = 0) or (Seg.vx > MaxX) then MaxX := Seg.vx;
            if (i = 0) or (Seg.vy < MinY) then MinY := Seg.vy;
            if (i = 0) or (Seg.vy > MaxY) then MaxY := Seg.vy;
            Xs.Add('[' + PcbDocNum('0.###', CoordToMMs(Seg.vx - Board.XOrigin)) + ', ' +
                   PcbDocNum('0.###', CoordToMMs(Seg.vy - Board.YOrigin)) + ']');
        end;
        Result := '{"success": true, "path": ' + JSONStr(PcbPath) +
                  ', "points": ' + IntToStr(Board.BoardOutline.PointCount) +
                  ', ' + BuildJSONArray(Xs, 'vertices_mm') +
                  ', "width_mm": ' + PcbDocNum('0.###', CoordToMMs(MaxX - MinX)) +
                  ', "height_mm": ' + PcbDocNum('0.###', CoordToMMs(MaxY - MinY)) +
                  ', "origin_mm": [' + PcbDocNum('0.###', CoordToMMs(Board.XOrigin)) + ', ' +
                  PcbDocNum('0.###', CoordToMMs(Board.YOrigin)) + ']}';
    finally
        Spec.Free;
        Xs.Free;
        Ys.Free;
    end;
end;

// ---------------------------------------------------------------------------
// update_pcb_from_project
//
// Design » Import Changes From <project> on the board: Altium's own ECO. The
// command opens the Engineering Change Order dialog and this script waits in
// it; the server answers the dialog (Execute Changes, Close) from outside.
// ---------------------------------------------------------------------------

function CountBoardObjects(Board: IPCB_Board; ObjectId: Integer): Integer;
var
    Iter : IPCB_BoardIterator;
    Obj  : IPCB_Primitive;
begin
    Result := 0;
    Iter := Board.BoardIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(ObjectId));
    Iter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
    Iter.AddFilter_Method(eProcessAll);
    Obj := Iter.FirstPCBObject;
    while Obj <> nil do
    begin
        Result := Result + 1;
        Obj := Iter.NextPCBObject;
    end;
    Board.BoardIterator_Destroy(Iter);
end;

function UpdatePcbFromProjectReport(ProjectPath, PcbPath: String): String;
var
    Project : IProject;
    Board   : IPCB_Board;
    MM      : IDXPMessagesManager;
    Msg     : IDXPMessage;
    Msgs    : TStringList;
    Before, Errors, i : Integer;
begin
    Project := OpenProjectAtPath(ProjectPath);
    if Project = nil then
    begin
        Result := PcbDocError('project not found or cannot be opened: ' + ProjectPath);
        Exit;
    end;
    if not ProjectHasDocument(Project, PcbPath) then
    begin
        Result := PcbDocError('the board is not a document of the project (create_pcb_document adds it): ' + PcbPath);
        Exit;
    end;
    Board := OpenBoardAtPath(PcbPath);
    if Board = nil then
    begin
        Result := PcbDocError('PCB document not found or cannot be opened: ' + PcbPath);
        Exit;
    end;
    Before := CountBoardObjects(Board, eComponentObject);
    Project.DM_SetAsCurrentProject;
    // The Messages panel receives what the ECO could not do (a footprint not
    // found, a pin missing); cleared first so the report holds only this run.
    MM := GetWorkspace.DM_MessagesManager;
    MM.ClearMessages;
    ResetParameters;
    AddStringParameter('ObjectKind', 'Project');
    AddStringParameter('Action', 'UpdateMe');
    RunProcess('WorkspaceManager:Compare');
    // The panel also lists every difference the comparison found (one line
    // per component, pin and class member); only errors and warnings are
    // reported, the rest is counted.
    Msgs := TStringList.Create;
    Errors := 0;
    for i := 0 to MM.MessagesCount - 1 do
    begin
        Msg := MM.Messages(i);
        if (Pos('Error]', Msg.MsgClass) > 0) or (Pos('[Warning]', Msg.MsgClass) > 0) then
            Msgs.Add('{"class": ' + JSONStr(Msg.MsgClass) + ', "text": ' + JSONStr(Msg.Text) +
                     ', "document": ' + JSONStr(ExtractFileName(Msg.Document)) + '}');
        if Pos('Error]', Msg.MsgClass) > 0 then Errors := Errors + 1;
    end;
    Board := OpenBoardAtPath(PcbPath);
    if Board = nil then
    begin
        Result := PcbDocError('the board is no longer open after the ECO: ' + PcbPath);
        Exit;
    end;
    Board.ViewManager_FullUpdate;
    SaveBoardDocument(PcbPath);
    Result := '{"success": true, "path": ' + JSONStr(PcbPath) + ', "project": ' + JSONStr(ProjectPath) +
              ', "components_before": ' + IntToStr(Before) +
              ', "components": ' + IntToStr(CountBoardObjects(Board, eComponentObject)) +
              ', "nets": ' + IntToStr(CountBoardObjects(Board, eNetObject)) +
              ', "errors": ' + IntToStr(Errors) + ', "messages_total": ' + IntToStr(MM.MessagesCount) +
              ', ' + BuildJSONArray(Msgs, 'messages') + '}';
    Msgs.Free;
end;

// ---------------------------------------------------------------------------
// set_pcb_regions: the rigid-flex structure of a board - sub-stacks of the
// layer stack, split lines that cut the outline into board regions, the
// sub-stack of each region and its bending lines.
//
// Spec:
//   PCB|<path>
//   SUBSTACK|<name>|<rigid|flex>|<copper layer names in it, comma separated; * = all>
//   REGION|<name>|<substack>|<x1,y1;x2,y2;...>   a board region by its polygon (mm from the origin);
//                                                the regions must tile the board outline
//   BEND|<region>|<x1>|<y1>|<x2>|<y2>|<radius_mm>|<angle_deg>|<fold_index>
//
// Regions are given as polygons, not as split lines: on Altium 26 the split
// line of IPCB_BoardOutline.CreateSplitLine leaves no split line behind. The
// first REGION of a spec takes over the single region every board starts with.
// ---------------------------------------------------------------------------

function CoordPointMM(Board: IPCB_Board; XMm, YMm: Double): TCoordPoint;
begin
    Result := TCoordPoint;
    Result.x := Board.XOrigin + MMsToCoord(XMm);
    Result.y := Board.YOrigin + MMsToCoord(YMm);
end;

function FindSubstack(Master: IPCB_MasterLayerStack; Name: String): IPCB_LayerStack;
var
    i : Integer;
begin
    Result := nil;
    for i := 0 to Master.SubstackCount - 1 do
        if SameText(Master.Substacks[i].Name, Name) then Result := Master.Substacks[i];
end;

// Whether Name is one of the comma-separated names in List ('*' = every name).
// Names may hold spaces, so the list is split on commas alone.
function NameInList(Name, List: String): Boolean;
var
    Piped : String;
    i     : Integer;
begin
    Result := (List = '*');
    if Result then Exit;
    Piped := StringReplace(List, ',', '|', REPLACEALL);
    for i := 0 to 63 do
        if (GetFieldFromPipeString(Piped, i) <> '') and SameText(Trim(GetFieldFromPipeString(Piped, i)), Name) then
            Result := True;
end;

function SubstackLayersJSON(Master: IPCB_MasterLayerStack; Sub: IPCB_LayerStack): String;
var
    L     : IPCB_LayerObject;
    Names : TStringList;
begin
    Names := TStringList.Create;
    L := Master.First(eLayerClass_Electrical);
    while L <> nil do
    begin
        if Sub.LayerEnabled(L) then Names.Add(JSONStr(L.Name));
        L := Master.Next(eLayerClass_Electrical, L);
    end;
    Result := BuildJSONArray(Names, 'layers');
    Names.Free;
end;

function RegionsJSON(Board: IPCB_Board): String;
var
    Master  : IPCB_MasterLayerStack;
    Manager : IPCB_BoardRegionsManager;
    R       : IPCB_BoardRegion;
    Subs, Regions : TStringList;
    i       : Integer;
    SubName : String;
begin
    Subs := TStringList.Create;
    Regions := TStringList.Create;
    Master := Board.MasterLayerStack;
    for i := 0 to Master.SubstackCount - 1 do
        Subs.Add('{"name": ' + JSONStr(Master.Substacks[i].Name) + ', "is_flex": ' + JSONBool(Master.Substacks[i].IsFlex) +
                 ', ' + SubstackLayersJSON(Master, Master.Substacks[i]) + '}');
    Manager := Board.GetState_BoardRegionManager;
    for i := 0 to Manager.BoardRegionCount - 1 do
    begin
        R := Manager.BoardRegion[i];
        SubName := '';
        if R.LayerStack <> nil then SubName := R.LayerStack.Name;
        Regions.Add('{"name": ' + JSONStr(R.Name) + ', "substack": ' + JSONStr(SubName) +
                    ', "area_mm2": ' + PcbDocNum('0.#', R.Area * 0.0254 * 0.0254 / 100000000) +
                    ', "bending_lines": ' + IntToStr(R.BendingLinesCount) + '}');
    end;
    Result := BuildJSONArray(Subs, 'substacks') + ', ' + BuildJSONArray(Regions, 'regions') +
              ', "bending_lines": ' + IntToStr(Board.BoardOutline.GetState_BendingLinesCount);
    Subs.Free;
    Regions.Free;
end;

// A contour (absolute coordinates) from 'x1,y1;x2,y2;...' in mm from the board origin.
function ContourFromSpec(Board: IPCB_Board; Poly: String): IPCB_Contour;
var
    Pairs, Pair : String;
    i : Integer;
begin
    Result := PCBServer.PCBContourFactory;
    Pairs := StringReplace(Poly, ';', '|', REPLACEALL);
    for i := 0 to 1023 do
    begin
        Pair := Trim(GetFieldFromPipeString(Pairs, i));
        if Pair = '' then Break;
        Pair := StringReplace(Pair, ',', '|', REPLACEALL);
        Result.AddPoint(Board.XOrigin + MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Pair, 0))),
                        Board.YOrigin + MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Pair, 1))));
    end;
end;

function SetPcbRegionsReport(SpecPath: String): String;
var
    Spec    : TStringList;
    PcbPath, Key, Rec, Kind : String;
    Board   : IPCB_Board;
    Master  : IPCB_MasterLayerStack;
    Manager : IPCB_BoardRegionsManager;
    Sub     : IPCB_LayerStack;
    L       : IPCB_LayerObject;
    R       : IPCB_BoardRegion;
    Contour : IPCB_Contour;
    i, Regions : Integer;
begin
    if not FileExists(SpecPath) then
    begin
        Result := PcbDocError('spec file not found: ' + SpecPath);
        Exit;
    end;
    Spec := TStringList.Create;
    Spec.LoadFromFile(SpecPath);
    PcbDocWarnings := TStringList.Create;
    PcbPath := '';
    Regions := 0;
    try
        for i := 0 to Spec.Count - 1 do
            if GetFieldFromPipeString(Spec[i], 0) = 'PCB' then PcbPath := GetFieldFromPipeString(Spec[i], 1);
        Board := OpenBoardAtPath(PcbPath);
        if Board = nil then
        begin
            Result := PcbDocError('PCB document not found or cannot be opened: ' + PcbPath);
            Exit;
        end;
        Master := Board.MasterLayerStack;
        Manager := Board.GetState_BoardRegionManager;
        PCBServer.PreProcess;
        // Sub-stacks first, then the regions, then the bending lines of the regions.
        for i := 0 to Spec.Count - 1 do
        begin
            Rec := Spec[i];
            Key := GetFieldFromPipeString(Rec, 0);
            if Key = 'SUBSTACK' then
            begin
                Sub := FindSubstack(Master, GetFieldFromPipeString(Rec, 1));
                // A new board has one unnamed sub-stack; the first sub-stack of
                // the spec takes it over instead of leaving it beside the others.
                if (Sub = nil) and (Master.SubstackCount = 1) and (Master.Substacks[0].Name = 'Board Layer Stack') then
                    Sub := Master.Substacks[0];
                if Sub = nil then Sub := Master.CreateSubstack;
                Sub.Name := GetFieldFromPipeString(Rec, 1);
                Kind := LowerCase(GetFieldFromPipeString(Rec, 2));
                Sub.IsFlex := (Kind = 'flex');
                L := Master.First(eLayerClass_Electrical);
                while L <> nil do
                begin
                    if NameInList(L.Name, GetFieldFromPipeString(Rec, 3)) then Master.EnableLayer(Sub, L)
                    else Master.DisableLayer(Sub, L);
                    L := Master.Next(eLayerClass_Electrical, L);
                end;
            end
            else if Key = 'REGION' then
            begin
                // An existing region of that name is reshaped; the first new
                // region of the spec takes over the single region of a new board.
                R := Manager.GetProperty_BoardRegionByName(GetFieldFromPipeString(Rec, 1));
                if (R = nil) and (Regions = 0) and (Manager.BoardRegionCount = 1) then
                    R := Manager.BoardRegion[0];
                if R = nil then R := Manager.CreateBoardRegion;
                Regions := Regions + 1;
                R.Name := GetFieldFromPipeString(Rec, 1);
                Contour := ContourFromSpec(Board, GetFieldFromPipeString(Rec, 3));
                if Contour.Count < 3 then
                    PcbDocWarn('region ' + R.Name + ' needs at least three vertices')
                else
                    R.SetOutlineContour(Contour);
                if GetFieldFromPipeString(Rec, 2) <> '' then
                begin
                    Sub := FindSubstack(Master, GetFieldFromPipeString(Rec, 2));
                    if Sub = nil then PcbDocWarn('no sub-stack named ' + GetFieldFromPipeString(Rec, 2) + ' for region ' + R.Name)
                    else R.LayerStack := Sub;
                end;
            end;
        end;
        if Regions > 0 then Board.BoardOutline.InvalidateBoardRegions;
        for i := 0 to Spec.Count - 1 do
        begin
            Rec := Spec[i];
            Key := GetFieldFromPipeString(Rec, 0);
            if Key = 'BEND' then
            begin
                R := Manager.GetProperty_BoardRegionByName(GetFieldFromPipeString(Rec, 1));
                if R = nil then
                begin
                    PcbDocWarn('no board region named ' + GetFieldFromPipeString(Rec, 1) + ' for ' + Rec);
                    Continue;
                end;
                R.AddBendingLine(
                    CoordPointMM(Board, SafeStrToFloat(GetFieldFromPipeString(Rec, 2)), SafeStrToFloat(GetFieldFromPipeString(Rec, 3))),
                    CoordPointMM(Board, SafeStrToFloat(GetFieldFromPipeString(Rec, 4)), SafeStrToFloat(GetFieldFromPipeString(Rec, 5))),
                    StrToInt(GetFieldFromPipeString(Rec, 8)),
                    MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Rec, 6))),
                    SafeStrToFloat(GetFieldFromPipeString(Rec, 7)));
            end;
        end;
        PCBServer.PostProcess;
        Board.GraphicallyInvalidate;
        Board.ViewManager_FullUpdate;
        SaveBoardDocument(PcbPath);
        Result := '{"success": true, "path": ' + JSONStr(PcbPath) + ', ' + RegionsJSON(Board) +
                  ', ' + BuildJSONArray(PcbDocWarnings, 'warnings') + '}';
    finally
        Spec.Free;
        PcbDocWarnings.Free;
        PcbDocWarnings := nil;
    end;
end;
