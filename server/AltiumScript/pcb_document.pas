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

function DielectricJSON(D: IPCB_DielectricObject): String;
begin
    if (D = nil) or (D.DielectricType = eNoDielectric) then
    begin
        Result := 'null';
        Exit;
    end;
    Result := '{"type": ' + JSONStr(DielectricTypeName(D.DielectricType)) +
              ', "height_um": ' + PcbDocNum('0.#', CoordToMMs(D.DielectricHeight) * 1000) +
              ', "material": ' + JSONStr(D.DielectricMaterial) +
              ', "dk": ' + PcbDocNum('0.###', D.DielectricConstant) + '}';
end;

// The copper layers of the board top to bottom, each with the dielectric below
// it, and the surface dielectrics; the body of every report of this unit.
function LayerStackJSON(Board: IPCB_Board): String;
var
    Stack  : IPCB_LayerStack_V7;
    L      : IPCB_LayerObject_V7;
    Master : IPCB_MasterLayerStack;
    Layers, Subs : TStringList;
    i      : Integer;
    Total  : Double;
begin
    Layers := TStringList.Create;
    Subs := TStringList.Create;
    Total := 0;
    Stack := Board.LayerStack_V7;
    L := Stack.FirstLayer;
    while L <> nil do
    begin
        Total := Total + CoordToMMs(L.CopperThickness);
        if (L.Dielectric <> nil) and (L.Dielectric.DielectricType <> eNoDielectric) then
            Total := Total + CoordToMMs(L.Dielectric.DielectricHeight);
        Layers.Add('{"name": ' + JSONStr(L.Name) + ', "layer_id": ' + IntToStr(L.V6_LayerID) +
                   ', "copper_um": ' + PcbDocNum('0.#', CoordToMMs(L.CopperThickness) * 1000) +
                   ', "dielectric_below": ' + DielectricJSON(L.Dielectric) + '}');
        L := Stack.NextLayer(L);
    end;
    Master := Board.MasterLayerStack;
    if Master <> nil then
        for i := 0 to Master.SubstackCount - 1 do
            Subs.Add('{"name": ' + JSONStr(Master.Substacks[i].Name) +
                     ', "is_flex": ' + JSONBool(Master.Substacks[i].IsFlex) + '}');
    Result := '"copper_layers": ' + IntToStr(Stack.LayersInStackCount) +
              ', "copper_and_dielectric_mm": ' + PcbDocNum('0.###', Total) +
              ', "solder_mask_top": ' + DielectricJSON(Stack.DielectricTop) +
              ', "solder_mask_bottom": ' + DielectricJSON(Stack.DielectricBottom) +
              ', ' + BuildJSONArray(Layers, 'layers') +
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
    if Added then
    begin
        Project.DM_AddSourceDocument(PcbPath);
        SaveProjectFile(Project);
    end;
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

procedure ApplyDielectricSpec(D: IPCB_DielectricObject; Kind: String; Rec: String; HeightField: Integer);
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
    L       : IPCB_LayerObject_V7;
    i, Count, Target, LayerId : Integer;
    Order   : TStringList;
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
        // Names, copper and the dielectric below each layer, top to bottom.
        Order := TStringList.Create;
        L := Stack.FirstLayer;
        i := 0;
        while (L <> nil) and (i < Names.Count) do
        begin
            L.Name := Names[i];
            L.CopperThickness := MMsToCoord(SafeStrToFloat(Copper[i]) / 1000);
            if Below[i] <> '' then
                ApplyDielectricSpec(L.Dielectric, GetFieldFromPipeString(Below[i], 1), Below[i], 2)
            else if (L.Dielectric <> nil) and (Stack.NextLayer(L) <> nil) then
                PcbDocWarn('no DIELECTRIC under layer ' + Names[i] + '; its dielectric is left as it was');
            Order.Add(L.Name);
            L := Stack.NextLayer(L);
            i := i + 1;
        end;
        for i := 0 to Masks.Count - 1 do
        begin
            if GetFieldFromPipeString(Masks[i], 1) = 'top' then
            begin
                ApplyDielectricSpec(Stack.DielectricTop, 'surface', Masks[i], 2);
                Stack.ShowDielectricTop := True;
            end
            else if GetFieldFromPipeString(Masks[i], 1) = 'bottom' then
            begin
                ApplyDielectricSpec(Stack.DielectricBottom, 'surface', Masks[i], 2);
                Stack.ShowDielectricBottom := True;
            end
            else PcbDocWarn('MASK side must be top or bottom: ' + Masks[i]);
        end;
        PCBServer.PostProcess;
        Board.ViewManager_UpdateLayerTabs;
        Board.ViewManager_FullUpdate;
        Board.GraphicallyInvalidate;
        SaveBoardDocument(PcbPath);
        Result := '{"success": true, "path": ' + JSONStr(PcbPath) + ', "layers_requested": ' + IntToStr(Target) +
                  ', ' + LayerStackJSON(Board) + ', ' + BuildJSONArray(PcbDocWarnings, 'warnings') + '}';
        Order.Free;
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
    Rect    : TCoordRect;
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
        Rect := Board.BoardOutline.BoundingRectangle;
        Result := '{"success": true, "path": ' + JSONStr(PcbPath) +
                  ', "points": ' + IntToStr(Board.BoardOutline.PointCount) +
                  ', "width_mm": ' + PcbDocNum('0.###', CoordToMMs(Rect.Right - Rect.Left)) +
                  ', "height_mm": ' + PcbDocNum('0.###', CoordToMMs(Rect.Top - Rect.Bottom)) +
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
    Before  : Integer;
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
    ResetParameters;
    AddStringParameter('ObjectKind', 'Project');
    AddStringParameter('Action', 'UpdateMe');
    RunProcess('WorkspaceManager:Compare');
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
              ', "nets": ' + IntToStr(CountBoardObjects(Board, eNetObject)) + '}';
