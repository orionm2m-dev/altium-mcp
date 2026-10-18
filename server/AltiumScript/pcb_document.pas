// pcb_document.pas
// A PCB document from data, the way a schematic is built by edit_schematic_sheet:
//   * create_pcb_document     - a new .PcbDoc saved under a path and added to a
//                               project (an existing file is opened and added)
//   * set_pcb_layer_stack     - the copper layers and dielectrics of a board from
//                               a spec file, top to bottom
//   * set_board_outline       - the board shape from a list of vertices (mm),
//                               straight edges and arcs, and board cutouts
//   * draw_pcb_mechanical     - tracks and arcs on named mechanical layers:
//                               reference geometry from MCAD
//   * export_board_3d_model   - a STEP of the board, flat or folded along its
//                               bending lines
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

// A segment of a board outline or a cutout from the fields of a spec record,
// starting at FirstField: x|y for a straight edge to the next vertex, or
// x|y|cx|cy|r|a1|a2 for an arc to the next vertex - centre, radius and the
// angles (degrees) of the counter-clockwise run Altium stores an arc as.
function SegmentFromFields(Board: IPCB_Board; Rec: String; FirstField: Integer): TPolySegment;
var
    Seg : TPolySegment;
begin
    Seg := TPolySegment;
    Seg.vx := Board.XOrigin + MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Rec, FirstField)));
    Seg.vy := Board.YOrigin + MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Rec, FirstField + 1)));
    if Trim(GetFieldFromPipeString(Rec, FirstField + 6)) <> '' then
    begin
        Seg.Kind := ePolySegmentArc;
        Seg.cx := Board.XOrigin + MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Rec, FirstField + 2)));
        Seg.cy := Board.YOrigin + MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Rec, FirstField + 3)));
        Seg.Radius := MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Rec, FirstField + 4)));
        Seg.Angle1 := SafeStrToFloat(GetFieldFromPipeString(Rec, FirstField + 5));
        Seg.Angle2 := SafeStrToFloat(GetFieldFromPipeString(Rec, FirstField + 6));
    end
    else
        Seg.Kind := ePolySegmentLine;
    Result := Seg;
end;

// True for a region that is one of the rigid-flex board regions (the layer
// stack regions) of the board: the region iterator returns them with the
// board cutouts, and they must never be removed as cutouts.
function IsBoardRegion(Board: IPCB_Board; R: IPCB_Region): Boolean;
var
    Mgr : IPCB_BoardRegionsManager;
    i   : Integer;
begin
    Result := False;
    Mgr := Board.GetState_BoardRegionManager;
    if Mgr = nil then Exit;
    for i := 0 to Mgr.BoardRegionCount - 1 do
        if Mgr.BoardRegion(i).I_ObjectAddress = R.I_ObjectAddress then Result := True;
end;

// Every board cutout region of the board is removed, one at a time, since the
// iterator does not survive a removal.
function RemoveBoardCutouts(Board: IPCB_Board): Integer;
var
    Iter   : IPCB_BoardIterator;
    R, Hit : IPCB_Region;
begin
    Result := 0;
    repeat
        Hit := nil;
        Iter := Board.BoardIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eRegionObject));
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        R := Iter.FirstPCBObject;
        while (R <> nil) and (Hit = nil) do
        begin
            if (R.Kind = eRegionKind_BoardCutout) and not IsBoardRegion(Board, R) then Hit := R;
            R := Iter.NextPCBObject;
        end;
        Board.BoardIterator_Destroy(Iter);
        if Hit <> nil then
        begin
            Board.RemovePCBObject(Hit);
            Result := Result + 1;
        end;
    until Hit = nil;
end;

// A board cutout region from 'x,y;x,y,cx,cy,r,a1,a2;...' (the same vertex
// records as the outline, comma separated, vertices separated by semicolons).
function AddBoardCutout(Board: IPCB_Board; Shape: String): Integer;
var
    Region  : IPCB_Region;
    Vertices, Vertex : String;
    i, N    : Integer;
begin
    Result := 0;
    Vertices := StringReplace(Shape, ';', '|', REPLACEALL);
    N := 0;
    for i := 0 to 1023 do
    begin
        if Trim(GetFieldFromPipeString(Vertices, i)) = '' then Break;
        N := N + 1;
    end;
    if N < 3 then Exit;
    Region := PCBServer.PCBObjectFactory(eRegionObject, eNoDimension, eCreate_Default);
    Region.Kind := eRegionKind_BoardCutout;
    Region.Layer := eMultiLayer;
    Region.ShapeSegmentCount := N;
    for i := 0 to N - 1 do
    begin
        Vertex := StringReplace(GetFieldFromPipeString(Vertices, i), ',', '|', REPLACEALL);
        Region.ShapeSegments[i] := SegmentFromFields(Board, Vertex, 0);
    end;
    Region.UpdateContourFromShape(True);
    Board.AddPCBObject(Region);
    PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Region.I_ObjectAddress);
    Result := N;
end;

// A vertex of an outline or a cutout for a report: [x, y] for a straight edge
// to the next vertex, [x, y, "arc", cx, cy, r, a1, a2] for an arc - the
// centre, radius and the angles (degrees) of the counter-clockwise run Altium
// stores an arc as; coordinates in mm from the board origin.
function OutlineSegmentJSON(Board: IPCB_Board; Seg: TPolySegment): String;
begin
    if Seg.Kind = ePolySegmentArc then
        Result := '[' + PcbDocNum('0.####', CoordToMMs(Seg.vx - Board.XOrigin)) + ', ' +
                  PcbDocNum('0.####', CoordToMMs(Seg.vy - Board.YOrigin)) + ', "arc", ' +
                  PcbDocNum('0.####', CoordToMMs(Seg.cx - Board.XOrigin)) + ', ' +
                  PcbDocNum('0.####', CoordToMMs(Seg.cy - Board.YOrigin)) + ', ' +
                  PcbDocNum('0.####', CoordToMMs(Seg.Radius)) + ', ' +
                  PcbDocNum('0.####', Seg.Angle1) + ', ' + PcbDocNum('0.####', Seg.Angle2) + ']'
    else
        Result := '[' + PcbDocNum('0.####', CoordToMMs(Seg.vx - Board.XOrigin)) + ', ' +
                  PcbDocNum('0.####', CoordToMMs(Seg.vy - Board.YOrigin)) + ']';
end;

// Every board cutout region of the board as a JSON object: its index in the
// board's order, bounding box and shape vertices (mm from the origin).
function BoardCutoutsJSON(Board: IPCB_Board): String;
var
    Iter : IPCB_BoardIterator;
    R    : IPCB_Region;
    Cuts, Segs, Regs : TStringList;
    Rect : TCoordRect;
    i, N : Integer;
