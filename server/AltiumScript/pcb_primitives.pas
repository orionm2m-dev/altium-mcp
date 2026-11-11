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

// Read-only reconciliation of an interrupted exact routing request.
// Does not focus/open a document, repair a transaction, rebuild, or save.
function InspectPcbRoutingReport(SpecPath: String): String;
var
    Spec, Keys, Items : TStringList;
    Board : IPCB_Board;
    Doc : IServerDocument;
    P : IPCB_Primitive;
    Net : IPCB_Net;
    PcbPath, Error, Rec, Key, NetName, Action, Observed : String;
    i, Count, NetCount : Integer;
    Matches, AllMatches : Boolean;
begin
    if not FileExists(SpecPath) then
    begin Result := PcbDocError('routing inspection spec does not exist'); Exit; end;
    Spec := TStringList.Create; Keys := TStringList.Create; Items := TStringList.Create;
    try
        Spec.LoadFromFile(SpecPath);
        if Spec.Count < 2 then
        begin Result := PcbDocError('routing inspection must not be empty'); Exit; end;
        PcbPath := GetFieldFromPipeString(Spec[0], 1);
        Error := PcbPrimitiveProcessGuard(0);
        if (Error = '') and (not FileExists(PcbPath)) then Error := 'requested PCB file does not exist';
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
        if Error <> '' then begin Result := PcbDocError(Error); Exit; end;
        // Validate all inputs before collecting any observations.
        for i := 1 to Spec.Count - 1 do
        begin
            Rec := Spec[i]; Error := RoutingRecordGuard(Board, Rec);
            if Error <> '' then Break;
            NetName := GetFieldFromPipeString(Rec, 2);
            Net := RoutingNet(Board, NetName, NetCount);
            if NetCount <> 1 then begin Error := 'net must resolve exactly once: ' + NetName; Break; end;
            Key := RoutingRequestKey(Board, Rec);
            if Keys.IndexOf(Key) >= 0 then begin Error := 'duplicate native routing geometry in inspection'; Break; end;
            Keys.Add(Key);
        end;
        if Error <> '' then begin Result := PcbDocError(Error); Exit; end;
        AllMatches := True;
        for i := 1 to Spec.Count - 1 do
        begin
            Rec := Spec[i]; Key := Keys[i - 1];
            Action := GetFieldFromPipeString(Rec, 1); NetName := GetFieldFromPipeString(Rec, 2);
            P := FindRoutingPrimitive(Board, Key, Count);
            Matches := False; Observed := 'null';
            if Count > 1 then Error := 'multiple routing objects match exact geometry'
            else if Count = 1 then
            begin
                Error := RoutingOwnershipGuard(P, NetName);
                if Error = '' then
                begin
                    Observed := RoutingReadbackJSON(Board, P, 'observed', i);
                    Matches := (Action = 'add') and (P.Moveable = (GetFieldFromPipeString(Rec, 9) = '0'));
                end;
            end
            else Matches := Action = 'delete';
            if Error <> '' then Matches := False;
            AllMatches := AllMatches and Matches;
            Items.Add('{"index": ' + IntToStr(i) + ', "requested_operation": ' + JSONStr(Action) +
                ', "requested_net": ' + JSONStr(NetName) + ', "native_geometry_key": ' + JSONStr(Key) +
                ', "present_count": ' + IntToStr(Count) + ', "matches_requested_final_state": ' + JSONBool(Matches) +
                ', "error": ' + JSONStr(Error) + ', "observed": ' + Observed + '}');
            if Error <> '' then Break;
        end;
        Result := '{"success": ' + JSONBool(Error = '') + ', "error": ' + JSONStr(Error) +
            ', "path": ' + JSONStr(PcbPath) + ', "read_only": true, "saved": false, "mutation_attempted": false' +
            ', "all_match_requested_final_state": ' + JSONBool(AllMatches and (Error = '')) +
            ', ' + BuildJSONArray(Items, 'observations') + '}';
    finally Spec.Free; Keys.Free; Items.Free; end;
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
                    KeepoutTrace(Trace, TracePath, 'add_object_completed', i, NetName);
                    PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, P.I_ObjectAddress);
                    KeepoutTrace(Trace, TracePath, 'registration_message_completed', i, NetName);
                    Added := Added + 1;
                    Actions.Add('added');
                    KeepoutTrace(Trace, TracePath, 'local_key_readback', i, NetName);
                    if RoutingObjectKey(P) <> Key then Error := 'new routing geometry readback mismatch';
                    KeepoutTrace(Trace, TracePath, 'local_key_readback_completed', i, NetName);
                    if Error = '' then Error := RoutingOwnershipGuard(P, NetName);
                    KeepoutTrace(Trace, TracePath, 'ownership_readback_completed', i, NetName);
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

