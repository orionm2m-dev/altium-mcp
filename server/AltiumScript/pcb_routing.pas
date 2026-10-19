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