begin
    Cuts := TStringList.Create;
    Regs := TStringList.Create;
    N := 0;
    Iter := Board.BoardIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eRegionObject));
    Iter.AddFilter_LayerSet(AllLayers);
    Iter.AddFilter_Method(eProcessAll);
    R := Iter.FirstPCBObject;
    while R <> nil do
    begin
        if IsBoardRegion(Board, R) then
        begin
            Rect := R.BoundingRectangle;
            Regs.Add('{"index": ' + IntToStr(Regs.Count) + ', "name": ' + JSONStr(R.Name) +
                     ', "bbox_mm": [' + PcbDocNum('0.####', CoordToMMs(Rect.Left - Board.XOrigin)) + ', ' +
                     PcbDocNum('0.####', CoordToMMs(Rect.Bottom - Board.YOrigin)) + ', ' +
                     PcbDocNum('0.####', CoordToMMs(Rect.Right - Board.XOrigin)) + ', ' +
                     PcbDocNum('0.####', CoordToMMs(Rect.Top - Board.YOrigin)) + ']' +
                     ', "shape_segments": ' + IntToStr(R.ShapeSegmentCount) + '}');
        end
        else if R.Kind = eRegionKind_BoardCutout then
        begin
            Segs := TStringList.Create;
            for i := 0 to R.ShapeSegmentCount - 1 do
                Segs.Add(OutlineSegmentJSON(Board, R.ShapeSegments[i]));
            Rect := R.BoundingRectangle;
            Cuts.Add('{"index": ' + IntToStr(N) +
                     ', "bbox_mm": [' + PcbDocNum('0.####', CoordToMMs(Rect.Left - Board.XOrigin)) + ', ' +
                     PcbDocNum('0.####', CoordToMMs(Rect.Bottom - Board.YOrigin)) + ', ' +
                     PcbDocNum('0.####', CoordToMMs(Rect.Right - Board.XOrigin)) + ', ' +
                     PcbDocNum('0.####', CoordToMMs(Rect.Top - Board.YOrigin)) + ']' +
                     ', "shape_segments": ' + IntToStr(R.ShapeSegmentCount) +
                     ', ' + BuildJSONArray(Segs, 'vertices_mm') + '}');
            Segs.Free;
            N := N + 1;
        end;
        R := Iter.NextPCBObject;
    end;
    Board.BoardIterator_Destroy(Iter);
    Result := '"cutouts": ' + IntToStr(N) + ', ' + BuildJSONArray(Cuts, 'cutout_list') +
              ', "board_regions": ' + IntToStr(Regs.Count) + ', ' + BuildJSONArray(Regs, 'board_region_list');
    Cuts.Free;
    Regs.Free;
end;

// Board cutout regions whose bounding box lies inside the rectangle (mm from
// the origin) are removed, one at a time, like RemoveBoardCutouts.
function RemoveBoardCutoutsWithin(Board: IPCB_Board; X1, Y1, X2, Y2: Double): Integer;
var
    Iter   : IPCB_BoardIterator;
    R, Hit : IPCB_Region;
    Rect   : TCoordRect;
    L, B, Rt, T : TCoord;
begin
    Result := 0;
    L := Board.XOrigin + MMsToCoord(X1);
    B := Board.YOrigin + MMsToCoord(Y1);
    Rt := Board.XOrigin + MMsToCoord(X2);
    T := Board.YOrigin + MMsToCoord(Y2);
    repeat
        Hit := nil;
        Iter := Board.BoardIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eRegionObject));
        Iter.AddFilter_LayerSet(AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        R := Iter.FirstPCBObject;
        while (R <> nil) and (Hit = nil) do
        begin
            if (R.Kind = eRegionKind_BoardCutout) and not IsBoardRegion(Board, R) then
            begin
                Rect := R.BoundingRectangle;
                if (Rect.Left >= L) and (Rect.Right <= Rt) and (Rect.Bottom >= B) and (Rect.Top <= T) then Hit := R;
            end;
            R := Iter.NextPCBObject;
        end;
        Board.BoardIterator_Destroy(Iter);
        if Hit <> nil then
        begin
            Board.RemovePCBObject(Hit);
            Result := Result + 1;
        end;
    until Hit = nil;
end;

function SetBoardOutlineReport(SpecPath: String): String;
var
    Spec    : TStringList;
    Verts, Cuts : TStringList;
    PcbPath, Key, Rec, OriginRec : String;
    Board   : IPCB_Board;
    Seg     : TPolySegment;
    i, Arcs, Removed : Integer;
    MinX, MaxX, MinY, MaxY : Integer;
    ClearCutouts : Boolean;
    WithinRec : String;
begin
    if not FileExists(SpecPath) then
    begin
        Result := PcbDocError('spec file not found: ' + SpecPath);
        Exit;
    end;
    Spec := TStringList.Create;
    Spec.LoadFromFile(SpecPath);
    Verts := TStringList.Create;
    Cuts := TStringList.Create;
    PcbDocWarnings := TStringList.Create;
    PcbPath := '';
    OriginRec := '';
    WithinRec := '';
    ClearCutouts := False;
    Arcs := 0;
    Removed := 0;
    try
        for i := 0 to Spec.Count - 1 do
        begin
            Rec := Spec[i];
            Key := GetFieldFromPipeString(Rec, 0);
            if Key = 'PCB' then PcbPath := GetFieldFromPipeString(Rec, 1)
            else if Key = 'ORIGIN' then OriginRec := Rec
            else if (Key = 'POINT') or (Key = 'ARC') then Verts.Add(Rec)
            else if Key = 'CUTOUT' then Cuts.Add(Rec)
            else if Key = 'CLEAR_CUTOUTS' then ClearCutouts := True
            else if Key = 'REMOVE_CUTOUTS_WITHIN' then WithinRec := Rec;
        end;
        if (Verts.Count > 0) and (Verts.Count < 3) then
        begin
            Result := PcbDocError('an outline needs at least three POINT or ARC records');
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
        if Verts.Count >= 3 then
        begin
            Board.BoardOutline.Invalidate;
            Board.BoardOutline.PointCount := Verts.Count;
            for i := 0 to Verts.Count - 1 do
            begin
                Seg := SegmentFromFields(Board, Verts[i], 1);
                if Seg.Kind = ePolySegmentArc then Arcs := Arcs + 1;
                Board.BoardOutline.Segments[i] := Seg;
            end;
            Board.BoardOutline.Validate;
            Board.BoardOutline.Rebuild;
            // The board regions of a rigid-flex board keep their old shape until
            // they are rebuilt from the new outline.
            Board.BoardOutline.RebuildRegion(True, False, False);
            Board.RebuildSplitBoardRegions(True);
            Board.BoardOutline.InvalidateBoardRegions;
        end;
        if ClearCutouts then Removed := RemoveBoardCutouts(Board);
        if WithinRec <> '' then
            Removed := Removed + RemoveBoardCutoutsWithin(Board,
                SafeStrToFloat(GetFieldFromPipeString(WithinRec, 1)), SafeStrToFloat(GetFieldFromPipeString(WithinRec, 2)),
                SafeStrToFloat(GetFieldFromPipeString(WithinRec, 3)), SafeStrToFloat(GetFieldFromPipeString(WithinRec, 4)));
        for i := 0 to Cuts.Count - 1 do
            if AddBoardCutout(Board, GetFieldFromPipeString(Cuts[i], 2)) = 0 then
                PcbDocWarn('cutout ' + GetFieldFromPipeString(Cuts[i], 1) + ' needs at least three vertices');
        PCBServer.PostProcess;
        Board.GraphicallyInvalidate;
        Board.ViewManager_FullUpdate;
        SaveBoardDocument(PcbPath);
        // BoundingRectangle lags behind a rebuilt outline; the extent comes from the vertices.
        Verts.Clear;
        for i := 0 to Board.BoardOutline.PointCount - 1 do
        begin
            Seg := Board.BoardOutline.Segments[i];
            if (i = 0) or (Seg.vx < MinX) then MinX := Seg.vx;
            if (i = 0) or (Seg.vx > MaxX) then MaxX := Seg.vx;
            if (i = 0) or (Seg.vy < MinY) then MinY := Seg.vy;
            if (i = 0) or (Seg.vy > MaxY) then MaxY := Seg.vy;
            if Seg.Kind = ePolySegmentArc then
                Verts.Add('[' + PcbDocNum('0.###', CoordToMMs(Seg.vx - Board.XOrigin)) + ', ' +
                          PcbDocNum('0.###', CoordToMMs(Seg.vy - Board.YOrigin)) + ', "arc", ' +
                          PcbDocNum('0.###', CoordToMMs(Seg.Radius)) + ', ' +
                          PcbDocNum('0.##', Seg.Angle1) + ', ' + PcbDocNum('0.##', Seg.Angle2) + ']')
            else
                Verts.Add('[' + PcbDocNum('0.###', CoordToMMs(Seg.vx - Board.XOrigin)) + ', ' +
                          PcbDocNum('0.###', CoordToMMs(Seg.vy - Board.YOrigin)) + ']');
        end;
        Result := '{"success": true, "path": ' + JSONStr(PcbPath) +
                  ', "points": ' + IntToStr(Board.BoardOutline.PointCount) +
                  ', "arcs": ' + IntToStr(Arcs) +
                  ', "cutouts_added": ' + IntToStr(Cuts.Count) +
                  ', "cutouts_removed": ' + IntToStr(Removed) +
                  ', ' + BuildJSONArray(Verts, 'vertices_mm') +
                  ', "width_mm": ' + PcbDocNum('0.###', CoordToMMs(MaxX - MinX)) +
                  ', "height_mm": ' + PcbDocNum('0.###', CoordToMMs(MaxY - MinY)) +
                  ', "origin_mm": [' + PcbDocNum('0.###', CoordToMMs(Board.XOrigin)) + ', ' +
                  PcbDocNum('0.###', CoordToMMs(Board.YOrigin)) + ']' +
                  ', ' + BoardCutoutsJSON(Board) +
                  ', ' + BuildJSONArray(PcbDocWarnings, 'warnings') + '}';
    finally
        Spec.Free;
        Verts.Free;
        Cuts.Free;
        PcbDocWarnings.Free;
        PcbDocWarnings := nil;
    end;
end;

// ---------------------------------------------------------------------------
// draw_pcb_mechanical
// ---------------------------------------------------------------------------

// Tracks and arcs of one mechanical layer are removed, one at a time.
function EnabledJSON(B: Boolean): String;
begin
    if B then Result := 'true' else Result := 'false';
end;

function ClearMechanicalLayer(Board: IPCB_Board; LayerIndex: Integer): Integer;
var
    Iter   : IPCB_BoardIterator;
    P, Hit : IPCB_Primitive;
    Target : TLayer;
begin
    Result := 0;
    Target := ILayer.MechanicalLayer(LayerIndex);
    repeat
        Hit := nil;
        Iter := Board.BoardIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject));
        Iter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        P := Iter.FirstPCBObject;
        while (P <> nil) and (Hit = nil) do
        begin
            if P.Layer = Target then Hit := P;
            P := Iter.NextPCBObject;
        end;
        Board.BoardIterator_Destroy(Iter);
        if Hit <> nil then
        begin
            Board.RemovePCBObject(Hit);
            Result := Result + 1;
        end;
    until Hit = nil;