function PourClosureGuard(P: IPCB_Primitive): String;
var Poly: IPCB_Polygon; FirstSeg, ClosingSeg: TPolySegment; N: Integer;
begin
    Result := '';
    if P = nil then Exit;
    if P.ObjectId <> ePolyObject then Exit;
    Poly := P; N := Poly.PointCount;
    if (N < 3) or (N > 4096) then begin Result := 'polygon point count is unsupported'; Exit; end;
    FirstSeg := Poly.Segments[0];
    ClosingSeg := Poly.Segments[N];
    if (ClosingSeg.Kind <> FirstSeg.Kind) or (ClosingSeg.vx <> FirstSeg.vx) or
       (ClosingSeg.vy <> FirstSeg.vy) then Result := 'polygon closing segment differs from first vertex';
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

// One linear split per contour operation. Repeated GetFieldFromPipeString
// inside vertex loops rescanned long records quadratically in DelphiScript.
// Preserve empty/trailing fields and exact strings; conversion stays unchanged.
procedure SplitPourFields(Rec: String; Fields: TStringList);
var i, Start: Integer;
begin
    Fields.Clear;
    Start := 1;
    for i := 1 to Length(Rec) do
        if Rec[i] = '|' then
        begin
            Fields.Add(Copy(Rec, Start, i - Start));
            Start := i + 1;
        end;
    Fields.Add(Copy(Rec, Start, Length(Rec) - Start + 1));
end;

function PourFieldCoord(Fields: TStringList; Index: Integer): TCoord;
var Field: String;
begin
    Field := '';
    if (Index >= 0) and (Index < Fields.Count) then Field := Fields[Index];
    Result := MMsToCoord(SafeStrToFloat(Field));
end;

function PourRequestKey(Board: IPCB_Board; Rec: String): String;
var i, N: Integer; Fields: TStringList;
begin
    Fields := TStringList.Create;
    try
    SplitPourFields(Rec, Fields);
    Result := IntToStr(PrimitiveLayer(GetFieldFromPipeString(Rec, 4))) + '|' +
        JSONBool(GetFieldFromPipeString(Rec, 5) = '1');
    if GetFieldFromPipeString(Rec, 0) = 'POLYGON' then
        Result := Result + '|' + IntToStr(eSignalLayerPolygon) + '|' + IntToStr(ePolySolid) +
            '|' + IntToStr(ePolygonPourOver_SameNet) + '|' + JSONBool(GetFieldFromPipeString(Rec, 6) = '1') +
            '|false|true|' + IntToStr(PourFieldCoord(Fields, 7)) + '|' + IntToStr(PourFieldCoord(Fields, 8));
    N := StrToInt(GetFieldFromPipeString(Rec, 10));
    for i := 0 to N - 1 do
        Result := Result + '|' + IntToStr(ePolySegmentLine) + ',' +
            IntToStr(Board.XOrigin + PourFieldCoord(Fields, 11 + i * 2)) + ',' +
            IntToStr(Board.YOrigin + PourFieldCoord(Fields, 12 + i * 2));
    finally Fields.Free; end;
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
var Poly: IPCB_Polygon; Reg: IPCB_Region; Seg: TPolySegment; i, N, Count: Integer; Fields: TStringList;
begin
    Fields := TStringList.Create;
    try
    SplitPourFields(Rec, Fields);
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
        Poly.NeckWidthThreshold := PourFieldCoord(Fields, 7);
        Poly.ArcApproximation := PourFieldCoord(Fields, 8);
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
        Seg.vx := Board.XOrigin + PourFieldCoord(Fields, 11 + i * 2);
        Seg.vy := Board.YOrigin + PourFieldCoord(Fields, 12 + i * 2);
        if P.ObjectId = ePolyObject then Poly.Segments[i] := Seg else Reg.ShapeSegments[i] := Seg;
    end;
    if P.ObjectId = ePolyObject then begin
        // Altium MillExporter MakeNewPolygon copies indices 0..PointCount inclusive.
        // PointCount is the edge count; its extra slot must repeat the first vertex.
        // Keep the public contour at N unique vertices, never an uninitialized (0,0).
        Seg := Poly.Segments[0];
        Poly.Segments[N] := Seg;
    end else Reg.UpdateContourFromShape(True);
    finally Fields.Free; end;
