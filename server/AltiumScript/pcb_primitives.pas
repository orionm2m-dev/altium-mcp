// Native PCB pad changes and object-specific keepouts. Coordinates are mm
// relative to the board origin. Python validates every record before dispatch.
// Existing targets are resolved and checked before the first board mutation.

// Read the actual PCB module depth, not only the client command depth.
// A script error may stop the interpreter before its finally block executes.
function ReadPcbProcessDepth(var ClientDepth, PcbDepth: Integer): String;
var
    PcbModule : IDispatch;
begin
    Result := '';
    ClientDepth := -1;
    PcbDepth := -1;
    try
        ClientDepth := Client.ProcessControl.ProcessDepth;
        PcbModule := Client.ServerModuleByName('PCB');
        if PcbModule = nil then
        begin
            Result := 'PCB server module is unavailable';
            Exit;
        end;
        PcbDepth := PcbModule.ProcessControl.ProcessDepth;
    except
        Result := 'cannot read client and PCB process depths';
    end;
end;

function PcbPrimitiveProcessGuard(Dummy: Integer): String;
var
    ClientDepth, PcbDepth : Integer;
begin
    Result := ReadPcbProcessDepth(ClientDepth, PcbDepth);
    if Result <> '' then Exit;
    if ClientDepth <> 0 then
        Result := 'client process is active; cancel the active command before editing'
    else if PcbDepth <> 0 then
        Result := 'PCB process depth is ' + IntToStr(PcbDepth) +
            '; inspect the abandoned transaction before editing; no automatic recovery';
end;

function RecoverPcbProcessReport(SpecPath: String): String;
var
    Spec : TStringList;
    Board : IPCB_Board;
    Doc : IServerDocument;
    PcbPath, Error, ReadError : String;
    ExpectedDepth, ClientBefore, PcbBefore, ClientAfter, PcbAfter : Integer;
    Repair, Called, Completed : Boolean;
begin
    if not FileExists(SpecPath) then
    begin
        Result := PcbDocError('process recovery spec does not exist');
        Exit;
    end;
    Spec := TStringList.Create;
    try
        Spec.LoadFromFile(SpecPath);
        if Spec.Count <> 2 then
        begin
            Result := PcbDocError('invalid process recovery spec');
            Exit;
        end;
        PcbPath := GetFieldFromPipeString(Spec[0], 1);
        ExpectedDepth := StrToInt(GetFieldFromPipeString(Spec[1], 1));
        Repair := GetFieldFromPipeString(Spec[1], 2) = '1';
        Called := False;
        Completed := False;
        Error := ReadPcbProcessDepth(ClientBefore, PcbBefore);
        ClientAfter := ClientBefore;
        PcbAfter := PcbBefore;
        // Do not open or focus documents to make a recovery target match.
        if Error = '' then
        begin
            if not FileExists(PcbPath) then Error := 'requested PCB file does not exist'
            else if not SameText(ExtractFileExt(PcbPath), '.PcbDoc') then Error := 'requested file must be a PcbDoc'
            else if not SameText(ExpandFileName(PcbPath), PcbPath) then Error := 'requested PCB path must be absolute and canonical';
        end;
        if Error = '' then
        begin
            if Client.CurrentView = nil then Error := 'there is no current document view'
            else
            begin
                Doc := Client.CurrentView.OwnerDocument;
                if Doc = nil then Error := 'there is no current document'
                else if not SameText(Doc.FileName, PcbPath) then Error := 'current document differs from requested PCB'
                else if not SameText(Doc.Kind, 'PCB') then Error := 'current document is not a PCB';
            end;
        end;
        if Error = '' then
        begin
            Board := GetBoardSafe(0);
            if Board = nil then Error := 'current PCB board is unavailable'
            else if not SameText(Board.FileName, PcbPath) then Error := 'current board differs from requested PCB';
        end;
        if (Error = '') and (ClientBefore <> 0) then Error := 'client process must be idle (depth 0)';
        if (Error = '') and (PcbBefore <> ExpectedDepth) then Error := 'actual PCB depth differs from expected_depth';
        if (Error = '') and Repair and (ExpectedDepth <> 1) then Error := 'repair requires expected and actual PCB depth exactly 1';
        if (Error = '') and Repair then
        begin
            // Explicitly finalize one abandoned transaction. Never reset a
            // counter, loop, alter geometry, or save a document here.
            Called := True;
            try
                PCBServer.PostProcess;
                Completed := True;
            except
                Error := 'PCB PostProcess raised an exception';
            end;
            ReadError := ReadPcbProcessDepth(ClientAfter, PcbAfter);
            if ReadError <> '' then Error := ReadError;
            if (Error = '') and ((PcbAfter <> 0) or (ClientAfter <> 0)) then
                Error := 'PostProcess returned but process depths are not idle; no further recovery attempted';
        end;
        Result := '{"success": ' + JSONBool(Error = '') + ', "error": ' + JSONStr(Error) +
            ', "path": ' + JSONStr(PcbPath) + ', "expected_depth": ' + IntToStr(ExpectedDepth) +
            ', "repair_requested": ' + JSONBool(Repair) + ', "postprocess_called": ' + JSONBool(Called) +
            ', "postprocess_completed": ' + JSONBool(Completed) + ', "saved": false' +
            ', "before": {"client_depth": ' + IntToStr(ClientBefore) + ', "pcb_depth": ' + IntToStr(PcbBefore) + '}' +
            ', "after": {"client_depth": ' + IntToStr(ClientAfter) + ', "pcb_depth": ' + IntToStr(PcbAfter) + '}}';
    finally
        Spec.Free;
    end;
end;

// Best-effort bounded trace: a logging failure must not strand a transaction.
procedure KeepoutTrace(Log: TStringList; Path, Phase: String; Index: Integer; Id: String);
begin
    try
        while Log.Count >= 64 do Log.Delete(0);
        Log.Add(FormatDateTime('yyyy-mm-dd hh:nn:ss.zzz', Now) + ' | ' + Phase +
            ' | item=' + IntToStr(Index) + ' | id=' + Id);
        Log.SaveToFile(Path);
    except
        // The native operation result remains authoritative if logging fails.
    end;
end;

function PrimitiveLayer(Name: String): TLayer;
begin
    if Name = 'top' then Result := eTopLayer
    else if Name = 'bottom' then Result := eBottomLayer
    else if Name = 'multi' then Result := eMultiLayer
    else if Name = 'all' then Result := eKeepOutLayer
    else Result := eMidLayer1 + StrToInt(Copy(Name, 4, Length(Name))) - 1;
end;

function PrimitiveCoord(Rec: String; Index: Integer): TCoord;
begin
    Result := MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Rec, Index)));
end;

function PrimitivePadOwner(Pad: IPCB_Pad): String;
begin
    Result := '';
    if Pad.Component <> nil then Result := Pad.Component.Name.Text;
end;

function FindPrimitivePad(Board: IPCB_Board; Owner, Name: String; var Count: Integer): IPCB_Pad;
var
    Iter : IPCB_BoardIterator;
    Pad  : IPCB_Pad;
begin
    Result := nil;
    Count := 0;
    Iter := Board.BoardIterator_Create;
    try
        Iter.AddFilter_ObjectSet(MkSet(ePadObject));
        Iter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        Pad := Iter.FirstPCBObject;
        while Pad <> nil do
        begin
            if SameText(Pad.Name, Name) and SameText(PrimitivePadOwner(Pad), Owner) then
            begin
                Result := Pad;
                Count := Count + 1;
            end;
            Pad := Iter.NextPCBObject;
        end;
    finally
        Board.BoardIterator_Destroy(Iter);
    end;
end;

function PrimitivePadJSON(Board: IPCB_Board; Pad: IPCB_Pad): String;
var
    NetName : String;
begin
    NetName := '';
    if Pad.Net <> nil then NetName := Pad.Net.Name;
    Result := '{"component": ' + JSONStr(PrimitivePadOwner(Pad)) + ', "name": ' + JSONStr(Pad.Name) +
        ', "x_mm": ' + PcbDocNum('0.######', CoordToMMs(Pad.X - Board.XOrigin)) +
        ', "y_mm": ' + PcbDocNum('0.######', CoordToMMs(Pad.Y - Board.YOrigin)) +
        ', "hole_mm": ' + PcbDocNum('0.######', CoordToMMs(Pad.HoleSize)) +
        ', "plated": ' + JSONBool(Pad.Plated) + ', "layer": ' + JSONStr(Layer2String(Pad.Layer)) +
        ', "mode": ' + IntToStr(Pad.Mode) +
        ', "size_x_mm": ' + PcbDocNum('0.######', CoordToMMs(Pad.TopXSize)) +
        ', "size_y_mm": ' + PcbDocNum('0.######', CoordToMMs(Pad.TopYSize)) +
        ', "shape": ' + IntToStr(Pad.TopShape) + ', "locked": ' + JSONBool(not Pad.Moveable) +
        ', "net": ' + JSONStr(NetName) + '}';