end;

function DrawPcbMechanicalReport(SpecPath: String): String;
var
    Spec     : TStringList;
    Layers   : TStringList;
    PcbPath, Key, Rec : String;
    Board    : IPCB_Board;
    Stack    : IPCB_LayerStack_V7;
    Mech     : IPCB_MechanicalLayer;
    Track    : IPCB_Track;
    Arc      : IPCB_Arc;
    i, N, Tracks, Arcs, Cleared : Integer;
begin
    if not FileExists(SpecPath) then
    begin
        Result := PcbDocError('spec file not found: ' + SpecPath);
        Exit;
    end;
    Spec := TStringList.Create;
    Spec.LoadFromFile(SpecPath);
    Layers := TStringList.Create;
    PcbDocWarnings := TStringList.Create;
    PcbPath := '';
    Tracks := 0;
    Arcs := 0;
    Cleared := 0;
    try
        for i := 0 to Spec.Count - 1 do
            if GetFieldFromPipeString(Spec[i], 0) = 'PCB' then PcbPath := GetFieldFromPipeString(Spec[i], 1);
        Board := OpenBoardAtPath(PcbPath);
        if Board = nil then
        begin
            Result := PcbDocError('PCB document not found or cannot be opened: ' + PcbPath);
            Exit;
        end;
        Stack := Board.LayerStack_V7;
        PCBServer.PreProcess;
        for i := 0 to Spec.Count - 1 do
        begin
            Rec := Spec[i];
            Key := GetFieldFromPipeString(Rec, 0);
            if Key = 'PCB' then Continue;
            N := Round(SafeStrToFloat(GetFieldFromPipeString(Rec, 1)));
            if (N < 1) or (N > 32) then
            begin
                PcbDocWarn('mechanical layer index out of range in ' + Rec);
                Continue;
            end;
            try
                if Key = 'LAYER' then
                begin
                    // a name of '-' disables the layer again and restores its default name
                    Mech := Stack.LayerObject_V7[ILayer.MechanicalLayer(N)];
                    if GetFieldFromPipeString(Rec, 2) = '-' then
                    begin
                        Mech.Name := 'Mechanical ' + IntToStr(N);
                        Mech.MechanicalLayerEnabled := False;
                    end
                    else
                    begin
                        Mech.MechanicalLayerEnabled := True;
                        if GetFieldFromPipeString(Rec, 2) <> '' then Mech.Name := GetFieldFromPipeString(Rec, 2);
                    end;
                    Layers.Add('{"index": ' + IntToStr(N) + ', "name": ' + JSONStr(Mech.Name) +
                               ', "enabled": ' + EnabledJSON(Mech.MechanicalLayerEnabled) + '}');
                end
                else if Key = 'CLEAR' then
                    Cleared := Cleared + ClearMechanicalLayer(Board, N)
                else if Key = 'TRACK' then
                begin
                    Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
                    Track.Layer := ILayer.MechanicalLayer(N);
                    Track.x1 := Board.XOrigin + MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Rec, 2)));
                    Track.y1 := Board.YOrigin + MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Rec, 3)));
                    Track.x2 := Board.XOrigin + MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Rec, 4)));
                    Track.y2 := Board.YOrigin + MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Rec, 5)));
                    Track.Width := MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Rec, 6)));
                    Board.AddPCBObject(Track);
                    PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Track.I_ObjectAddress);
                    Tracks := Tracks + 1;
                end
                else if Key = 'ARC' then
                begin
                    Arc := PCBServer.PCBObjectFactory(eArcObject, eNoDimension, eCreate_Default);
                    Arc.Layer := ILayer.MechanicalLayer(N);
                    Arc.XCenter := Board.XOrigin + MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Rec, 2)));
                    Arc.YCenter := Board.YOrigin + MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Rec, 3)));
                    Arc.Radius := MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Rec, 4)));
                    Arc.StartAngle := SafeStrToFloat(GetFieldFromPipeString(Rec, 5));
                    Arc.EndAngle := SafeStrToFloat(GetFieldFromPipeString(Rec, 6));
                    Arc.LineWidth := MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Rec, 7)));
                    Board.AddPCBObject(Arc);
                    PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Arc.I_ObjectAddress);
                    Arcs := Arcs + 1;
                end;
            except
                PcbDocWarn('record failed: ' + Rec);
            end;
        end;
        PCBServer.PostProcess;
        Board.GraphicallyInvalidate;
        Board.ViewManager_FullUpdate;
        SaveBoardDocument(PcbPath);
        Result := '{"success": true, "path": ' + JSONStr(PcbPath) +
                  ', ' + BuildJSONArray(Layers, 'layers') +
                  ', "tracks": ' + IntToStr(Tracks) + ', "arcs": ' + IntToStr(Arcs) +
                  ', "cleared": ' + IntToStr(Cleared) +
                  ', ' + BuildJSONArray(PcbDocWarnings, 'warnings') + '}';
    finally
        Spec.Free;
        Layers.Free;
        PcbDocWarnings.Free;
        PcbDocWarnings := nil;
    end;