end;

function SetPcbPolygonsReport(SpecPath: String): String;
var
    Spec, Names, Keys, Actions, Items, Trace, Fields: TStringList;
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
    Fields := TStringList.Create;
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
            SplitPourFields(Rec, Fields);
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
                if (PourFieldCoord(Fields, 7) <= 0) or (PourFieldCoord(Fields, 8) <= 0) then Error := 'neck/arc dimension rounds to zero';
                for j := 0 to N - 1 do
                    if (PourFieldCoord(Fields, 11+j*2) = PourFieldCoord(Fields, 11+((j+1) mod N)*2)) and
                       (PourFieldCoord(Fields, 12+j*2) = PourFieldCoord(Fields, 12+((j+1) mod N)*2)) then Error := 'edge collapses in native coordinates';
            end;
            if Error <> '' then Break;
            P := FindPourObject(Board, Name, Count);
            if Count > 1 then Error := 'multiple objects have the requested name'
            else if (Action = 'create') and (Count <> 0) then Error := 'create requires an unused name'
            else if ((Action = 'update') or (Action = 'delete') or (Action = 'repour')) and (Count <> 1) then Error := 'operation requires exactly one existing object'
            else Error := PourOwnershipGuard(P, Rec);
            if Error = '' then Error := PourShapeGuard(P);
            if (Error = '') and (Action = 'repour') then Error := PourClosureGuard(P);
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
                    else if (P <> nil) and (PourGeometryKey(P) = Keys[i-1]) and (PourClosureGuard(P) = '') then begin
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
                        if Error = '' then Error := PourClosureGuard(P);
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
    finally Spec.Free; Names.Free; Keys.Free; Actions.Free; Items.Free; Trace.Free; Fields.Free; end;
end;

// Narrow mechanical inventory and deletion. Never opens/focuses a document.
// A delete requires the complete key returned by a prior native inspection.
function MechanicalObjectKind(P: IPCB_Primitive): String;
var Pad: IPCB_Pad; Reg: IPCB_Region;
begin
    Result := '';
    if P = nil then Exit;
    if P.Component <> nil then Exit;
    if P.InPolygon then Exit;
    if P.Net <> nil then Exit;
    if P.ObjectId = ePadObject then begin
        Pad := P;
        if Pad.IsKeepout or Pad.Plated or (Pad.Layer <> eMultiLayer) then Exit;
        if (Pad.Mode <> ePadMode_Simple) or (Pad.HoleSize <= 0) then Exit;
        if (Pad.HoleType <> 0) or (Pad.TopShape <> eRounded) then Exit;
        if (Pad.TopXSize > Pad.HoleSize) or (Pad.TopYSize > Pad.HoleSize) then Exit;
        if Pad.Name = '' then Exit;
        Result := 'free_npth';
    end else if P.ObjectId = eRegionObject then begin
        Reg := P;
        if not Reg.IsKeepout then Exit;
        if (Reg.Kind <> eRegionKind_Copper) or (Reg.Name = '') or (Reg.HoleCount <> 0) then Exit;
        if PourShapeGuard(P) <> '' then Exit;
        Result := 'keepout_region';
    end;
end;

function MechanicalObjectName(P: IPCB_Primitive): String;
var Pad: IPCB_Pad; Reg: IPCB_Region;
begin
    Result := '';
    if P.ObjectId = ePadObject then begin Pad := P; Result := Pad.Name; end
    else if P.ObjectId = eRegionObject then begin Reg := P; Result := Reg.Name; end;
end;