end;

function SetPcbPadsReport(SpecPath: String): String;
var
    Spec, Items : TStringList;
    Board       : IPCB_Board;
    Pad         : IPCB_Pad;
    PcbPath, Rec, Op, Owner, Name, V, Error : String;
    i, Count, Added, Updated : Integer;
    IsNew, Saved : Boolean;
    FinalLayer : TLayer;
    FinalHole : TCoord;
begin
    if not FileExists(SpecPath) then
    begin
        Result := PcbDocError('pad spec does not exist');
        Exit;
    end;
    Spec := TStringList.Create;
    Items := TStringList.Create;
    try
        Spec.LoadFromFile(SpecPath);
        PcbPath := GetFieldFromPipeString(Spec[0], 1);
        Board := OpenBoardAtPath(PcbPath);
        if Board = nil then
        begin
            Result := PcbDocError('cannot open the requested PCB');
            Exit;
        end;
        if not SameText(Board.FileName, PcbPath) then
        begin
            Result := PcbDocError('opened board path differs from the requested PCB');
            Exit;
        end;
        Error := '';
        // Preflight every target. No additions or edits until all are valid.
        for i := 1 to Spec.Count - 1 do
        begin
            Rec := Spec[i];
            Op := GetFieldFromPipeString(Rec, 1);
            Owner := GetFieldFromPipeString(Rec, 2);
            Name := GetFieldFromPipeString(Rec, 3);
            Pad := FindPrimitivePad(Board, Owner, Name, Count);
            if (Op = 'add') and (Count <> 0) then Error := 'pad already exists: ' + Owner + '.' + Name;
            if (Op = 'update') and (Count <> 1) then Error := 'pad must resolve exactly once: ' + Owner + '.' + Name;
            if Error <> '' then Break;
            if Pad <> nil then
            begin
                FinalLayer := Pad.Layer;
                FinalHole := Pad.HoleSize;
                if (GetFieldFromPipeString(Rec, 11) <> '') and (GetFieldFromPipeString(Rec, 6) = '') and
                   (Pad.Mode <> ePadMode_Simple) then
                    Error := 'shape-only change requires a simple pad: ' + Owner + '.' + Name;
            end
            else
            begin
                FinalLayer := eMultiLayer;
                FinalHole := 0;
            end;
            if GetFieldFromPipeString(Rec, 10) <> '' then FinalLayer := PrimitiveLayer(GetFieldFromPipeString(Rec, 10));
            if GetFieldFromPipeString(Rec, 8) <> '' then FinalHole := PrimitiveCoord(Rec, 8);
            if (FinalHole > 0) and (FinalLayer <> eMultiLayer) then Error := 'drilled pad must be multilayer: ' + Owner + '.' + Name;
            if Error <> '' then Break;
        end;
        if Error <> '' then
        begin
            Result := PcbDocError(Error);
            Exit;
        end;
        Added := 0;
        Updated := 0;
        Error := PcbPrimitiveProcessGuard(0);
        if Error <> '' then
        begin
            Result := PcbDocError(Error);
            Exit;
        end;
        PCBServer.PreProcess;
        try
            for i := 1 to Spec.Count - 1 do
            begin
                Rec := Spec[i];
                Op := GetFieldFromPipeString(Rec, 1);
                Owner := GetFieldFromPipeString(Rec, 2);
                Name := GetFieldFromPipeString(Rec, 3);
                Pad := FindPrimitivePad(Board, Owner, Name, Count);
                IsNew := Op = 'add';
                if IsNew then
                begin
                    Pad := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
                    Pad.Name := Name;
                    Pad.Net := nil;
                end
                else PCBServer.SendMessageToRobots(Pad.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
                if GetFieldFromPipeString(Rec, 4) <> '' then Pad.X := Board.XOrigin + PrimitiveCoord(Rec, 4);
                if GetFieldFromPipeString(Rec, 5) <> '' then Pad.Y := Board.YOrigin + PrimitiveCoord(Rec, 5);
                if GetFieldFromPipeString(Rec, 6) <> '' then
                begin
                    // Supplying dimensions explicitly requests a simple pad stack.
                    Pad.Mode := ePadMode_Simple;
                    Pad.TopXSize := PrimitiveCoord(Rec, 6);
                    Pad.TopYSize := PrimitiveCoord(Rec, 7);
                end;
                if GetFieldFromPipeString(Rec, 8) <> '' then Pad.HoleSize := PrimitiveCoord(Rec, 8);
                if GetFieldFromPipeString(Rec, 9) <> '' then Pad.Plated := GetFieldFromPipeString(Rec, 9) = '1';
                if GetFieldFromPipeString(Rec, 10) <> '' then Pad.Layer := PrimitiveLayer(GetFieldFromPipeString(Rec, 10));
                V := GetFieldFromPipeString(Rec, 11);
                if V = 'round' then Pad.TopShape := eRounded
                else if V = 'rect' then Pad.TopShape := eRectangular
                else if V = 'octagonal' then Pad.TopShape := eOctagonal;
                if GetFieldFromPipeString(Rec, 12) <> '' then Pad.Moveable := GetFieldFromPipeString(Rec, 12) = '0';
                if IsNew then
                begin
                    Pad.HoleType := 0;
                    Pad.HoleRotation := 0;
                    Board.AddPCBObject(Pad);
                    PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Pad.I_ObjectAddress);
                    Added := Added + 1;
                end
                else
                begin
                    PCBServer.SendMessageToRobots(Pad.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);
                    Updated := Updated + 1;
                end;
                Items.Add(PrimitivePadJSON(Board, Pad));
            end;
        finally
            PCBServer.PostProcess;
        end;
        Board.GraphicallyInvalidate;
        Board.ViewManager_FullUpdate;
        Saved := SaveBoardDocument(PcbPath);
        Result := '{"success": ' + JSONBool(Saved) + ', "saved": ' + JSONBool(Saved) +
            ', "path": ' + JSONStr(PcbPath) + ', "added": ' + IntToStr(Added) +
            ', "updated": ' + IntToStr(Updated) + ', ' + BuildJSONArray(Items, 'pads') + '}';
    finally
        Spec.Free;
        Items.Free;
    end;
end;

function FindKeepoutPrimitive(Board: IPCB_Board; Id: String; var Count: Integer): IPCB_Primitive;
var
    Iter : IPCB_BoardIterator;
    P : IPCB_Primitive;
    Region : IPCB_Region;
    Matches : Boolean;
begin
    Result := nil;
    Count := 0;
    Iter := Board.BoardIterator_Create;
    try
        // Search all objects to reject an ID collision with a foreign primitive.
        // Free primitive UniqueId is not reliably writable; region identity is Name.
        Iter.AddFilter_ObjectSet(AllObjects);
        Iter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        P := Iter.FirstPCBObject;
        while P <> nil do
        begin
            Matches := P.UniqueId = Id;
            if P.ObjectId = eRegionObject then
            begin
                Region := P;
                if Region.Name = Id then Matches := True;
            end;
            if Matches then
            begin
                Count := Count + 1;
                Result := P;
            end;
            P := Iter.NextPCBObject;
        end;
    finally
        Board.BoardIterator_Destroy(Iter);
    end;
end;

function SetPcbKeepoutsReport(SpecPath: String): String;
var
    Spec, Items, Trace : TStringList;
    Board : IPCB_Board;
    P : IPCB_Primitive;
    Region : IPCB_Region;
    Contour : IPCB_Contour;
    PcbPath, Rec, Id, Error, TracePath, ActualId : String;
    i, j, Count, Added, Updated, RequestedVertices, ActualVertices : Integer;
    TargetLayer : TLayer;
    IsNew, Saved, FlagsVerified, IdentityVerified, ItemIdentity : Boolean;