end;


// ---------------------------------------------------------------------------
// export_board_3d_model
// ---------------------------------------------------------------------------

// The 3D model export of File » Export » STEP 3D, through the PCB server's
// exporter: every component as its STEP model, all holes, no copper, and
// the board flat or folded along its bending lines (rate 0..100 %).
function ExportBoard3DModelReport(PcbPath, OutputPath, Folded, FoldRate: String): String;
var
    Board    : IPCB_Board;
    Exporter : IPCB_Board3DModelExporter;
    Project  : IProject;
    Rate     : Integer;
    Size     : Integer;
    Stream   : TFileStream;
    Ok       : String;
begin
    Board := OpenBoardAtPath(PcbPath);
    if Board = nil then
    begin
        Result := PcbDocError('PCB document not found or cannot be opened: ' + PcbPath);
        Exit;
    end;
    Exporter := PCBServer.Board3DModelExporter;
    if Exporter = nil then
    begin
        Result := PcbDocError('the PCB server has no 3D model exporter');
        Exit;
    end;
    Rate := 100;
    if Trim(FoldRate) <> '' then Rate := Round(SafeStrToFloat(FoldRate));
    if Rate < 0 then Rate := 0;
    if Rate > 100 then Rate := 100;
    Project := GetWorkspace.DM_FocusedProject;
    Exporter.SetState_ExportComponentOptions(eBoard3DModelExportComponentOptions_AllComponents);
    Exporter.SetState_ExportHolesOption(eBoard3DModelExportHolesOptions_AllHoles);
    Exporter.SetState_ExportModelsOption(eBoard3DModelExportModelsOptions_AsStep);
    Exporter.SetState_ExportCopperOption(eBoard3DModelExportCopperOptions_CopperNone);
    Exporter.SetState_ComponentSuffixType(eBoard3DModelExportComponentSuffix_None);
    Exporter.SetState_ExportFoldedBoard(LowerCase(Folded) = 'true');
    Exporter.SetState_ExportFoldedBoardRate(Rate);
    if FileExists(OutputPath) then DeleteFile(OutputPath);
    Exporter.ExportBoard3DModel(OutputPath, eBoard3DModelExportFormat_Step, Board, Project);
    Size := -1;
    if FileExists(OutputPath) then
    begin
        Stream := TFileStream.Create(OutputPath, fmOpenRead);
        Size := Stream.Size;
        Stream.Free;
    end;
    Ok := 'false';
    if Size > 0 then Ok := 'true';
    Result := '{"success": ' + Ok + ', "path": ' + JSONStr(OutputPath) +
              ', "size_bytes": ' + IntToStr(Size) +
              ', "folded": ' + LowerCase(Folded) +
              ', "fold_rate": ' + IntToStr(Rate) + '}';
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
              ', "split_lines": ' + IntToStr(Board.BoardOutline.GetState_SplitLineCount) +
              ', "bending_lines": ' + IntToStr(Board.BoardOutline.GetState_BendingLinesCount);
    Subs.Free;
    Regions.Free;
end;

// The board shape read back without changing the board: origin, outline
// vertices with their arcs, every board cutout and the rigid-flex structure.
function GetBoardOutlineReport(PcbPath: String): String;
var
    Board : IPCB_Board;
    Seg   : TPolySegment;
    Verts : TStringList;
    i, Arcs : Integer;
    MinX, MaxX, MinY, MaxY : Integer;
begin
    Board := OpenBoardAtPath(PcbPath);
    if Board = nil then
    begin
        Result := PcbDocError('PCB document not found or cannot be opened: ' + PcbPath);
        Exit;
    end;
    Verts := TStringList.Create;
    Arcs := 0;
    MinX := 0;
    MaxX := 0;
    MinY := 0;
    MaxY := 0;
    for i := 0 to Board.BoardOutline.PointCount - 1 do
    begin
        Seg := Board.BoardOutline.Segments[i];
        if Seg.Kind = ePolySegmentArc then Arcs := Arcs + 1;
        if (i = 0) or (Seg.vx < MinX) then MinX := Seg.vx;
        if (i = 0) or (Seg.vx > MaxX) then MaxX := Seg.vx;
        if (i = 0) or (Seg.vy < MinY) then MinY := Seg.vy;
        if (i = 0) or (Seg.vy > MaxY) then MaxY := Seg.vy;
        Verts.Add(OutlineSegmentJSON(Board, Seg));
    end;
    Result := '{"success": true, "path": ' + JSONStr(PcbPath) +
              ', "origin_mm": [' + PcbDocNum('0.####', CoordToMMs(Board.XOrigin)) + ', ' +
              PcbDocNum('0.####', CoordToMMs(Board.YOrigin)) + ']' +
              ', "points": ' + IntToStr(Board.BoardOutline.PointCount) +
              ', "arcs": ' + IntToStr(Arcs) +
              ', ' + BuildJSONArray(Verts, 'vertices_mm') +
              ', "width_mm": ' + PcbDocNum('0.####', CoordToMMs(MaxX - MinX)) +
              ', "height_mm": ' + PcbDocNum('0.####', CoordToMMs(MaxY - MinY)) +
              ', ' + BoardCutoutsJSON(Board) +
              ', ' + RegionsJSON(Board) + '}';
    Verts.Free;
end;

// A primitive of a violation for the report: its kind, the designator of the
// component it belongs to (if any) and the centre of its bounding box (mm from
// the board origin).
function ViolationPrimitiveJSON(Board: IPCB_Board; P: IPCB_Primitive): String;
var
    Rect  : TCoordRect;
    Owner : String;
begin
    if P = nil then
    begin
        Result := 'null';
        Exit;
    end;
    Owner := '';
    if P.InComponent then
        if P.Component <> nil then Owner := P.Component.Name.Text;
    Rect := P.BoundingRectangle;
    Result := '{"kind": ' + JSONStr(P.ObjectIDString) + ', "component": ' + JSONStr(Owner) +
              ', "layer": ' + JSONStr(Layer2String(P.Layer)) +
              ', "at_mm": [' + PcbDocNum('0.###', CoordToMMs((Rect.Left + Rect.Right) / 2 - Board.XOrigin)) + ', ' +
              PcbDocNum('0.###', CoordToMMs((Rect.Top + Rect.Bottom) / 2 - Board.YOrigin)) + ']}';
end;

// Tools > Design Rule Check > Run Design Rule Check as a batch, without the
// dialog (the text report goes to ReportPath), then every violation left on
// the board: the rule, the description Altium gives it, the two primitives
// and whether it is waived. With Run = 'false' the violations already on the
// board are listed as they are.
function RunPcbDrcReport(PcbPath, Run, ReportPath: String): String;
var
    Board  : IPCB_Board;
    Iter   : IPCB_BoardIterator;
    V      : IPCB_Violation;
    Items, Rules, Counts : TStringList;
    RuleName : String;
    i, N, Waived, Kept : Integer;
    Ran    : Boolean;