function MechanicalObjectKey(P: IPCB_Primitive): String;
var Pad: IPCB_Pad;
begin
    Result := MechanicalObjectKind(P);
    if Result = '' then Exit;
    Result := Result + ':' + IntToStr(P.Layer) + ':' + JSONBool(not P.Moveable);
    if P.ObjectId = ePadObject then begin
        Pad := P;
        Result := Result + ':' + IntToStr(Pad.X) + ':' + IntToStr(Pad.Y) +
            ':' + IntToStr(Pad.TopXSize) + ':' + IntToStr(Pad.TopYSize) + ':' + IntToStr(Pad.HoleSize) +
            ':' + PcbDocNum('0.########', Pad.Rotation);
    end else Result := Result + ':' + StringReplace(PourGeometryKey(P), '|', ';', 1);
end;

function MechanicalFind(Board: IPCB_Board; Kind, Name: String; var Count: Integer): IPCB_Primitive;
begin
    Result := nil; Count := 0;
    if Kind = 'free_npth' then Result := FindPrimitivePad(Board, '', Name, Count)
    else if Kind = 'keepout_region' then Result := FindKeepoutPrimitive(Board, Name, Count);
end;

function MechanicalInventory(Board: IPCB_Board): String;
var Iter: IPCB_BoardIterator; P: IPCB_Primitive; Items: TStringList; Kind: String;
begin
    Items := TStringList.Create;
    Iter := Board.BoardIterator_Create;
    try
        Iter.AddFilter_ObjectSet(MkSet(ePadObject, eRegionObject));
        Iter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        P := Iter.FirstPCBObject;
        while P <> nil do begin
            Kind := MechanicalObjectKind(P);
            if Kind <> '' then Items.Add('{"kind":' + JSONStr(Kind) + ',"name":' +
                JSONStr(MechanicalObjectName(P)) + ',"expected_key":' + JSONStr(MechanicalObjectKey(P)) + '}');
            P := Iter.NextPCBObject;
        end;
        Result := BuildJSONArray(Items, 'inventory');
    finally Board.BoardIterator_Destroy(Iter); Items.Free; end;
end;

function MechanicalTargetGuard(Board: IPCB_Board; Rec: String; var P: IPCB_Primitive): String;
var Kind, Name: String; Count: Integer;
begin
    Kind := GetFieldFromPipeString(Rec, 0); Name := GetFieldFromPipeString(Rec, 1);
    P := MechanicalFind(Board, Kind, Name, Count);
    Result := '';
    if Count <> 1 then Result := 'target must resolve exactly once: ' + Name
    else if MechanicalObjectKind(P) <> Kind then Result := 'target ownership/type is unsupported: ' + Name
    else if MechanicalObjectName(P) <> Name then Result := 'exact target name is required: ' + Name
    else if MechanicalObjectKey(P) <> GetFieldFromPipeString(Rec, 2) then Result := 'target changed since inspect: ' + Name;
end;

function MechanicalControlReport(SpecPath: String): String;
var Spec, Deleted: TStringList; Board: IPCB_Board; Doc: IServerDocument; P: IPCB_Primitive;
    Path, Action, Error, Before, After, Rec, Kind, Name: String;
    i, j, Count, Removed: Integer; Saved, Verified: Boolean;