begin
    if not FileExists(SpecPath) then
    begin
        Result := PcbDocError('keepout spec does not exist');
        Exit;
    end;
    Spec := TStringList.Create;
    Items := TStringList.Create;
    Trace := TStringList.Create;
    TracePath := ChangeFileExt(SpecPath, '.trace.txt');
    try
        KeepoutTrace(Trace, TracePath, 'load_spec', 0, '');
        Spec.LoadFromFile(SpecPath);
        if Spec.Count < 2 then
        begin
            Result := PcbDocError('keepout spec must contain at least one region');
            Exit;
        end;
        // Also reject old-server arc/fill/track requests before opening a board.
        // The existing public region input format remains unchanged.
        for i := 1 to Spec.Count - 1 do
        begin
            if GetFieldFromPipeString(Spec[i], 2) <> 'region' then
            begin
                Result := PcbDocError('only kind=region is supported; stable identity uses Region.Name');
                Exit;
            end;
            if GetFieldFromPipeString(Spec[i], 4) <> '31' then
            begin
                Result := PcbDocError('all five keepout restrictions must be requested');
                Exit;
            end;
            for j := 1 to i - 1 do
                if GetFieldFromPipeString(Spec[j], 1) = GetFieldFromPipeString(Spec[i], 1) then
                begin
                    Result := PcbDocError('duplicate requested keepout identity');
                    Exit;
                end;
        end;
        PcbPath := GetFieldFromPipeString(Spec[0], 1);
        KeepoutTrace(Trace, TracePath, 'open_board', 0, '');
        Board := OpenBoardAtPath(PcbPath);
        if Board = nil then
        begin
            Result := PcbDocError('cannot open the requested PCB');
            Exit;
        end;
        if not SameText(Board.FileName, PcbPath) then
        begin
            Result := PcbDocError('opened board path differs from the requested PCB');
            Exit;
        end;
        KeepoutTrace(Trace, TracePath, 'board_opened', 0, '');
        Error := '';
        for i := 1 to Spec.Count - 1 do
        begin
            Rec := Spec[i];
            Id := GetFieldFromPipeString(Rec, 1);
            KeepoutTrace(Trace, TracePath, 'preflight_layer', i, Id);
            TargetLayer := PrimitiveLayer(GetFieldFromPipeString(Rec, 3));
            if (TargetLayer <> eKeepOutLayer) and not MidLayerInStack(Board.LayerStack_V7, TargetLayer) then
                Error := 'requested copper layer is not in the board stack: ' + GetFieldFromPipeString(Rec, 3);
            KeepoutTrace(Trace, TracePath, 'preflight_lookup', i, Id);
            P := FindKeepoutPrimitive(Board, Id, Count);
            if Count > 1 then Error := 'duplicate primitive identity: ' + Id;
            if P <> nil then
            begin
                if not P.IsKeepout then Error := 'identity belongs to a non-keepout primitive: ' + Id;
                if P.Component <> nil then Error := 'component-owned keepout cannot be changed: ' + Id;
                if P.InPolygon then Error := 'polygon-owned keepout cannot be changed: ' + Id;
                if P.ObjectId <> eRegionObject then Error := 'identity belongs to another primitive kind: ' + Id
                else
                begin
                    Region := P;
                    if Region.Name <> Id then Error := 'primitive UID collides with a differently named region: ' + Id;
                    if Region.Kind <> eRegionKind_Copper then Error := 'keepout region kind cannot change: ' + Id;
                end;
            end;
            if Error <> '' then Break;
        end;
        if Error <> '' then
        begin
            Result := PcbDocError(Error);
            Exit;
        end;
        Added := 0;
        Updated := 0;
        FlagsVerified := True;
        IdentityVerified := True;
        KeepoutTrace(Trace, TracePath, 'process_guard', 0, '');
        Error := PcbPrimitiveProcessGuard(0);
        if Error <> '' then
        begin
            Result := PcbDocError(Error);
            Exit;
        end;
        KeepoutTrace(Trace, TracePath, 'preprocess', 0, '');
        PCBServer.PreProcess;
        try
            KeepoutTrace(Trace, TracePath, 'preprocess_completed', 0, '');
            for i := 1 to Spec.Count - 1 do
            begin
                Rec := Spec[i];
                Id := GetFieldFromPipeString(Rec, 1);
                TargetLayer := PrimitiveLayer(GetFieldFromPipeString(Rec, 3));
                KeepoutTrace(Trace, TracePath, 'mutation_lookup', i, Id);
                P := FindKeepoutPrimitive(Board, Id, Count);
                IsNew := P = nil;
                KeepoutTrace(Trace, TracePath, 'create_or_begin_modify', i, Id);
                if IsNew then
                    P := PCBServer.PCBObjectFactory(eRegionObject, eNoDimension, eCreate_Default)
                else PCBServer.SendMessageToRobots(P.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
                Region := P;
                KeepoutTrace(Trace, TracePath, 'set_region_name', i, Id);
                Region.Name := Id;
                KeepoutTrace(Trace, TracePath, 'set_layer', i, Id);
                P.Layer := TargetLayer;
                KeepoutTrace(Trace, TracePath, 'set_net', i, Id);
                P.Net := nil;
                KeepoutTrace(Trace, TracePath, 'set_keepout_flag', i, Id);
                P.IsKeepout := True;
                KeepoutTrace(Trace, TracePath, 'set_locked', i, Id);
                P.Moveable := GetFieldFromPipeString(Rec, 5) = '0';
                KeepoutTrace(Trace, TracePath, 'contour_factory', i, Id);
                Contour := PCBServer.PCBContourFactory;
                j := 6;
                while GetFieldFromPipeString(Rec, j) <> '' do
                begin
                    Contour.AddPoint(Board.XOrigin + PrimitiveCoord(Rec, j), Board.YOrigin + PrimitiveCoord(Rec, j + 1));
                    j := j + 2;
                end;
                RequestedVertices := (j - 6) div 2;
                KeepoutTrace(Trace, TracePath, 'contour_apply', i, Id);
                Region.SetOutlineContour(Contour);
                KeepoutTrace(Trace, TracePath, 'region_kind', i, Id);
                Region.Kind := eRegionKind_Copper;
                KeepoutTrace(Trace, TracePath, 'register_or_end_modify', i, Id);
                if IsNew then
                begin
                    Board.AddPCBObject(P);
                    PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, P.I_ObjectAddress);
                    Added := Added + 1;
                end
                else
                begin
                    PCBServer.SendMessageToRobots(P.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);
                    Updated := Updated + 1;
                end;
                KeepoutTrace(Trace, TracePath, 'readback', i, Id);
                ActualId := Region.Name;
                ItemIdentity := ActualId = Id;
                if not ItemIdentity then
                begin
                    IdentityVerified := False;
                    Error := 'region identity readback mismatch; board not saved';
                end;
                if (P.Layer <> TargetLayer) or not P.IsKeepout then
                begin
                    FlagsVerified := False;
                    Error := 'keepout layer/flag readback mismatch; board not saved';
                end;
                ActualVertices := 0;
                Contour := Region.MainContour;
                if Contour <> nil then ActualVertices := Contour.Count;
                Items.Add('{"unique_id": ' + JSONStr(ActualId) + ', "requested_id": ' + JSONStr(Id) +
                    ', "actual_id": ' + JSONStr(ActualId) + ', "identity_kind": "region_name"' +
                    ', "identity_verified": ' + JSONBool(ItemIdentity) + ', "kind": "region"' +
                    ', "layer": ' + JSONStr(Layer2String(P.Layer)) + ', "is_keepout": ' + JSONBool(P.IsKeepout) +
                    ', "locked": ' + JSONBool(not P.Moveable) + ', "vertex_count": ' + IntToStr(ActualVertices) +
                    ', "requested_vertex_count": ' + IntToStr(RequestedVertices) + '}');
                if Error <> '' then Break;
            end;
        finally
            KeepoutTrace(Trace, TracePath, 'postprocess', 0, '');
            PCBServer.PostProcess;
            KeepoutTrace(Trace, TracePath, 'postprocess_completed', 0, '');
        end;
        KeepoutTrace(Trace, TracePath, 'refresh', 0, '');
        Board.GraphicallyInvalidate;
        Board.ViewManager_FullUpdate;
        Saved := False;
        KeepoutTrace(Trace, TracePath, 'save_if_verified', 0, '');
        if IdentityVerified and FlagsVerified and (Error = '') then Saved := SaveBoardDocument(PcbPath);
        KeepoutTrace(Trace, TracePath, 'completed', 0, '');
        Result := '{"success": ' + JSONBool(Saved and IdentityVerified and FlagsVerified) + ', "saved": ' + JSONBool(Saved) +
            ', "error": ' + JSONStr(Error) + ', "identity_kind": "region_name"' +
            ', "identity_verified": ' + JSONBool(IdentityVerified) + ', "keepout_flag_verified": ' + JSONBool(FlagsVerified) +
            ', "path": ' + JSONStr(PcbPath) + ', "added": ' + IntToStr(Added) + ', "updated": ' + IntToStr(Updated) +
            ', ' + BuildJSONArray(Items, 'keepouts') + '}';
    finally
        Spec.Free;
        Items.Free;
        Trace.Free;
    end;
end;

// Explicit free copper track/via batches. Geometry keys use exact native
// integer coordinates, not free-primitive UniqueId. No implicit rip-up.

function RoutingNet(Board: IPCB_Board; Name: String; var Count: Integer): IPCB_Net;
var
    Iter : IPCB_BoardIterator;
    Net : IPCB_Net;