begin
    Board := OpenBoardAtPath(PcbPath);
    if Board = nil then
    begin
        Result := PcbDocError('PCB document not found or cannot be opened: ' + PcbPath);
        Exit;
    end;
    Ran := False;
    if LowerCase(Trim(Run)) <> 'false' then
    begin
        if FileExists(ReportPath) then DeleteFile(ReportPath);
        // The function returns False when the check finds violations; the report file shows that it ran.
        Board.RunBatchDesignRuleCheck(ReportPath, 1, False, False);
        Ran := FileExists(ReportPath);
    end;
    Items := TStringList.Create;
    Rules := TStringList.Create;
    Counts := TStringList.Create;
    N := 0;
    Waived := 0;
    Kept := 0;
    Iter := Board.BoardIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eViolationObject));
    Iter.AddFilter_LayerSet(AllLayers);
    Iter.AddFilter_Method(eProcessAll);
    V := Iter.FirstPCBObject;
    while V <> nil do
    begin
        RuleName := '';
        if V.Rule <> nil then RuleName := V.Rule.Name;
        if V.IsWaived then Waived := Waived + 1;
        i := Rules.IndexOf(RuleName);
        if i < 0 then
        begin
            Rules.Add(RuleName);
            Counts.Add('1');
        end
        else
            Counts[i] := IntToStr(StrToInt(Counts[i]) + 1);
        if Kept < 500 then
        begin
            Items.Add('{"rule": ' + JSONStr(RuleName) + ', "description": ' + JSONStr(V.Description) +
                      ', "waived": ' + JSONBool(V.IsWaived) +
                      ', "primitive1": ' + ViolationPrimitiveJSON(Board, V.Primitive1) +
                      ', "primitive2": ' + ViolationPrimitiveJSON(Board, V.Primitive2) + '}');
            Kept := Kept + 1;
        end;
        N := N + 1;
        V := Iter.NextPCBObject;
    end;
    Board.BoardIterator_Destroy(Iter);
    for i := 0 to Rules.Count - 1 do
        Rules[i] := '{"rule": ' + JSONStr(Rules[i]) + ', "count": ' + Counts[i] + '}';
    Result := '{"success": true, "path": ' + JSONStr(PcbPath) + ', "ran": ' + JSONBool(Ran) +
              ', "report_file": ' + JSONStr(ReportPath) + ', "report_written": ' + JSONBool(FileExists(ReportPath)) +
              ', "violations": ' + IntToStr(N) + ', "waived": ' + IntToStr(Waived) +
              ', "listed": ' + IntToStr(Kept) +
              ', ' + BuildJSONArray(Rules, 'by_rule') +
              ', ' + BuildJSONArray(Items, 'violation_list') + '}';
    Items.Free;
    Rules.Free;
    Counts.Free;
end;

// The design rule of the board with this name, or nil.
function FindRuleByName(Board: IPCB_Board; Name: String): IPCB_Rule;
var
    Iter : IPCB_BoardIterator;
    Rule : IPCB_Rule;
begin
    Result := nil;
    Iter := Board.BoardIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eRuleObject));
    Iter.AddFilter_LayerSet(AllLayers);
    Iter.AddFilter_Method(eProcessAll);
    Rule := Iter.FirstPCBObject;
    while Rule <> nil do
    begin
        if Rule.Name = Name then
        begin
            Result := Rule;
            Break;
        end;
        Rule := Iter.NextPCBObject;
    end;
    Board.BoardIterator_Destroy(Iter);
end;

// A new rule of the kind the set_pcb_rules tool names, or nil for a kind it
// does not know.
function NewRuleOfKind(Kind: String): IPCB_Rule;
begin
    Result := nil;
    if Kind = 'BoardOutlineClearance' then Result := PCBServer.PCBRuleFactory(eRule_BoardOutlineClearance)
    else if Kind = 'Clearance' then Result := PCBServer.PCBRuleFactory(eRule_Clearance)
    else if Kind = 'Width' then Result := PCBServer.PCBRuleFactory(eRule_MaxMinWidth)
    else if Kind = 'RoutingViaStyle' then Result := PCBServer.PCBRuleFactory(eRule_RoutingViaStyle)
    else if Kind = 'HoleSize' then Result := PCBServer.PCBRuleFactory(eRule_MaxMinHoleSize)
    else if Kind = 'MinimumAnnularRing' then Result := PCBServer.PCBRuleFactory(eRule_MinimumAnnularRing)
    else if Kind = 'MinimumSolderMaskSliver' then Result := PCBServer.PCBRuleFactory(eRule_MinimumSolderMaskSliver)
    else if Kind = 'HoleToHoleClearance' then Result := PCBServer.PCBRuleFactory(eRule_HoleToHoleClearance)
    else if Kind = 'SilkToSolderMaskClearance' then Result := PCBServer.PCBRuleFactory(eRule_SilkToSolderMaskClearance)
    else if Kind = 'SilkToSilkClearance' then Result := PCBServer.PCBRuleFactory(eRule_SilkToSilkClearance)
    else if Kind = 'ComponentClearance' then Result := PCBServer.PCBRuleFactory(eRule_ComponentClearance)
    else if Kind = 'Height' then Result := PCBServer.PCBRuleFactory(eRule_MaxMinHeight)
    else if Kind = 'SolderMaskExpansion' then Result := PCBServer.PCBRuleFactory(eRule_SolderMaskExpansion)
    else if Kind = 'PasteMaskExpansion' then Result := PCBServer.PCBRuleFactory(eRule_PasteMaskExpansion);
end;

// The kind name the set_pcb_rules tool uses for an existing rule (empty for
// a kind it does not set values of).
function KindNameOf(Rule: IPCB_Rule): String;
begin
    Result := '';
    if Rule.RuleKind = eRule_BoardOutlineClearance then Result := 'BoardOutlineClearance'
    else if Rule.RuleKind = eRule_Clearance then Result := 'Clearance'
    else if Rule.RuleKind = eRule_MaxMinWidth then Result := 'Width'
    else if Rule.RuleKind = eRule_RoutingViaStyle then Result := 'RoutingViaStyle'
    else if Rule.RuleKind = eRule_MaxMinHoleSize then Result := 'HoleSize'
    else if Rule.RuleKind = eRule_MinimumAnnularRing then Result := 'MinimumAnnularRing'
    else if Rule.RuleKind = eRule_MinimumSolderMaskSliver then Result := 'MinimumSolderMaskSliver'
    else if Rule.RuleKind = eRule_HoleToHoleClearance then Result := 'HoleToHoleClearance'
    else if Rule.RuleKind = eRule_SilkToSolderMaskClearance then Result := 'SilkToSolderMaskClearance'
    else if Rule.RuleKind = eRule_SilkToSilkClearance then Result := 'SilkToSilkClearance'
    else if Rule.RuleKind = eRule_ComponentClearance then Result := 'ComponentClearance'
    else if Rule.RuleKind = eRule_MaxMinHeight then Result := 'Height'
    else if Rule.RuleKind = eRule_SolderMaskExpansion then Result := 'SolderMaskExpansion'
    else if Rule.RuleKind = eRule_PasteMaskExpansion then Result := 'PasteMaskExpansion';
end;

// Set the numbers of a rule from 'key=value;key=value' (mm) the way its kind
// names them; the keys the kind has no use for are returned, space separated.
function ApplyRuleValues(Rule: IPCB_Rule; Kind, Values: String): String;
var
    Rest, Item, Key, Val : String;
    p, q, o, g : Integer;
    V     : TCoord;
    Known : Boolean;