end;

// ---------------------------------------------------------------------------
// set_pcb_regions: the rigid-flex structure of a board - sub-stacks of the
// layer stack, split lines that cut the outline into board regions, the
// sub-stack of each region and its bending lines.
//
// Spec:
//   PCB|<path>
//   SUBSTACK|<name>|<rigid|flex>|<copper layer names in it, comma separated; * = all>
//   SPLIT|<x1>|<y1>|<x2>|<y2>              a split line across the outline (mm from the origin)
//   REGION|<name>|<x>|<y>|<substack>       the region holding the point gets the name and sub-stack
//   BEND|<region>|<x1>|<y1>|<x2>|<y2>|<radius_mm>|<angle_deg>|<fold_index>
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

function NameInList(Name, List: String): Boolean;
var
    Items : TStringList;
    i     : Integer;
begin
    Result := (List = '*');
    if Result then Exit;
    Items := TStringList.Create;
    Items.CommaText := List;
    for i := 0 to Items.Count - 1 do
        if SameText(Trim(Items[i]), Name) then Result := True;
    Items.Free;
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
              ', "split_lines": ' + IntToStr(Board.BoardOutline.GetState_SplitLineCount) +
              ', "bending_lines": ' + IntToStr(Board.BoardOutline.GetState_BendingLinesCount);
    Subs.Free;
    Regions.Free;
end;

function SetPcbRegionsReport(SpecPath: String): String;
var
    Spec    : TStringList;
    PcbPath, Key, Rec, Kind : String;
    Board   : IPCB_Board;
    Master  : IPCB_MasterLayerStack;
    Sub     : IPCB_LayerStack;
    L       : IPCB_LayerObject;
    R       : IPCB_BoardRegion;
    i, Splits : Integer;
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
    Splits := 0;
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
        PCBServer.PreProcess;
        // Sub-stacks first, then the cuts, then the regions the cuts made.
        for i := 0 to Spec.Count - 1 do
        begin
            Rec := Spec[i];
            Key := GetFieldFromPipeString(Rec, 0);
            if Key = 'SUBSTACK' then
            begin
                Sub := FindSubstack(Master, GetFieldFromPipeString(Rec, 1));
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
            else if Key = 'SPLIT' then
            begin
                Board.BoardOutline.CreateSplitLine(
                    CoordPointMM(Board, SafeStrToFloat(GetFieldFromPipeString(Rec, 1)), SafeStrToFloat(GetFieldFromPipeString(Rec, 2))),
                    CoordPointMM(Board, SafeStrToFloat(GetFieldFromPipeString(Rec, 3)), SafeStrToFloat(GetFieldFromPipeString(Rec, 4))));
                Splits := Splits + 1;
            end;
        end;
        if Splits > 0 then Board.RebuildSplitBoardRegions(True);
        for i := 0 to Spec.Count - 1 do
        begin
            Rec := Spec[i];
            Key := GetFieldFromPipeString(Rec, 0);
            if Key = 'REGION' then
            begin
                R := Board.GetBoardRegionAtPoint(Board.XOrigin + MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Rec, 2))),
                                                 Board.YOrigin + MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Rec, 3))));
                if R = nil then
                begin
                    PcbDocWarn('no board region at the point of ' + Rec);
                    Continue;
                end;
                R.Name := GetFieldFromPipeString(Rec, 1);
                if GetFieldFromPipeString(Rec, 4) <> '' then
                begin
                    Sub := FindSubstack(Master, GetFieldFromPipeString(Rec, 4));
                    if Sub = nil then PcbDocWarn('no sub-stack named ' + GetFieldFromPipeString(Rec, 4) + ' for region ' + R.Name)
                    else R.LayerStack := Sub;
                end;
            end
            else if Key = 'BEND' then
            begin
                R := Board.GetState_BoardRegionManager.GetProperty_BoardRegionByName(GetFieldFromPipeString(Rec, 1));
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