begin
    Result := nil;
    Count := 0;
    Iter := Board.BoardIterator_Create;
    try
        Iter.AddFilter_ObjectSet(MkSet(eNetObject));
        Iter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        Net := Iter.FirstPCBObject;
        while Net <> nil do
        begin
            if Net.Name = Name then
            begin
                Result := Net;
                Count := Count + 1;
            end;
            Net := Iter.NextPCBObject;
        end;
    finally
        Board.BoardIterator_Destroy(Iter);
    end;
end;

function RoutingTrackKey(Layer: TLayer; X1, Y1, X2, Y2, Width: TCoord): String;
var
    Swap : TCoord;
begin
    if (X1 > X2) or ((X1 = X2) and (Y1 > Y2)) then
    begin
        Swap := X1; X1 := X2; X2 := Swap;
        Swap := Y1; Y1 := Y2; Y2 := Swap;
    end;
    Result := 'TRACK|' + IntToStr(Layer) + '|' + IntToStr(X1) + '|' + IntToStr(Y1) +
        '|' + IntToStr(X2) + '|' + IntToStr(Y2) + '|' + IntToStr(Width);
end;

function RoutingViaKey(X, Y, Diameter, Hole: TCoord; Low, High: TLayer): String;
begin
    Result := 'VIA|' + IntToStr(X) + '|' + IntToStr(Y) + '|' + IntToStr(Diameter) +
        '|' + IntToStr(Hole) + '|' + IntToStr(Low) + '|' + IntToStr(High);
end;

function RoutingRequestKey(Board: IPCB_Board; Rec: String): String;
begin
    if GetFieldFromPipeString(Rec, 0) = 'TRACK' then
        Result := RoutingTrackKey(PrimitiveLayer(GetFieldFromPipeString(Rec, 3)),
            Board.XOrigin + PrimitiveCoord(Rec, 4), Board.YOrigin + PrimitiveCoord(Rec, 5),
            Board.XOrigin + PrimitiveCoord(Rec, 6), Board.YOrigin + PrimitiveCoord(Rec, 7), PrimitiveCoord(Rec, 8))
    else
        Result := RoutingViaKey(Board.XOrigin + PrimitiveCoord(Rec, 5), Board.YOrigin + PrimitiveCoord(Rec, 6),
            PrimitiveCoord(Rec, 7), PrimitiveCoord(Rec, 8), eTopLayer, eBottomLayer);
end;

function RoutingObjectKey(P: IPCB_Primitive): String;
var
    Track : IPCB_Track;
    Via : IPCB_Via;
begin
    Result := '';
    if P.ObjectId = eTrackObject then
    begin
        Track := P;
        Result := RoutingTrackKey(Track.Layer, Track.X1, Track.Y1, Track.X2, Track.Y2, Track.Width);
    end
    else if P.ObjectId = eViaObject then
    begin
        Via := P;
        Result := RoutingViaKey(Via.X, Via.Y, Via.Size, Via.HoleSize, Via.LowLayer, Via.HighLayer);
    end;
end;

function FindRoutingPrimitive(Board: IPCB_Board; Key: String; var Count: Integer): IPCB_Primitive;
var
    Iter : IPCB_BoardIterator;
    P : IPCB_Primitive;
begin
    Result := nil;
    Count := 0;
    Iter := Board.BoardIterator_Create;
    try
        Iter.AddFilter_ObjectSet(MkSet(eTrackObject, eViaObject));
        Iter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        P := Iter.FirstPCBObject;
        while P <> nil do
        begin
            if RoutingObjectKey(P) = Key then
            begin
                Result := P;
                Count := Count + 1;
            end;
            P := Iter.NextPCBObject;
        end;
    finally
        Board.BoardIterator_Destroy(Iter);
    end;
end;

function RoutingOwnershipGuard(P: IPCB_Primitive; NetName: String): String;
var
    Via : IPCB_Via;
begin
    Result := '';
    if P = nil then Exit;
    if P.Component <> nil then Result := 'component-owned routing cannot be changed or adopted'
    else if P.InPolygon then Result := 'polygon-owned routing cannot be changed or adopted'
    else if P.IsKeepout then Result := 'keepout cannot be changed or adopted as routing'
    else if P.Net = nil then Result := 'same geometry belongs to an unassigned net'
    else if P.Net.Name <> NetName then Result := 'same geometry belongs to a different net';
    if (Result = '') and (P.ObjectId = eViaObject) then
    begin
        Via := P;
        if Via.Mode <> ePadMode_Simple then Result := 'custom via stack cannot be changed or adopted';
    end;
end;

function RoutingRecordGuard(Board: IPCB_Board; Rec: String): String;
var
    Kind, Action, LayerName : String;
    Layer : TLayer;
begin
    Result := '';
    Kind := GetFieldFromPipeString(Rec, 0);
    Action := GetFieldFromPipeString(Rec, 1);
    if (Action <> 'add') and (Action <> 'delete') then Result := 'routing operation must be add or delete'
    else if Trim(GetFieldFromPipeString(Rec, 2)) = '' then Result := 'routing net must be nonempty'
    else if Kind = 'TRACK' then
    begin
        LayerName := GetFieldFromPipeString(Rec, 3);
        if (LayerName = 'multi') or (LayerName = 'all') then
            Result := 'track must be on a signal layer'
        else
        begin
            Layer := PrimitiveLayer(LayerName);
            if not MidLayerInStack(Board.LayerStack_V7, Layer) then Result := 'track layer is absent from board stack';
        end;
        if PrimitiveCoord(Rec, 8) <= 0 then Result := 'track width rounds to zero or is negative';
        if (PrimitiveCoord(Rec, 4) = PrimitiveCoord(Rec, 6)) and
           (PrimitiveCoord(Rec, 5) = PrimitiveCoord(Rec, 7)) then Result := 'track endpoints coincide in native coordinates';
    end
    else if Kind = 'VIA' then
    begin
        if (GetFieldFromPipeString(Rec, 3) <> 'top') or (GetFieldFromPipeString(Rec, 4) <> 'bottom') then
            Result := 'only top-to-bottom through vias are supported';
        if not MidLayerInStack(Board.LayerStack_V7, eTopLayer) or
           not MidLayerInStack(Board.LayerStack_V7, eBottomLayer) then Result := 'through-via outer layers are absent';
        if (PrimitiveCoord(Rec, 8) <= 0) or (PrimitiveCoord(Rec, 7) <= PrimitiveCoord(Rec, 8)) then
            Result := 'via needs positive hole and larger diameter in native coordinates';
    end
    else Result := 'unsupported routing primitive kind';
end;

function RoutingReadbackJSON(Board: IPCB_Board; P: IPCB_Primitive; Action: String; Index: Integer): String;
var
    Track : IPCB_Track;
    Via : IPCB_Via;
    Geometry : String;
begin
    if P.ObjectId = eTrackObject then
    begin
        Track := P;
        Geometry := '"kind": "track", "layer": ' + JSONStr(Layer2String(Track.Layer)) +
            ', "x1_mm": ' + PcbDocNum('0.########', CoordToMMs(Track.X1 - Board.XOrigin)) +
            ', "y1_mm": ' + PcbDocNum('0.########', CoordToMMs(Track.Y1 - Board.YOrigin)) +
            ', "x2_mm": ' + PcbDocNum('0.########', CoordToMMs(Track.X2 - Board.XOrigin)) +
            ', "y2_mm": ' + PcbDocNum('0.########', CoordToMMs(Track.Y2 - Board.YOrigin)) +
            ', "width_mm": ' + PcbDocNum('0.########', CoordToMMs(Track.Width));
    end
    else
    begin
        Via := P;
        Geometry := '"kind": "via", "x_mm": ' + PcbDocNum('0.########', CoordToMMs(Via.X - Board.XOrigin)) +
            ', "y_mm": ' + PcbDocNum('0.########', CoordToMMs(Via.Y - Board.YOrigin)) +
            ', "diameter_mm": ' + PcbDocNum('0.########', CoordToMMs(Via.Size)) + ', "hole_mm": ' + PcbDocNum('0.########', CoordToMMs(Via.HoleSize)) +
            ', "start_layer": ' + JSONStr(Layer2String(Via.LowLayer)) +
            ', "end_layer": ' + JSONStr(Layer2String(Via.HighLayer)) + ', "simple_stack": ' + JSONBool(Via.Mode = ePadMode_Simple);
    end;
    Result := '{"index": ' + IntToStr(Index) + ', "operation": ' + JSONStr(Action) + ', ' + Geometry +
        ', "net": ' + JSONStr(P.Net.Name) + ', "locked": ' + JSONBool(not P.Moveable) +
        ', "native_geometry_key": ' + JSONStr(RoutingObjectKey(P)) + ', "verified": true}';