begin
    Result := '';
    Rest := Values;
    while Rest <> '' do
    begin
        p := Pos(';', Rest);
        if p = 0 then
        begin
            Item := Rest;
            Rest := '';
        end
        else
        begin
            Item := Copy(Rest, 1, p - 1);
            Rest := Copy(Rest, p + 1, Length(Rest));
        end;
        q := Pos('=', Item);
        if q > 0 then
        begin
            Key := LowerCase(Trim(Copy(Item, 1, q - 1)));
            Val := Trim(Copy(Item, q + 1, Length(Item)));
            V := MMsToCoord(SafeStrToFloat(Val));
            Known := True;
            if Kind = 'Width' then
            begin
                if Key = 'min' then Rule.MinLimit := V
                else if Key = 'max' then Rule.MaxLimit := V
                else if Key = 'preferred' then Rule.PreferedWidth := V
                else Known := False;
            end
            else if Kind = 'RoutingViaStyle' then
            begin
                if Key = 'hole_min' then Rule.MinHoleWidth := V
                else if Key = 'hole_max' then Rule.MaxHoleWidth := V
                else if Key = 'hole_preferred' then Rule.PreferedHoleWidth := V
                else if Key = 'pad_min' then Rule.MinWidth := V
                else if Key = 'pad_max' then Rule.MaxWidth := V
                else if Key = 'pad_preferred' then Rule.PreferedWidth := V
                else Known := False;
            end
            else if Kind = 'HoleSize' then
            begin
                Rule.AbsoluteValues := True;
                if Key = 'min' then Rule.MinLimit := V
                else if Key = 'max' then Rule.MaxLimit := V
                else Known := False;
            end
            else if Kind = 'Height' then
            begin
                if Key = 'min' then Rule.MinHeight := V
                else if Key = 'max' then Rule.MaxHeight := V
                else if Key = 'preferred' then Rule.PreferedHeight := V
                else Known := False;
            end
            else if Kind = 'ComponentClearance' then
            begin
                if Key = 'gap' then Rule.Gap := V
                else if Key = 'vertical_gap' then Rule.VerticalGap := V
                else Known := False;
            end
            else if Kind = 'MinimumAnnularRing' then
            begin
                if Key = 'min' then Rule.Minimum := V
                else Known := False;
            end
            else if Kind = 'MinimumSolderMaskSliver' then
            begin
                if Key = 'gap' then Rule.MinSolderMaskSliver := V
                else Known := False;
            end
            else if Kind = 'SilkToSolderMaskClearance' then
            begin
                if Key = 'gap' then Rule.SilkToMaskGap := V
                else Known := False;
            end
            else if Kind = 'SilkToSilkClearance' then
            begin
                if Key = 'gap' then Rule.SilkToSilkClearance := V
                else Known := False;
            end
            else if (Kind = 'SolderMaskExpansion') or (Kind = 'PasteMaskExpansion') then
            begin
                if Key = 'expansion' then Rule.Expansion := V
                else Known := False;
            end
            else if Kind = 'BoardOutlineClearance' then
            begin
                // the rule keeps a matrix of object kind (arc, track, SMD pad,
                // TH pad, via, fill, polygon, region, text, hole: ids 0..9)
                // against outline, cavity and cutout edge (ids 10, 11, 12)
                if Key = 'gap' then
                begin
                    for o := 0 to 9 do
                        for g := 10 to 12 do
                        begin
                            Rule.SetClearance(o, g, V);
                            Rule.SetClearance(g, o, V);
                        end;
                    Rule.Gap := V;
                end
                else Known := False;
            end
            else if Kind = 'Clearance' then
            begin
                if Key = 'gap' then Rule.Gap := V
                else if Key = 'ignore_pad_to_pad' then Rule.SetState_IgnorePadToPad(SafeStrToFloat(Val) <> 0)
                else Known := False;
            end
            else
            begin
                // HoleToHoleClearance
                if Key = 'gap' then Rule.Gap := V
                else Known := False;
            end;
            if not Known then Result := Result + Key + ' ';
        end;
    end;
    Result := Trim(Result);
end;

// Create, update or delete design rules of a board from the spec the
// set_pcb_rules tool writes (RULE|name|kind|scope1|scope2|priority|enabled|
// comment|key=value;... and DELETE|name). A rule is found by its name; one
// that is missing is created of the kind given. The board is saved.
function SetPcbRulesReport(SpecPath: String): String;
var
    Spec, Items : TStringList;
    PcbPath, Key, Rec, Name, Kind, Field, Action, Unknown : String;
    Board   : IPCB_Board;
    Rule    : IPCB_Rule;
    i, Created, Updated, Deleted : Integer;