begin
    if not FileExists(SpecPath) then begin Result := PcbDocError('mechanical spec missing'); Exit; end;
    Spec := TStringList.Create; Deleted := TStringList.Create;
    try
        Spec.LoadFromFile(SpecPath);
        if Spec.Count < 2 then begin Result := PcbDocError('mechanical spec incomplete'); Exit; end;
        Path := GetFieldFromPipeString(Spec[0], 1); Action := GetFieldFromPipeString(Spec[1], 1);
        Error := PcbPrimitiveProcessGuard(0);
        if Error = '' then begin
            if Client.CurrentView = nil then Error := 'no current view'
            else begin
                Doc := Client.CurrentView.OwnerDocument;
                if Doc = nil then Error := 'no current document'
                else if not SameText(Doc.FileName, Path) or not SameText(Doc.Kind, 'PCB') then Error := 'exact PCB must already be active';
            end;
        end;
        if Error = '' then begin
            Board := PCBServer.GetCurrentPCBBoard;
            if Board = nil then Error := 'no current PCB'
            else if not SameText(Board.FileName, Path) then Error := 'current PCB differs';
        end;
        if (Action <> 'inspect') and (Action <> 'delete') then Error := 'unsupported action';
        if (Action = 'inspect') and (Spec.Count <> 2) then Error := 'inspect must not include targets';
        if (Action = 'delete') and ((Spec.Count < 3) or (Spec.Count > 66)) then Error := 'invalid deletion count';
        if Error <> '' then begin Result := PcbDocError(Error); Exit; end;
        Before := MechanicalInventory(Board);
        Removed := 0; Saved := False; Verified := True;
        if Action = 'delete' then begin
            for i := 2 to Spec.Count - 1 do begin
                Error := MechanicalTargetGuard(Board, Spec[i], P);
                for j := 2 to i - 1 do
                    if SameText(GetFieldFromPipeString(Spec[j],0), GetFieldFromPipeString(Spec[i],0)) and
                       SameText(GetFieldFromPipeString(Spec[j],1), GetFieldFromPipeString(Spec[i],1)) then Error := 'duplicate target';
                if Error <> '' then Break;
            end;
            if Error <> '' then begin Result := PcbDocError(Error); Exit; end;
            PCBServer.PreProcess;
            try
                for i := 2 to Spec.Count - 1 do begin
                    Rec := Spec[i]; Error := MechanicalTargetGuard(Board, Rec, P);
                    if Error <> '' then Break;
                    Board.RemovePCBObject(P); Removed := Removed + 1;
                    Deleted.Add(JSONStr(GetFieldFromPipeString(Rec, 1)));
                end;
            finally PCBServer.PostProcess; end;
            for i := 2 to Spec.Count - 1 do begin
                P := MechanicalFind(Board, GetFieldFromPipeString(Spec[i],0), GetFieldFromPipeString(Spec[i],1), Count);
                if Count <> 0 then Verified := False;
            end;
            if Removed <> Spec.Count - 2 then Verified := False;
            if PcbPrimitiveProcessGuard(0) <> '' then Verified := False;
            if not Verified and (Error = '') then Error := 'removal readback failed; board not saved';
            if Verified and (Error = '') then begin
                Board.GraphicallyInvalidate; Board.ViewManager_FullUpdate;
                Saved := SaveBoardDocument(Path);
                if not Saved then Error := 'save failed';
            end;
        end;
        After := MechanicalInventory(Board);
        Result := '{"success":' + JSONBool((Error = '') and Verified and ((Action = 'inspect') or Saved)) +
            ',"action":' + JSONStr(Action) + ',"path":' + JSONStr(Path) + ',"error":' + JSONStr(Error) +
            ',"removed":' + IntToStr(Removed) + ',"saved":' + JSONBool(Saved) + ',"verified":' + JSONBool(Verified) +
            ',"before":{' + Before + '},"after":{' + After + '},' + BuildJSONArray(Deleted, 'deleted_names') + '}';
    finally Spec.Free; Deleted.Free; end;
end;

// Add-only, hash-pinned STEP attachment. Geometry is pre-oriented in PCB XY axes.
// Placement and side must exactly match expectations; never moves components.
function ComponentModelCount(Comp: IPCB_Component): Integer;
var It: IPCB_GroupIterator; Body: IPCB_ComponentBody;
begin
    Result := 0; It := Comp.GroupIterator_Create;
    It.AddFilter_ObjectSet(MkSet(eComponentBodyObject)); Body := It.FirstPCBObject;
    while Body <> nil do begin Result := Result + 1; Body := It.NextPCBObject; end;
    Comp.GroupIterator_Destroy(It);
end;

function ModelBoardGuard(Path: String; var Board: IPCB_Board): String;
var Doc: IServerDocument;
begin
    Result := PcbPrimitiveProcessGuard(0); if Result <> '' then Exit;
    if not FileExists(Path) then begin Result := 'PCB missing'; Exit; end;
    if Client.CurrentView = nil then begin Result := 'no current view'; Exit; end;
    Doc := Client.CurrentView.OwnerDocument;
    if Doc = nil then begin Result := 'no current document'; Exit; end;
    if not SameText(Doc.Kind, 'PCB') or not SameText(Doc.FileName,Path) then
    begin Result := 'current document differs from exact PCB target'; Exit; end;
    Board := GetBoardSafe(0);
    if Board = nil then begin Result := 'no board'; Exit; end;
    if not SameText(Board.FileName,Path) then Result := 'current board differs from exact PCB target';