end;

function SetPcbRoutingReport(SpecPath: String): String;
var
    Spec, Keys, Actions, Items, Trace : TStringList;
    Board : IPCB_Board;
    Net : IPCB_Net;
    P : IPCB_Primitive;
    Track : IPCB_Track;
    Via : IPCB_Via;
    PcbPath, Rec, Kind, Action, NetName, Key, Error, TracePath : String;
    i, Count, NetCount, Added, Removed, Unchanged : Integer;
    Saved, Verified, Locked : Boolean;
begin
    if not FileExists(SpecPath) then
    begin
        Result := PcbDocError('routing spec does not exist');
        Exit;
    end;
    Spec := TStringList.Create;
    Keys := TStringList.Create;
    Actions := TStringList.Create;
    Items := TStringList.Create;
    Trace := TStringList.Create;
    TracePath := ChangeFileExt(SpecPath, '.trace.txt');
    try
        KeepoutTrace(Trace, TracePath, 'load_spec', 0, '');
        Spec.LoadFromFile(SpecPath);
        if Spec.Count < 2 then
        begin
            Result := PcbDocError('routing batch must not be empty');
            Exit;
        end;
        PcbPath := GetFieldFromPipeString(Spec[0], 1);
        KeepoutTrace(Trace, TracePath, 'open_board', 0, '');
        Board := OpenBoardAtPath(PcbPath);
        if Board = nil then
        begin
            Result := PcbDocError('cannot open requested PCB');
            Exit;
        end;
        if not SameText(Board.FileName, PcbPath) then
        begin
            Result := PcbDocError('current PCB differs from requested path');
            Exit;
        end;
        Error := '';
        for i := 1 to Spec.Count - 1 do
        begin
            Rec := Spec[i];
            NetName := GetFieldFromPipeString(Rec, 2);
            Action := GetFieldFromPipeString(Rec, 1);
            KeepoutTrace(Trace, TracePath, 'preflight', i, NetName);
            Error := RoutingRecordGuard(Board, Rec);
            if Error <> '' then Break;
            Net := RoutingNet(Board, NetName, NetCount);
            if NetCount <> 1 then
            begin
                Error := 'net must resolve exactly once: ' + NetName;
                Break;
            end;
            Key := RoutingRequestKey(Board, Rec);
            if Keys.IndexOf(Key) >= 0 then
            begin
                Error := 'duplicate native routing geometry in batch';
                Break;
            end;
            Keys.Add(Key);
            P := FindRoutingPrimitive(Board, Key, Count);
            if Count > 1 then Error := 'multiple existing routing objects match exact geometry'
            else if (Action = 'delete') and (Count <> 1) then Error := 'delete requires exactly one existing routing object'
            else Error := RoutingOwnershipGuard(P, NetName);
            if (Error = '') and (Action = 'add') and (P <> nil) then
                if P.Moveable <> (GetFieldFromPipeString(Rec, 9) = '0') then
                    Error := 'existing identical routing has a different lock state; no implicit update';
            if Error <> '' then Break;
        end;
        if Error <> '' then
        begin
            Result := PcbDocError(Error);
            Exit;
        end;
        KeepoutTrace(Trace, TracePath, 'process_guard', 0, '');
        Error := PcbPrimitiveProcessGuard(0);
        if Error <> '' then
        begin
            Result := PcbDocError(Error);
            Exit;
        end;
        Added := 0; Removed := 0; Unchanged := 0;
        Verified := False; Saved := False;
        KeepoutTrace(Trace, TracePath, 'preprocess', 0, '');
        PCBServer.PreProcess;
        try
            for i := 1 to Spec.Count - 1 do
            begin
                Rec := Spec[i];
                Kind := GetFieldFromPipeString(Rec, 0);
                Action := GetFieldFromPipeString(Rec, 1);
                NetName := GetFieldFromPipeString(Rec, 2);
                Key := Keys[i - 1];
                KeepoutTrace(Trace, TracePath, 'mutation_lookup', i, NetName);
                P := FindRoutingPrimitive(Board, Key, Count);
                Error := RoutingOwnershipGuard(P, NetName);
                if Count > 1 then Error := 'routing target became ambiguous';
                if (Action = 'delete') and (Count <> 1) then Error := 'delete target disappeared';
                if Error <> '' then Break;
                if Action = 'delete' then
                begin
                    KeepoutTrace(Trace, TracePath, 'remove_exact_object', i, NetName);
                    Board.RemovePCBObject(P);
                    Removed := Removed + 1;
                    Actions.Add('deleted');
                end
                else if Count = 1 then
                begin
                    Unchanged := Unchanged + 1;
                    Actions.Add('unchanged');
                end
                else
                begin
                    Net := RoutingNet(Board, NetName, NetCount);
                    if NetCount <> 1 then
                    begin
                        Error := 'net disappeared or became ambiguous';
                        Break;
                    end;
                    KeepoutTrace(Trace, TracePath, 'factory_and_geometry', i, NetName);
                    if Kind = 'TRACK' then
                    begin
                        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
                        Track.X1 := Board.XOrigin + PrimitiveCoord(Rec, 4);
                        Track.Y1 := Board.YOrigin + PrimitiveCoord(Rec, 5);
                        Track.X2 := Board.XOrigin + PrimitiveCoord(Rec, 6);
                        Track.Y2 := Board.YOrigin + PrimitiveCoord(Rec, 7);
                        Track.Width := PrimitiveCoord(Rec, 8);
                        Track.Layer := PrimitiveLayer(GetFieldFromPipeString(Rec, 3));
                        P := Track;
                    end
                    else
                    begin
                        Via := PCBServer.PCBObjectFactory(eViaObject, eNoDimension, eCreate_Default);
                        Via.X := Board.XOrigin + PrimitiveCoord(Rec, 5);
                        Via.Y := Board.YOrigin + PrimitiveCoord(Rec, 6);
                        Via.Mode := ePadMode_Simple;
                        Via.Size := PrimitiveCoord(Rec, 7);
                        Via.HoleSize := PrimitiveCoord(Rec, 8);
                        Via.Layer := eMultiLayer;
                        Via.LowLayer := eTopLayer;
                        Via.HighLayer := eBottomLayer;
                        P := Via;
                    end;
                    P.Net := Net;
                    P.IsKeepout := False;
                    P.Moveable := GetFieldFromPipeString(Rec, 9) = '0';
                    KeepoutTrace(Trace, TracePath, 'register_object', i, NetName);
                    Board.AddPCBObject(P);
                    PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, P.I_ObjectAddress);
                    Added := Added + 1;
                    Actions.Add('added');
                    if RoutingObjectKey(P) <> Key then Error := 'new routing geometry readback mismatch';
                    if Error = '' then Error := RoutingOwnershipGuard(P, NetName);
                    if Error <> '' then Break;
                end;
            end;
        finally
            KeepoutTrace(Trace, TracePath, 'postprocess', 0, '');
            PCBServer.PostProcess;
            KeepoutTrace(Trace, TracePath, 'postprocess_completed', 0, '');
        end;
        if Error = '' then
        begin
            KeepoutTrace(Trace, TracePath, 'rebuild_connectivity', 0, '');
            ResetParameters;
            AddStringParameter('Action', 'RebuildConnectivity');
            RunProcess('PCB:UpdateConnectivity');
            Board.ConnectivelyValidateNets;
            for i := 1 to Spec.Count - 1 do
            begin
                Rec := Spec[i]; NetName := GetFieldFromPipeString(Rec, 2);
                Key := Keys[i - 1]; Action := GetFieldFromPipeString(Rec, 1);
                KeepoutTrace(Trace, TracePath, 'final_readback', i, NetName);
                P := FindRoutingPrimitive(Board, Key, Count);
                if Action = 'delete' then
                begin
                    if Count <> 0 then Error := 'deleted routing is still present'
                    else Items.Add('{"index": ' + IntToStr(i) + ', "operation": "deleted", "net": ' + JSONStr(NetName) +
                        ', "native_geometry_key": ' + JSONStr(Key) + ', "verified": true}');
                end
                else
                begin
                    if Count <> 1 then Error := 'saved routing candidate is missing or ambiguous'
                    else
                    begin
                        Error := RoutingOwnershipGuard(P, NetName);
                        Locked := GetFieldFromPipeString(Rec, 9) = '1';
                        if (Error = '') and (P.Moveable = Locked) then Error := 'routing lock-state readback mismatch';
                        if Error = '' then Items.Add(RoutingReadbackJSON(Board, P, Actions[i - 1], i));
                    end;
                end;
                if Error <> '' then Break;
            end;
            Verified := Error = '';
        end;
        Board.GraphicallyInvalidate;
        Board.ViewManager_FullUpdate;
        KeepoutTrace(Trace, TracePath, 'save_if_verified', 0, '');
        if Verified and (Error = '') then Saved := SaveBoardDocument(PcbPath);
        KeepoutTrace(Trace, TracePath, 'completed', 0, '');
        Result := '{"success": ' + JSONBool(Saved and Verified) + ', "saved": ' + JSONBool(Saved) +
            ', "verified": ' + JSONBool(Verified) + ', "error": ' + JSONStr(Error) + ', "path": ' + JSONStr(PcbPath) +
            ', "added": ' + IntToStr(Added) + ', "deleted": ' + IntToStr(Removed) +
            ', "unchanged": ' + IntToStr(Unchanged) + ', "drc_run": false, ' + BuildJSONArray(Items, 'routing') + '}';
    finally
        Spec.Free; Keys.Free; Actions.Free; Items.Free; Trace.Free;
    end;