begin
    if not FileExists(SpecPath) then
    begin
        Result := PcbDocError('spec file not found: ' + SpecPath);
        Exit;
    end;
    Spec := TStringList.Create;
    Spec.LoadFromFile(SpecPath);
    Items := TStringList.Create;
    PcbDocWarnings := TStringList.Create;
    PcbPath := '';
    Created := 0;
    Updated := 0;
    Deleted := 0;
    try
        for i := 0 to Spec.Count - 1 do
            if GetFieldFromPipeString(Spec[i], 0) = 'PCB' then PcbPath := GetFieldFromPipeString(Spec[i], 1);
        Board := OpenBoardAtPath(PcbPath);
        if Board = nil then
        begin
            Result := PcbDocError('PCB document not found or cannot be opened: ' + PcbPath);
            Exit;
        end;
        PCBServer.PreProcess;
        for i := 0 to Spec.Count - 1 do
        begin
            Rec := Spec[i];
            Key := GetFieldFromPipeString(Rec, 0);
            Name := GetFieldFromPipeString(Rec, 1);
            if Key = 'DELETE' then
            begin
                Rule := FindRuleByName(Board, Name);
                if Rule = nil then PcbDocWarn('no rule named ' + Name)
                else
                begin
                    Board.RemovePCBObject(Rule);
                    Deleted := Deleted + 1;
                    Items.Add('{"name": ' + JSONStr(Name) + ', "action": "deleted"}');
                end;
            end
            else if Key = 'RULE' then
            begin
                Kind := GetFieldFromPipeString(Rec, 2);
                Rule := FindRuleByName(Board, Name);
                Action := '';
                if Rule = nil then
                begin
                    Rule := NewRuleOfKind(Kind);
                    if Rule = nil then PcbDocWarn('rule ' + Name + ': unknown kind "' + Kind + '"')
                    else
                    begin
                        Rule.Name := Name;
                        Board.AddPCBObject(Rule);
                        Action := 'created';
                        Created := Created + 1;
                    end;
                end
                else
                begin
                    Kind := KindNameOf(Rule);
                    PCBServer.SendMessageToRobots(Rule.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
                    Action := 'updated';
                    Updated := Updated + 1;
                end;
                if Rule <> nil then
                begin
                    Field := GetFieldFromPipeString(Rec, 3);
                    if Field <> '' then Rule.Scope1Expression := Field;
                    Field := GetFieldFromPipeString(Rec, 4);
                    if Field <> '' then Rule.Scope2Expression := Field;
                    Field := GetFieldFromPipeString(Rec, 6);
                    if Field <> '' then Rule.DRCEnabled := (LowerCase(Field) = 'true');
                    Field := GetFieldFromPipeString(Rec, 7);
                    if Field <> '' then Rule.Comment := Field;
                    Unknown := ApplyRuleValues(Rule, Kind, GetFieldFromPipeString(Rec, 8));
                    Field := GetFieldFromPipeString(Rec, 5);
                    if Field <> '' then
                    begin
                        // 1 is the highest; the value takes effect once the rules
                        // are re-sorted below
                        Rule.SetState_Priority(StrToInt(Field));
                    end;
                    if Action = 'updated' then
                        PCBServer.SendMessageToRobots(Rule.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);
                    if Unknown <> '' then PcbDocWarn('rule ' + Name + ' (' + Kind + '): keys not used: ' + Unknown);
                    Items.Add('{"name": ' + JSONStr(Rule.Name) + ', "kind": ' + JSONStr(Kind) +
                              ', "action": ' + JSONStr(Action) + ', "descriptor": ' + JSONStr(Rule.Descriptor) +
                              ', "scope1": ' + JSONStr(Rule.Scope1Expression) +
                              ', "scope2": ' + JSONStr(Rule.Scope2Expression) +
                              ', "priority": ' + IntToStr(Rule.Priority) +
                              ', "enabled": ' + JSONBool(Rule.DRCEnabled) + '}');
                end;
            end;
        end;
        // the priorities set above take effect once the rules are re-sorted
        Board.SortRulesByPriority;
        PCBServer.PostProcess;
        Board.GraphicallyInvalidate;
        SaveBoardDocument(PcbPath);
        Result := '{"success": true, "path": ' + JSONStr(PcbPath) +
                  ', "created": ' + IntToStr(Created) + ', "updated": ' + IntToStr(Updated) +
                  ', "deleted": ' + IntToStr(Deleted) +
                  ', ' + BuildJSONArray(Items, 'rules') +
                  ', ' + BuildJSONArray(PcbDocWarnings, 'warnings') + '}';
    finally
        Spec.Free;
        Items.Free;
        PcbDocWarnings.Free;
        PcbDocWarnings := nil;
    end;
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

// Ray casting over the vertices of a contour; the coordinates are taken as
// doubles so the products of board coordinates cannot overflow.
function PointInContour(C: IPCB_Contour; X, Y: Integer): Boolean;
var
    i, j : Integer;
    xi, yi, xj, yj, px, py : Double;
begin
    Result := False;
    if C = nil then Exit;
    px := X;
    py := Y;
    j := C.Count - 1;
    for i := 0 to C.Count - 1 do
    begin
        xi := C.x[i]; yi := C.y[i];
        xj := C.x[j]; yj := C.y[j];
        if ((yi > py) <> (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi) then
            Result := not Result;
        j := i;
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
    i, j, Regions, Splits : Integer;
    Hit     : IPCB_BoardRegion;
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
        Manager := Board.GetState_BoardRegionManager;
        PCBServer.PreProcess;
        // Split lines first: Altium derives the board regions from them, with the
        // arcs of the outline, once the board is asked to rebuild them.
        for i := 0 to Spec.Count - 1 do
            if GetFieldFromPipeString(Spec[i], 0) = 'SPLIT' then
            begin
                Board.BoardOutline.CreateSplitLine(
                    CoordPointMM(Board, SafeStrToFloat(GetFieldFromPipeString(Spec[i], 1)), SafeStrToFloat(GetFieldFromPipeString(Spec[i], 2))),
                    CoordPointMM(Board, SafeStrToFloat(GetFieldFromPipeString(Spec[i], 3)), SafeStrToFloat(GetFieldFromPipeString(Spec[i], 4))));
                Splits := Splits + 1;
            end;
        if Splits > 0 then
        begin
            Board.RebuildSplitBoardRegions(True);
            Board.BoardOutline.InvalidateBoardRegions;
        end;
        // Sub-stacks, then the regions, then the bending lines of the regions.
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
            end
            else if Key = 'REGION_AT' then
            begin
                // The region holding the point gets the name and the sub-stack.
                Hit := nil;
                for j := 0 to Manager.BoardRegionCount - 1 do
                    if PointInContour(Manager.BoardRegion[j].MainContour,
                                      Board.XOrigin + MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Rec, 3))),
                                      Board.YOrigin + MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Rec, 4)))) then
                        Hit := Manager.BoardRegion[j];
                if Hit = nil then
                    PcbDocWarn('no board region holds the point of ' + Rec)
                else
                begin
                    Hit.Name := GetFieldFromPipeString(Rec, 1);
                    Regions := Regions + 1;
                    if GetFieldFromPipeString(Rec, 2) <> '' then
                    begin
                        Sub := FindSubstack(Master, GetFieldFromPipeString(Rec, 2));
                        if Sub = nil then PcbDocWarn('no sub-stack named ' + GetFieldFromPipeString(Rec, 2) + ' for region ' + Hit.Name)
                        else Hit.LayerStack := Sub;
                    end;
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

// ---------------------------------------------------------------------------
// set_pcb_footprints: replace the footprint of placed components with another
// pattern of a PCB library, keeping position, side, rotation, designator,
// comment, pad nets and the link to the schematic component - the change
// Design » Import Changes makes when the comparator reports the new footprint.
//
// Spec:
//   PCB|<path>
//   FOOTPRINT|<designator>|<pattern>|<.PcbLib path>
//   NET|<designator>|<pad>|<net>        a pad net by name (empty = none)
// ---------------------------------------------------------------------------

function FindBoardComponent(Board: IPCB_Board; Designator: String): IPCB_Component;
var
    Iter : IPCB_BoardIterator;
    Comp : IPCB_Component;
begin
    Result := nil;
    Iter := Board.BoardIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eComponentObject));
    Iter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
    Iter.AddFilter_Method(eProcessAll);
    Comp := Iter.FirstPCBObject;
    while Comp <> nil do
    begin
        if Comp.Name.Text = Designator then
        begin
            Result := Comp;
            Break;
        end;
        Comp := Iter.NextPCBObject;
    end;
    Board.BoardIterator_Destroy(Iter);
end;

function FindBoardNet(Board: IPCB_Board; NetName: String): IPCB_Net;
var
    Iter : IPCB_BoardIterator;
    Net  : IPCB_Net;
begin
    Result := nil;
    Iter := Board.BoardIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eNetObject));
    Iter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
    Iter.AddFilter_Method(eProcessAll);
    Net := Iter.FirstPCBObject;
    while Net <> nil do
    begin
        if Net.Name = NetName then
        begin
            Result := Net;
            Break;
        end;
        Net := Iter.NextPCBObject;
    end;
    Board.BoardIterator_Destroy(Iter);
end;

// The pad of a component by name gets the net (empty = none).
function SetPadNet(Board: IPCB_Board; Comp: IPCB_Component; PadName, NetName: String): Boolean;
var
    Iter : IPCB_GroupIterator;
    Pad  : IPCB_Pad;