end;

function InspectComponentModelsReport(Path: String): String;
var Board: IPCB_Board; It: IPCB_BoardIterator; Body: IPCB_ComponentBody;
    Items: TStringList; R: TCoordRect; Error, Ref: String; N: Integer;
begin
    Error := ModelBoardGuard(Path,Board);
    if Error <> '' then begin Result := PcbDocError(Error); Exit; end;
    Items := TStringList.Create; N := 0;
    It := Board.BoardIterator_Create; It.AddFilter_ObjectSet(MkSet(eComponentBodyObject));
    It.AddFilter_LayerSet(AllLayers); It.AddFilter_Method(eProcessAll);
    Body := It.FirstPCBObject;
    while Body <> nil do begin
        Ref := ''; if Body.Component <> nil then Ref := Body.Component.Name.Text;
        R := Body.BoundingRectangle;
        Items.Add('{"designator":'+JSONStr(Ref)+',"name":'+JSONStr(Body.Name)+
          ',"model_name":'+JSONStr(Body.Model.Name)+',"embedded":'+JSONBool(Body.Model.Embed)+
          ',"data_size":'+IntToStr(Body.Model.DataSize)+',"side":'+IntToStr(Body.BodyProjection)+
          ',"bounds_xy_mm":['+PcbDocNum('0.000000',(R.Left-Board.XOrigin)/393700.7874015748)+','+
          PcbDocNum('0.000000',(R.Bottom-Board.YOrigin)/393700.7874015748)+','+
          PcbDocNum('0.000000',(R.Right-Board.XOrigin)/393700.7874015748)+','+
          PcbDocNum('0.000000',(R.Top-Board.YOrigin)/393700.7874015748)+']}');
        N := N + 1; Body := It.NextPCBObject;
    end;
    Board.BoardIterator_Destroy(It);
    Result := '{"success":true,"count":'+IntToStr(N)+','+BuildJSONArray(Items,'models')+'}';
    Items.Free;
end;

function SetComponentModelsReport(SpecPath: String): String;
var Board: IPCB_Board; Comps: array[0..127] of IPCB_Component;
    Bodies: array[0..127] of IPCB_ComponentBody;
    Spec,Trace: TStringList; Path,Error,Line,Ref,FileName,TracePath,Side: String;
    I,J,N,Added: Integer; R: TCoordRect; X,Y,Rot: Double; Saved,Verified: Boolean;
    It: IPCB_BoardIterator; TestComp: IPCB_Component; Matches: Integer;