end;

// Named solid copper polygons and polygon-only cutouts. Identity is Name.
// All request targets and affected existing pours are preflighted before writes.

function PourObjectName(P: IPCB_Primitive): String;
var Poly: IPCB_Polygon; Reg: IPCB_Region;
begin
    Result := '';
    if P.ObjectId = ePolyObject then begin Poly := P; Result := Poly.Name; end
    else if P.ObjectId = eRegionObject then begin Reg := P; Result := Reg.Name; end;
end;

function FindPourObject(Board: IPCB_Board; Name: String; var Count: Integer): IPCB_Primitive;
var Iter: IPCB_BoardIterator; P: IPCB_Primitive;
begin
    Result := nil; Count := 0;
    Iter := Board.BoardIterator_Create;
    try
        Iter.AddFilter_ObjectSet(MkSet(ePolyObject, eRegionObject));
        Iter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        P := Iter.FirstPCBObject;
        while P <> nil do begin
            if SameText(PourObjectName(P), Name) then begin Result := P; Count := Count + 1; end;
            P := Iter.NextPCBObject;
        end;
    finally Board.BoardIterator_Destroy(Iter); end;
end;

function PourOwnershipGuard(P: IPCB_Primitive; Rec: String): String;
var Poly: IPCB_Polygon; Reg: IPCB_Region; Kind, Name: String;
begin
    Result := '';
    if P = nil then Exit;
    Kind := GetFieldFromPipeString(Rec, 0);
    Name := GetFieldFromPipeString(Rec, 2);
    if PourObjectName(P) <> Name then Result := 'case-insensitive name collision; exact spelling is required'
    else if P.Component <> nil then Result := 'component-owned pour object cannot be adopted'
    else if P.InPolygon then Result := 'polygon-owned child cannot be adopted'
    else if P.IsKeepout then Result := 'keepout cannot be adopted as polygon or cutout'
    else if P.Layer <> PrimitiveLayer(GetFieldFromPipeString(Rec, 4)) then Result := 'existing named object belongs to another layer'
    else if Kind = 'POLYGON' then begin
        if P.ObjectId <> ePolyObject then Result := 'polygon name collides with another object kind'
        else begin
            Poly := P;
            if Poly.PolygonType <> eSignalLayerPolygon then Result := 'only signal-layer polygons are supported'
            else if Poly.Net = nil then Result := 'existing polygon has no net'
            else if Poly.Net.Name <> GetFieldFromPipeString(Rec, 3) then Result := 'existing polygon belongs to another net';
        end;
    end
    else if Kind = 'CUTOUT' then begin
        if P.ObjectId <> eRegionObject then Result := 'cutout name collides with another object kind'
        else begin
            Reg := P;
            if Reg.Kind <> eRegionKind_Cutout then Result := 'region is not a polygon-only cutout'
            else if Reg.Net <> nil then Result := 'polygon cutout must not have a net'
            else if Reg.HoleCount <> 0 then Result := 'compound cutout regions are unsupported';
        end;
    end else Result := 'unsupported pour object kind';
end;

function PourSegmentCount(P: IPCB_Primitive): Integer;
var Poly: IPCB_Polygon; Reg: IPCB_Region;
begin
    if P.ObjectId = ePolyObject then begin Poly := P; Result := Poly.PointCount; end
    else begin Reg := P; Result := Reg.ShapeSegmentCount; end;
end;

function PourSegmentAt(P: IPCB_Primitive; Index: Integer): TPolySegment;
var Poly: IPCB_Polygon; Reg: IPCB_Region;
begin
    if P.ObjectId = ePolyObject then begin Poly := P; Result := Poly.Segments[Index]; end
    else begin Reg := P; Result := Reg.ShapeSegments[Index]; end;
end;

function PourShapeGuard(P: IPCB_Primitive): String;
var i, N: Integer; Seg: TPolySegment;
begin
    Result := '';
    if P = nil then Exit;
    N := PourSegmentCount(P);
    if (N < 3) or (N > 4096) then begin Result := 'existing contour vertex count is unsupported'; Exit; end;
    for i := 0 to N - 1 do begin
        Seg := PourSegmentAt(P, i);
        if Seg.Kind <> ePolySegmentLine then begin Result := 'arc contours are unsupported'; Exit; end;
    end;
end;

function PourOptionsKey(P: IPCB_Primitive): String;
var Poly: IPCB_Polygon;
begin
    Result := IntToStr(P.Layer) + '|' + JSONBool(not P.Moveable);
    if P.ObjectId = ePolyObject then begin
        Poly := P;
        Result := Result + '|' + IntToStr(Poly.PolygonType) + '|' + IntToStr(Poly.PolyHatchStyle) +
            '|' + IntToStr(Poly.PourOver) + '|' + JSONBool(Poly.RemoveDead) +
            '|' + JSONBool(Poly.RemoveIslandsByArea) + '|' + JSONBool(Poly.RemoveNarrowNecks) +
            '|' + IntToStr(Poly.NeckWidthThreshold) + '|' + IntToStr(Poly.ArcApproximation);
    end;
end;

function PourGeometryKey(P: IPCB_Primitive): String;
var i: Integer; Seg: TPolySegment;
begin
    Result := PourOptionsKey(P);
    for i := 0 to PourSegmentCount(P) - 1 do begin
        Seg := PourSegmentAt(P, i);
        Result := Result + '|' + IntToStr(Seg.Kind) + ',' + IntToStr(Seg.vx) + ',' + IntToStr(Seg.vy);
    end;
end;

function PourRequestKey(Board: IPCB_Board; Rec: String): String;
var i, N: Integer;
begin
    Result := IntToStr(PrimitiveLayer(GetFieldFromPipeString(Rec, 4))) + '|' +
        JSONBool(GetFieldFromPipeString(Rec, 5) = '1');
    if GetFieldFromPipeString(Rec, 0) = 'POLYGON' then
        Result := Result + '|' + IntToStr(eSignalLayerPolygon) + '|' + IntToStr(ePolySolid) +
            '|' + IntToStr(ePolygonPourOver_SameNet) + '|' + JSONBool(GetFieldFromPipeString(Rec, 6) = '1') +
            '|false|true|' + IntToStr(PrimitiveCoord(Rec, 7)) + '|' + IntToStr(PrimitiveCoord(Rec, 8));
    N := StrToInt(GetFieldFromPipeString(Rec, 10));
    for i := 0 to N - 1 do
        Result := Result + '|' + IntToStr(ePolySegmentLine) + ',' +
            IntToStr(Board.XOrigin + PrimitiveCoord(Rec, 11 + i * 2)) + ',' +
            IntToStr(Board.YOrigin + PrimitiveCoord(Rec, 12 + i * 2));
end;

function PourCopperRegionCount(Poly: IPCB_Polygon): Integer;
var Iter: IPCB_GroupIterator; Reg: IPCB_Region;
begin
    Result := 0;
    Iter := Poly.GroupIterator_Create;
    try
        Iter.AddFilter_ObjectSet(MkSet(eRegionObject));
        Reg := Iter.FirstPCBObject;
        while Reg <> nil do begin
            if (Reg.Kind = eRegionKind_Copper) and (Reg.Area > 0) then Result := Result + 1;
            Reg := Iter.NextPCBObject;
        end;
    finally Poly.GroupIterator_Destroy(Iter); end;
end;