begin
    Result := False;
    Iter := Comp.GroupIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(ePadObject));
    Pad := Iter.FirstPCBObject;
    while Pad <> nil do
    begin
        if Pad.Name = PadName then
        begin
            PCBServer.SendMessageToRobots(Pad.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
            if NetName = '' then Pad.Net := nil
            else Pad.Net := FindBoardNet(Board, NetName);
            PCBServer.SendMessageToRobots(Pad.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);
            Result := True;
            Break;
        end;
        Pad := Iter.NextPCBObject;
    end;
    Comp.GroupIterator_Destroy(Iter);
end;

// A new component is built from the primitives of the library pattern and
// takes over position, side, rotation, designator, comment, the pad nets (by
// pad name) and the link to the schematic component of the old one, which is
// then removed - the change the ECO makes for a changed footprint.
function ReplaceComponentFootprint(Board: IPCB_Board; Old: IPCB_Component; Pattern, LibPath: String): Boolean;
var
    Lib     : IPCB_Library;
    LibComp : IPCB_LibComponent;
    NewComp     : IPCB_Component;
    Iter    : IPCB_GroupIterator;
    Prim, Twin : IPCB_Primitive;
    Pad     : IPCB_Pad;
    PadNames, PadNets : TStringList;
    i       : Integer;
begin
    Result := False;
    Lib := PCBServer.GetPCBLibraryByPath(LibPath);
    if Lib = nil then Lib := PCBServer.LoadPCBLibraryByPath(LibPath);
    if Lib = nil then Exit;
    LibComp := Lib.GetComponentByName(Pattern);
    if LibComp = nil then Exit;
    PadNames := TStringList.Create;
    PadNets := TStringList.Create;
    Iter := Old.GroupIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(ePadObject));
    Pad := Iter.FirstPCBObject;
    while Pad <> nil do
    begin
        if Pad.Net <> nil then
        begin
            PadNames.Add(Pad.Name);
            PadNets.Add(Pad.Net.Name);
        end;
        Pad := Iter.NextPCBObject;
    end;
    Old.GroupIterator_Destroy(Iter);
    NewComp := PCBServer.PCBObjectFactory(eComponentObject, eNoDimension, eCreate_Default);
    NewComp.Pattern := Pattern;
    // the replicas keep the library's internal coordinates, in which the
    // pattern origin is the library origin; each one is brought to the new
    // component's reference (0, 0) first, so that the flip to the other
    // side, the rotation and the move below act about the pattern origin,
    // the way Place » Component does
    Iter := LibComp.GroupIterator_Create;
    Prim := Iter.FirstPCBObject;
    while Prim <> nil do
    begin
        Twin := Prim.Replicate;
        Twin.MoveByXY(-Lib.Board.XOrigin, -Lib.Board.YOrigin);
        NewComp.AddPCBObject(Twin);
        Prim := Iter.NextPCBObject;
    end;
    LibComp.GroupIterator_Destroy(Iter);
    NewComp.Name.Text := Old.Name.Text;
    NewComp.Comment.Text := Old.Comment.Text;
    NewComp.SourceDesignator := Old.SourceDesignator;
    NewComp.SourceUniqueId := Old.SourceUniqueId;
    NewComp.SourceHierarchicalPath := Old.SourceHierarchicalPath;
    NewComp.SourceLibReference := Old.SourceLibReference;
    NewComp.SourceDescription := Old.SourceDescription;
    NewComp.SourceComponentLibrary := Old.SourceComponentLibrary;
    NewComp.SourceCompDesignItemID := Old.SourceCompDesignItemID;
    NewComp.SourceFootprintLibrary := ExtractFileName(LibPath);
    NewComp.ChannelOffset := Old.ChannelOffset;
    NewComp.ComponentKind := Old.ComponentKind;
    Board.AddPCBObject(NewComp);
    // the link to the schematic component once more, now that the part is
    // on the board (set before the add it did not survive on Altium 26)
    NewComp.SourceUniqueId := Old.SourceUniqueId;
    NewComp.SourceDesignator := Old.SourceDesignator;
    NewComp.SourceHierarchicalPath := Old.SourceHierarchicalPath;
    NewComp.SourceLibReference := Old.SourceLibReference;
    NewComp.SourceDescription := Old.SourceDescription;
    NewComp.SourceComponentLibrary := Old.SourceComponentLibrary;
    NewComp.SourceCompDesignItemID := Old.SourceCompDesignItemID;
    NewComp.ChannelOffset := Old.ChannelOffset;
    NewComp.Layer := Old.Layer;
    NewComp.Rotation := Old.Rotation;
    NewComp.MoveToXY(Old.x, Old.y);
    NewComp.NameOn := Old.NameOn;
    NewComp.CommentOn := Old.CommentOn;
    NewComp.NameAutoPosition := Old.NameAutoPosition;
    NewComp.CommentAutoPosition := Old.CommentAutoPosition;
    // one iterator per pad: a group iterator does not survive a net change
    for i := 0 to PadNames.Count - 1 do
        SetPadNet(Board, NewComp, PadNames[i], PadNets[i]);
    PadNames.Free;
    PadNets.Free;
    // the old component is only taken off the board: destroying it while the
    // undo buffer and the connectivity still refer to it crashed Altium 26
    // during the next ECO
    Board.RemovePCBObject(Old);
    Result := True;
end;

function SetPcbFootprintsReport(SpecFile: String): String;
var
    Spec    : TStringList;
    Board   : IPCB_Board;
    Comp    : IPCB_Component;
    Rec, PcbPath, Designator, Pattern, LibPath : String;
    i, Changed : Integer;
begin
    Spec := TStringList.Create;
    PcbDocWarnings := TStringList.Create;
    Changed := 0;
    try
        Spec.LoadFromFile(SpecFile);
        for i := 0 to Spec.Count - 1 do
            if GetFieldFromPipeString(Spec[i], 0) = 'PCB' then PcbPath := GetFieldFromPipeString(Spec[i], 1);
        Board := OpenBoardAtPath(PcbPath);
        if Board = nil then
        begin
            Result := PcbDocError('PCB document not found or cannot be opened: ' + PcbPath);
            Exit;
        end;
        PCBServer.PreProcess;
        for i := 0 to Spec.Count - 1 do
        begin
            Rec := Spec[i];
            if GetFieldFromPipeString(Rec, 0) = 'NET' then
            begin
                Comp := FindBoardComponent(Board, GetFieldFromPipeString(Rec, 1));
                if Comp = nil then PcbDocWarn('no component ' + GetFieldFromPipeString(Rec, 1) + ' on the board')
                else if SetPadNet(Board, Comp, GetFieldFromPipeString(Rec, 2), GetFieldFromPipeString(Rec, 3)) then Changed := Changed + 1
                else PcbDocWarn('no pad ' + GetFieldFromPipeString(Rec, 2) + ' on ' + GetFieldFromPipeString(Rec, 1));
                Continue;
            end;
            if GetFieldFromPipeString(Rec, 0) <> 'FOOTPRINT' then Continue;
            Designator := GetFieldFromPipeString(Rec, 1);
            Pattern := GetFieldFromPipeString(Rec, 2);
            LibPath := GetFieldFromPipeString(Rec, 3);
            Comp := FindBoardComponent(Board, Designator);
            if Comp = nil then
            begin
                PcbDocWarn('no component ' + Designator + ' on the board');
                Continue;
            end;
            if ReplaceComponentFootprint(Board, Comp, Pattern, LibPath) then Changed := Changed + 1
            else PcbDocWarn('footprint ' + Pattern + ' not found in ' + LibPath + ' for ' + Designator);
        end;
        PCBServer.PostProcess;
        Board.GraphicallyInvalidate;
        Board.ViewManager_FullUpdate;
        if Changed > 0 then SaveBoardDocument(PcbPath);
        Result := '{"success": true, "path": ' + JSONStr(PcbPath) + ', "changed": ' + IntToStr(Changed) +
                  ', "components": ' + IntToStr(CountBoardObjects(Board, eComponentObject)) +
                  ', ' + BuildJSONArray(PcbDocWarnings, 'warnings') + '}';
    finally
        Spec.Free;
        PcbDocWarnings.Free;
        PcbDocWarnings := nil;
    end;
end;