begin
    Spec := TStringList.Create; Trace := TStringList.Create;
    TracePath := SpecPath+'.trace.txt'; Added := 0; Saved := False;
    try
        Spec.LoadFromFile(SpecPath); Path := GetFieldFromPipeString(Spec[0],1);
        Error := ModelBoardGuard(Path,Board);
        if Error <> '' then begin Result := PcbDocError(Error); Exit; end;
        N := Spec.Count-1;
        if (N<1) or (N>128) then begin Result := PcbDocError('invalid model count'); Exit; end;
        Trace.Add('preflight'); Trace.SaveToFile(TracePath);
        // Whole batch identity preflight before import or transaction.
        for I := 0 to N-1 do begin
            Line := Spec[I+1]; Ref := GetFieldFromPipeString(Line,1);
            if GetFieldFromPipeString(Line,0)<>'MODEL' then Error := 'invalid record';
            for J := 0 to I-1 do if SameText(Ref,GetFieldFromPipeString(Spec[J+1],1)) then Error := 'duplicate designator';
            Comps[I] := nil; Matches := 0;
            It := Board.BoardIterator_Create; It.AddFilter_ObjectSet(MkSet(eComponentObject));
            It.AddFilter_LayerSet(AllLayers); It.AddFilter_Method(eProcessAll); TestComp := It.FirstPCBObject;
            while TestComp <> nil do begin
                if SameText(TestComp.Name.Text,Ref) then begin Comps[I] := TestComp; Matches := Matches+1; end;
                TestComp := It.NextPCBObject;
            end;
            Board.BoardIterator_Destroy(It);
            if Matches<>1 then Error := 'component missing or ambiguous: '+Ref;
            if Error = '' then begin
                X := SafeStrToFloat(GetFieldFromPipeString(Line,4)); Y := SafeStrToFloat(GetFieldFromPipeString(Line,5));
                Rot := SafeStrToFloat(GetFieldFromPipeString(Line,6)); Side := GetFieldFromPipeString(Line,7);
                if (Abs(Comps[I].X-Board.XOrigin-MMsToCoord(X))>10) or
                   (Abs(Comps[I].Y-Board.YOrigin-MMsToCoord(Y))>10) or
                   (Abs(Comps[I].Rotation-Rot)>0.00001) then Error := 'placement mismatch: '+Ref;
                if Comps[I].Pattern <> GetFieldFromPipeString(Line,8) then Error := 'footprint mismatch: '+Ref;
                if ((Side='top') and (Comps[I].Layer<>eTopLayer)) or
                   ((Side='bottom') and (Comps[I].Layer<>eBottomLayer)) then Error := 'side mismatch: '+Ref;
                if ComponentModelCount(Comps[I])<>0 then Error := 'existing model refused: '+Ref;
                if not FileExists(GetFieldFromPipeString(Line,2)) then Error := 'STEP missing: '+Ref;
            end;
            if Error<>'' then begin Result := PcbDocError(Error); Exit; end;
        end;
        // Import every model off-board, validate data and exact XY before mutation.
        for I := 0 to N-1 do begin
            Line := Spec[I+1]; Ref := GetFieldFromPipeString(Line,1);
            Trace.Add('import '+Ref); Trace.SaveToFile(TracePath);
            Bodies[I] := PCBServer.PCBObjectFactory(eComponentBodyObject,eNoDimension,eCreate_Default);
            Bodies[I].LoadGenericModelFromFile(GetFieldFromPipeString(Line,2),True);
            Bodies[I].Layer := eMechanical13;
            Bodies[I].Name := 'VYBRO3D_'+Ref+'_'+Copy(GetFieldFromPipeString(Line,3),1,12);
            if GetFieldFromPipeString(Line,7)='bottom' then Bodies[I].BodyProjection := eBoardSide_Bottom
            else Bodies[I].BodyProjection := eBoardSide_Top;
            Bodies[I].Model.SetState(0,0,0,0);
            Bodies[I].OverrideColor := True;
            Bodies[I].BodyColor3D := StrToInt(GetFieldFromPipeString(Line,13));
            Bodies[I].BodyOpacity3D := 1.0;
            Bodies[I].MoveByXY(Comps[I].X,Comps[I].Y);
            R := Bodies[I].BoundingRectangle;
            if (Bodies[I].Model.DataSize<=0) or not Bodies[I].Model.Embed then Error := 'empty or unembedded model: '+Ref;
            if (Abs(R.Left-Comps[I].X-MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Line,9))))>MMsToCoord(0.005)) or
               (Abs(R.Bottom-Comps[I].Y-MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Line,10))))>MMsToCoord(0.005)) or
               (Abs(R.Right-Comps[I].X-MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Line,11))))>MMsToCoord(0.005)) or
               (Abs(R.Top-Comps[I].Y-MMsToCoord(SafeStrToFloat(GetFieldFromPipeString(Line,12))))>MMsToCoord(0.005)) then Error := 'model bounds mismatch: '+Ref;
            if Error<>'' then begin Result := PcbDocError(Error); Exit; end;
        end;
        Trace.Add('preprocess'); Trace.SaveToFile(TracePath); PCBServer.PreProcess;
        try
            for I := 0 to N-1 do begin
                Trace.Add('add '+Comps[I].Name.Text); Trace.SaveToFile(TracePath);
                Board.AddPCBObject(Bodies[I]);
                Comps[I].AddPCBObject(Bodies[I]);
                PCBServer.SendMessageToRobots(Board.I_ObjectAddress,c_Broadcast,PCBM_BoardRegisteration,Bodies[I].I_ObjectAddress);
                Added := Added+1; Trace.Add('registered '+Comps[I].Name.Text); Trace.SaveToFile(TracePath);
            end;
        finally PCBServer.PostProcess; end;
        Verified := True;
        for I := 0 to N-1 do begin
            if ComponentModelCount(Comps[I])<>1 then Verified := False;
            if Bodies[I].Component=nil then Verified := False
            else if Bodies[I].Component.Name.Text<>Comps[I].Name.Text then Verified := False;
        end;
        Board.GraphicallyInvalidate; Board.ViewManager_FullUpdate;
        if Verified then Saved := SaveBoardDocument(Path);
        Trace.Add('completed saved='+JSONBool(Saved)); Trace.SaveToFile(TracePath);
        Result := '{"success":'+JSONBool(Saved and Verified)+',"saved":'+JSONBool(Saved)+
          ',"verified":'+JSONBool(Verified)+',"added":'+IntToStr(Added)+',"inventory":'+InspectComponentModelsReport(Path)+'}';
    finally Spec.Free;Trace.Free; end;