function PourObjectJSON(Board: IPCB_Board; P: IPCB_Primitive): String;
var Poly: IPCB_Polygon; Reg: IPCB_Region; Verts: TStringList; i: Integer; Kind, NetName, Extra: String;
begin
    Verts := TStringList.Create;
    try
        Kind := 'cutout'; NetName := ''; Extra := '';
        if P.ObjectId = ePolyObject then begin
            Kind := 'polygon'; Poly := P;
            if Poly.Net <> nil then NetName := Poly.Net.Name;
            Extra := ', "solid": ' + JSONBool(Poly.PolyHatchStyle = ePolySolid) +
                ', "remove_dead": ' + JSONBool(Poly.RemoveDead) +
                ', "remove_islands_by_area": ' + JSONBool(Poly.RemoveIslandsByArea) +
                ', "remove_narrow_necks": ' + JSONBool(Poly.RemoveNarrowNecks) +
                ', "neck_width_mm": ' + PcbDocNum('0.########', CoordToMMs(Poly.NeckWidthThreshold)) +
                ', "arc_tolerance_mm": ' + PcbDocNum('0.########', CoordToMMs(Poly.ArcApproximation)) +
                ', "generated_copper_regions": ' + IntToStr(PourCopperRegionCount(Poly));
        end;
        for i := 0 to PourSegmentCount(P) - 1 do Verts.Add(OutlineSegmentJSON(Board, PourSegmentAt(P, i)));
        Result := '{"kind": ' + JSONStr(Kind) + ', "name": ' + JSONStr(PourObjectName(P)) +
            ', "net": ' + JSONStr(NetName) + ', "layer": ' + JSONStr(Layer2String(P.Layer)) +
            ', "locked": ' + JSONBool(not P.Moveable) + ', "is_keepout": ' + JSONBool(P.IsKeepout) +
            ', "native_geometry_key": ' + JSONStr(PourGeometryKey(P)) + Extra + ', ' + BuildJSONArray(Verts, 'vertices_mm') + '}';
    finally Verts.Free; end;
end;

function PourInventoryJSON(Board: IPCB_Board): String;
var Iter: IPCB_BoardIterator; P: IPCB_Primitive; Reg: IPCB_Region; Items: TStringList; Include: Boolean;
begin
    Items := TStringList.Create;
    Iter := Board.BoardIterator_Create;
    try
        Iter.AddFilter_ObjectSet(MkSet(ePolyObject, eRegionObject));
        Iter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        P := Iter.FirstPCBObject;
        while P <> nil do begin
            Include := P.ObjectId = ePolyObject;
            if P.ObjectId = eRegionObject then begin Reg := P; Include := Reg.Kind = eRegionKind_Cutout; end;
            if Include then Items.Add(PourObjectJSON(Board, P));
            P := Iter.NextPCBObject;
        end;
        Result := BuildJSONArray(Items, 'inventory');
    finally Board.BoardIterator_Destroy(Iter); Items.Free; end;
end;

function PourBatchHasPolygon(Spec: TStringList; Name: String): Boolean;
var i: Integer;
begin
    Result := False;
    for i := 1 to Spec.Count - 1 do
        if (GetFieldFromPipeString(Spec[i], 0) = 'POLYGON') and
           (GetFieldFromPipeString(Spec[i], 2) = Name) then Result := True;
end;

function PourAffectedGuard(Board: IPCB_Board; Spec: TStringList): String;
var Iter: IPCB_BoardIterator; Poly: IPCB_Polygon; i: Integer; Affected: Boolean;
begin
    Result := '';
    Iter := Board.BoardIterator_Create;
    try
        Iter.AddFilter_ObjectSet(MkSet(ePolyObject));
        Iter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        Poly := Iter.FirstPCBObject;
        while Poly <> nil do begin
            Affected := False;
            for i := 1 to Spec.Count - 1 do
                if (PrimitiveLayer(GetFieldFromPipeString(Spec[i], 4)) = Poly.Layer) and
                   (GetFieldFromPipeString(Spec[i], 1) <> 'repour') then Affected := True;
            if Affected and not PourBatchHasPolygon(Spec, Poly.Name) then begin
                Result := 'include every existing polygon on changed layers explicitly, at least as repour: ' + Poly.Name;
                Break;
            end;
            Poly := Iter.NextPCBObject;
        end;
    finally Board.BoardIterator_Destroy(Iter); end;
end;

procedure ApplyPourGeometry(Board: IPCB_Board; P: IPCB_Primitive; Rec: String);
var Poly: IPCB_Polygon; Reg: IPCB_Region; Seg: TPolySegment; i, N, Count: Integer;
begin
    N := StrToInt(GetFieldFromPipeString(Rec, 10));
    P.Layer := PrimitiveLayer(GetFieldFromPipeString(Rec, 4));
    P.IsKeepout := False;
    P.Moveable := GetFieldFromPipeString(Rec, 5) = '0';
    if P.ObjectId = ePolyObject then begin
        Poly := P;
        Poly.Name := GetFieldFromPipeString(Rec, 2);
        Poly.Net := RoutingNet(Board, GetFieldFromPipeString(Rec, 3), Count);
        Poly.PolygonType := eSignalLayerPolygon;
        Poly.PolyHatchStyle := ePolySolid;
        Poly.PourOver := ePolygonPourOver_SameNet;
        Poly.RemoveDead := GetFieldFromPipeString(Rec, 6) = '1';
        Poly.RemoveIslandsByArea := False;
        Poly.RemoveNarrowNecks := True;
        Poly.NeckWidthThreshold := PrimitiveCoord(Rec, 7);
        Poly.ArcApproximation := PrimitiveCoord(Rec, 8);
        Poly.PointCount := N;
    end else begin
        Reg := P;
        Reg.Name := GetFieldFromPipeString(Rec, 2);
        Reg.Net := nil;
        Reg.Kind := eRegionKind_Cutout;
        Reg.ShapeSegmentCount := N;
    end;
    for i := 0 to N - 1 do begin
        Seg := TPolySegment;
        Seg.Kind := ePolySegmentLine;
        Seg.vx := Board.XOrigin + PrimitiveCoord(Rec, 11 + i * 2);
        Seg.vy := Board.YOrigin + PrimitiveCoord(Rec, 12 + i * 2);
        if P.ObjectId = ePolyObject then Poly.Segments[i] := Seg else Reg.ShapeSegments[i] := Seg;
    end;
    if P.ObjectId = eRegionObject then Reg.UpdateContourFromShape(True);
end;

function SetPcbPolygonsReport(SpecPath: String): String;
var
    Spec, Names, Keys, Actions, Items, Trace: TStringList;
    Board: IPCB_Board;
    Net: IPCB_Net;
    P: IPCB_Primitive;
    Poly: IPCB_Polygon;
    PcbPath, Rec, Name, Kind, Action, Error, Key, TracePath: String;
    i, j, Count, NetCount, N, Added, Updated, Removed, Repoured, Unchanged: Integer;
    OldRepourMode: TPolygonRepourMode;
    DryRun, Saved, Verified, IsNew: Boolean;
begin
    if not FileExists(SpecPath) then begin Result := PcbDocError('polygon spec does not exist'); Exit; end;
    Spec := TStringList.Create; Names := TStringList.Create; Keys := TStringList.Create;
    Actions := TStringList.Create; Items := TStringList.Create; Trace := TStringList.Create;
    TracePath := ChangeFileExt(SpecPath, '.trace.txt');
    try
        KeepoutTrace(Trace, TracePath, 'load_spec', 0, '');
        Spec.LoadFromFile(SpecPath);
        if Spec.Count = 0 then begin Result := PcbDocError('empty polygon spec'); Exit; end;
        PcbPath := GetFieldFromPipeString(Spec[0], 1);
        DryRun := GetFieldFromPipeString(Spec[0], 2) = '1';
        if (Spec.Count = 1) and not DryRun then begin Result := PcbDocError('empty mutation batch'); Exit; end;
        KeepoutTrace(Trace, TracePath, 'open_board', 0, '');
        Board := OpenBoardAtPath(PcbPath);
        if Board = nil then begin Result := PcbDocError('cannot open requested PCB'); Exit; end;
        if not SameText(Board.FileName, PcbPath) then begin Result := PcbDocError('opened PCB path differs'); Exit; end;
        Error := PcbPrimitiveProcessGuard(0);
        if Error <> '' then begin Result := PcbDocError(Error); Exit; end;
        for i := 1 to Spec.Count - 1 do begin
            Rec := Spec[i]; Kind := GetFieldFromPipeString(Rec, 0);
            Action := GetFieldFromPipeString(Rec, 1); Name := GetFieldFromPipeString(Rec, 2);
            KeepoutTrace(Trace, TracePath, 'preflight', i, Name);
            if (Kind <> 'POLYGON') and (Kind <> 'CUTOUT') then Error := 'invalid pour record kind';
            if (Action <> 'create') and (Action <> 'update') and (Action <> 'upsert') and
               (Action <> 'delete') and (Action <> 'repour') then Error := 'invalid pour operation';
            if (Kind = 'CUTOUT') and (Action = 'repour') then Error := 'cutouts cannot be repoured';
            if Trim(Name) = '' then Error := 'empty pour identity';
            if Names.IndexOf(Name) >= 0 then Error := 'duplicate pour identity';
            Names.Add(Name);
            if (GetFieldFromPipeString(Rec, 4) <> 'top') and (GetFieldFromPipeString(Rec, 4) <> 'bottom') then Error := 'outer copper layers only';
            if not MidLayerInStack(Board.LayerStack_V7, PrimitiveLayer(GetFieldFromPipeString(Rec, 4))) then Error := 'requested layer absent';
            if Kind = 'POLYGON' then begin
                Net := RoutingNet(Board, GetFieldFromPipeString(Rec, 3), NetCount);
                if NetCount <> 1 then Error := 'polygon net must resolve exactly once';
            end;
            if (Action <> 'delete') and (Action <> 'repour') then begin
                N := StrToInt(GetFieldFromPipeString(Rec, 10));
                if (N < 3) or (N > 4096) then Error := 'unsupported contour count';
                if (PrimitiveCoord(Rec, 7) <= 0) or (PrimitiveCoord(Rec, 8) <= 0) then Error := 'neck/arc dimension rounds to zero';
                for j := 0 to N - 1 do
                    if (PrimitiveCoord(Rec, 11+j*2) = PrimitiveCoord(Rec, 11+((j+1) mod N)*2)) and
                       (PrimitiveCoord(Rec, 12+j*2) = PrimitiveCoord(Rec, 12+((j+1) mod N)*2)) then Error := 'edge collapses in native coordinates';
            end;
            if Error <> '' then Break;
            P := FindPourObject(Board, Name, Count);
            if Count > 1 then Error := 'multiple objects have the requested name'
            else if (Action = 'create') and (Count <> 0) then Error := 'create requires an unused name'
            else if ((Action = 'update') or (Action = 'delete') or (Action = 'repour')) and (Count <> 1) then Error := 'operation requires exactly one existing object'
            else Error := PourOwnershipGuard(P, Rec);
            if Error = '' then Error := PourShapeGuard(P);
            if Error <> '' then Break;
            if (Action = 'delete') or (Action = 'repour') then Key := PourGeometryKey(P)
            else Key := PourRequestKey(Board, Rec);
            Keys.Add(Key);
        end;
        if Error = '' then Error := PourAffectedGuard(Board, Spec);
        if Error <> '' then begin Result := PcbDocError(Error); Exit; end;
        if DryRun then begin
            KeepoutTrace(Trace, TracePath, 'dry_run_completed', 0, '');
            Result := '{"success": true, "dry_run": true, "saved": false, "preflight_pass": true, "path": ' +
                JSONStr(PcbPath) + ', ' + PourInventoryJSON(Board) + '}';
            Exit;
        end;
        Added := 0; Updated := 0; Removed := 0; Repoured := 0; Unchanged := 0;
        Saved := False; Verified := False;
        KeepoutTrace(Trace, TracePath, 'preprocess', 0, '');
        OldRepourMode := PCBServer.SystemOptions.PolygonRepour;
        try
            // Avoid user-configurable confirmation prompts; restore in every normal exit.
            PCBServer.SystemOptions.PolygonRepour := eAlwaysRepour;
            PCBServer.PreProcess;
            try
                for i := 1 to Spec.Count - 1 do begin
                    Rec := Spec[i]; Kind := GetFieldFromPipeString(Rec, 0);
                    Action := GetFieldFromPipeString(Rec, 1); Name := GetFieldFromPipeString(Rec, 2);
                    KeepoutTrace(Trace, TracePath, 'mutation_lookup', i, Name);
                    P := FindPourObject(Board, Name, Count);
                    Error := PourOwnershipGuard(P, Rec);
                    if Count > 1 then Error := 'pour target became ambiguous';
                    if Error <> '' then Break;
                    if Action = 'delete' then begin
                        Board.RemovePCBObject(P); Removed := Removed + 1; Actions.Add('deleted');
                    end else if Action = 'repour' then Actions.Add('repoured')
                    else if (P <> nil) and (PourGeometryKey(P) = Keys[i-1]) then begin
                        Unchanged := Unchanged + 1; Actions.Add('unchanged');
                    end else begin
                        IsNew := P = nil;
                        if IsNew then begin
                            if Kind = 'POLYGON' then P := PCBServer.PCBObjectFactory(ePolyObject, eNoDimension, eCreate_Default)
                            else P := PCBServer.PCBObjectFactory(eRegionObject, eNoDimension, eCreate_Default);
                        end else PCBServer.SendMessageToRobots(P.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
                        try
                            KeepoutTrace(Trace, TracePath, 'apply_geometry', i, Name);
                            ApplyPourGeometry(Board, P, Rec);
                        finally
                            if not IsNew then PCBServer.SendMessageToRobots(P.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);
                        end;
                        if IsNew then begin
                            Board.AddPCBObject(P);
                            PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, P.I_ObjectAddress);
                            Added := Added + 1; Actions.Add('added');
                        end else begin Updated := Updated + 1; Actions.Add('updated'); end;
                    end;
                end;
            finally
                KeepoutTrace(Trace, TracePath, 'postprocess', 0, '');
                PCBServer.PostProcess;
                KeepoutTrace(Trace, TracePath, 'postprocess_completed', 0, '');
            end;
            if Error = '' then
                for i := 1 to Spec.Count - 1 do begin
                    Rec := Spec[i]; Name := GetFieldFromPipeString(Rec, 2);
                    if (GetFieldFromPipeString(Rec, 0) = 'POLYGON') and (GetFieldFromPipeString(Rec, 1) <> 'delete') then begin
                        P := FindPourObject(Board, Name, Count);
                        if Count <> 1 then begin Error := 'polygon disappeared before repour'; Break; end;
                        Poly := P;
                        KeepoutTrace(Trace, TracePath, 'repour', i, Name);
                        Poly.SetState_CopperPourInvalid;
                        Poly.Rebuild;
                        Repoured := Repoured + 1;
                        KeepoutTrace(Trace, TracePath, 'repour_completed', i, Name);
                    end;
                end;
        finally
            PCBServer.SystemOptions.PolygonRepour := OldRepourMode;
            KeepoutTrace(Trace, TracePath, 'preferences_restored', 0, '');
        end;
        if Error = '' then begin
            ResetParameters; AddStringParameter('Action', 'RebuildConnectivity'); RunProcess('PCB:UpdateConnectivity');
            Board.ConnectivelyValidateNets;
            for i := 1 to Spec.Count - 1 do begin
                Rec := Spec[i]; Name := GetFieldFromPipeString(Rec, 2);
                Action := GetFieldFromPipeString(Rec, 1);
                KeepoutTrace(Trace, TracePath, 'final_readback', i, Name);
                P := FindPourObject(Board, Name, Count);
                if Action = 'delete' then begin
                    if Count <> 0 then Error := 'deleted object is still present'
                    else Items.Add('{"name": ' + JSONStr(Name) + ', "action": "deleted", "verified": true}');
                end else begin
                    if Count <> 1 then Error := 'saved candidate missing or ambiguous'
                    else begin
                        Error := PourOwnershipGuard(P, Rec);
                        if (Error = '') and (PourGeometryKey(P) <> Keys[i-1]) then Error := 'native contour/options readback mismatch';
                        if (Error = '') and (P.ObjectId = ePolyObject) then begin
                            Poly := P;
                            if (GetFieldFromPipeString(Rec, 9) <> '1') and (PourCopperRegionCount(Poly) = 0) then
                                Error := 'polygon has no generated copper; inspect before retry or explicitly allow_empty';
                        end;
                        if Error = '' then Items.Add('{"action": ' + JSONStr(Actions[i-1]) + ', "verified": true, "object": ' + PourObjectJSON(Board, P) + '}');
                    end;
                end;
                if Error <> '' then Break;
            end;
            Verified := Error = '';
        end;
        Board.GraphicallyInvalidate; Board.ViewManager_FullUpdate;
        KeepoutTrace(Trace, TracePath, 'save_if_verified', 0, '');
        if Verified and (Error = '') then Saved := SaveBoardDocument(PcbPath);
        KeepoutTrace(Trace, TracePath, 'completed', 0, '');
        Result := '{"success": ' + JSONBool(Saved and Verified) + ', "saved": ' + JSONBool(Saved) +
            ', "verified": ' + JSONBool(Verified) + ', "dry_run": false, "drc_run": false, "error": ' + JSONStr(Error) +
            ', "path": ' + JSONStr(PcbPath) + ', "added": ' + IntToStr(Added) + ', "updated": ' + IntToStr(Updated) +
            ', "deleted": ' + IntToStr(Removed) + ', "unchanged": ' + IntToStr(Unchanged) +
            ', "repoured": ' + IntToStr(Repoured) + ', ' + BuildJSONArray(Items, 'objects') + '}';
    finally Spec.Free; Names.Free; Keys.Free; Actions.Free; Items.Free; Trace.Free; end;
end;