end;

function Snapshot3DViewReport(Path: String): String;
var Board: IPCB_Board; Error: String; Saved: Boolean;
begin
    Error := ModelBoardGuard(Path,Board);
    if Error <> '' then begin Result := PcbDocError(Error); Exit; end;
    Saved := SaveBoardDocument(Path);
    Result := '{"success":'+JSONBool(Saved)+',"snapshot_saved":'+JSONBool(Saved)+'}';
end;

function Set3DSilkscreenReport(SpecPath: String): String;
var Board: IPCB_Board; Error,Path: String; Spec: TStringList;
    Applied,Saved: Boolean; Expected: Double;
begin
    Spec := TStringList.Create;
    try
        Spec.LoadFromFile(SpecPath);
        if Spec.Count<>4 then begin Result := PcbDocError('invalid 3D silk spec'); Exit; end;
        Path := Spec[0]; Error := ModelBoardGuard(Path,Board);
        if Error<>'' then begin Result := PcbDocError(Error); Exit; end;
        if Spec[1]<>'.config_3d' then begin Result := PcbDocError('unsupported config kind'); Exit; end;
        Expected := SafeStrToFloat(Spec[3]);
        Applied := Board.SetState_ViewConfigFromString_ForceRebuild(Spec[1],Spec[2]);
        if not Applied then begin Result := PcbDocError('native configuration was refused'); Exit; end;
        if (Abs(Board.GetState_ViewConfigOpacity3D(eTopOverlay)-Expected)>0.00001) or
           (Abs(Board.GetState_ViewConfigOpacity3D(eBottomOverlay)-Expected)>0.00001) then
        begin Result := PcbDocError('native 3D silk readback differs'); Exit; end;
        Board.GraphicallyInvalidate; Board.ViewManager_FullUpdate;
        Saved := SaveBoardDocument(Path);
        Result := '{"success":'+JSONBool(Saved)+',"saved":'+JSONBool(Saved)+
          ',"top_opacity":'+PcbDocNum('0.000000',Board.GetState_ViewConfigOpacity3D(eTopOverlay))+
          ',"bottom_opacity":'+PcbDocNum('0.000000',Board.GetState_ViewConfigOpacity3D(eBottomOverlay))+'}';
    finally Spec.Free; end;
end;


function Set3DBoardOpacityReport(SpecPath: String): String;
var Board: IPCB_Board; Error,Path: String; Spec: TStringList;
    Applied,Saved: Boolean; Expected: Double;
begin
    Spec := TStringList.Create;
    try
        Spec.LoadFromFile(SpecPath);
        if Spec.Count<>4 then begin Result := PcbDocError('invalid3Dopacity spec'); Exit; end;
        Path := Spec[0]; Error := ModelBoardGuard(Path,Board);
        if Error<>'' then begin Result := PcbDocError(Error); Exit; end;
        if Spec[1]<>'.config_3d' then begin Result := PcbDocError('unsupported config kind'); Exit; end;
        Expected := SafeStrToFloat(Spec[3]);
        if (Expected<0) or (Expected>1) then begin Result := PcbDocError('invalid opacity'); Exit; end;
        Applied := Board.SetState_ViewConfigFromString_ForceRebuild(Spec[1],Spec[2]);
        if not Applied then begin Result := PcbDocError('native configuration was refused'); Exit; end;
        Board.GraphicallyInvalidate; Board.ViewManager_FullUpdate;
        Saved := SaveBoardDocument(Path);
        Result := '{"success":'+JSONBool(Saved)+',"saved":'+JSONBool(Saved)+',"native_profile_applied":true}';
    finally Spec.Free; end;
end;
