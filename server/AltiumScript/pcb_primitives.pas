// Native PCB pad changes and object-specific keepouts. Coordinates are mm
// relative to the board origin. Python validates every record before dispatch.
// Existing targets are resolved and checked before the first board mutation.

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
begin
    Result := nil;
    Count := 0;
    Iter := Board.BoardIterator_Create;
    try
        // Search every primitive so a foreign object with this ID cannot be overwritten.
        Iter.AddFilter_ObjectSet(AllObjects);
        Iter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        P := Iter.FirstPCBObject;
        while P <> nil do
        begin
            if P.UniqueId = Id then
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

function KeepoutObjectId(Kind: String): Integer;
begin
    if Kind = 'track' then Result := eTrackObject
    else if Kind = 'arc' then Result := eArcObject
    else if Kind = 'fill' then Result := eFillObject
    else Result := eRegionObject;
end;

function SetPcbKeepoutsReport(SpecPath: String): String;
var
    Spec, Items : TStringList;
    Board : IPCB_Board;
    P : IPCB_Primitive;
    Track : IPCB_Track;
    Arc : IPCB_Arc;
    Fill : IPCB_Fill;
    Region : IPCB_Region;
    Contour : IPCB_Contour;
    PcbPath, Rec, Id, Kind, Error, ShapeJSON : String;
    i, j, Count, Mask, Added, Updated : Integer;
    TargetLayer : TLayer;
    IsNew, Saved, Verified : Boolean;
begin
    if not FileExists(SpecPath) then
    begin
        Result := PcbDocError('keepout spec does not exist');
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
        for i := 1 to Spec.Count - 1 do
        begin
            Rec := Spec[i];
            Id := GetFieldFromPipeString(Rec, 1);
            Kind := GetFieldFromPipeString(Rec, 2);
            TargetLayer := PrimitiveLayer(GetFieldFromPipeString(Rec, 3));
            if (TargetLayer <> eKeepOutLayer) and not MidLayerInStack(Board.LayerStack_V7, TargetLayer) then
                Error := 'requested copper layer is not in the board stack: ' + GetFieldFromPipeString(Rec, 3);
            P := FindKeepoutPrimitive(Board, Id, Count);
            if Count > 1 then Error := 'duplicate primitive unique_id: ' + Id;
            if P <> nil then
            begin
                if not P.IsKeepout then Error := 'unique_id belongs to a non-keepout primitive: ' + Id;
                if P.Component <> nil then Error := 'component-owned keepout cannot be changed: ' + Id;
                if P.ObjectId <> KeepoutObjectId(Kind) then Error := 'keepout kind cannot change: ' + Id;
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
        Verified := True;
        PCBServer.PreProcess;
        try
            for i := 1 to Spec.Count - 1 do
            begin
                Rec := Spec[i];
                Id := GetFieldFromPipeString(Rec, 1);
                Kind := GetFieldFromPipeString(Rec, 2);
                TargetLayer := PrimitiveLayer(GetFieldFromPipeString(Rec, 3));
                Mask := StrToInt(GetFieldFromPipeString(Rec, 4));
                P := FindKeepoutPrimitive(Board, Id, Count);
                IsNew := P = nil;
                if IsNew then
                    P := PCBServer.PCBObjectFactory(KeepoutObjectId(Kind), eNoDimension, eCreate_Default)
                else PCBServer.SendMessageToRobots(P.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
                P.UniqueId := Id;
                P.Layer := TargetLayer;
                P.Net := nil;
                // Legacy scripting API enables an all-object keepout. Selective
                // restrictions are rejected by the typed tool before dispatch.
                P.IsKeepout := True;
                P.Moveable := GetFieldFromPipeString(Rec, 5) = '0';
                ShapeJSON := '';
                if Kind = 'track' then
                begin
                    Track := P;
                    Track.X1 := Board.XOrigin + PrimitiveCoord(Rec, 6);
                    Track.Y1 := Board.YOrigin + PrimitiveCoord(Rec, 7);
                    Track.X2 := Board.XOrigin + PrimitiveCoord(Rec, 8);
                    Track.Y2 := Board.YOrigin + PrimitiveCoord(Rec, 9);
                    Track.Width := PrimitiveCoord(Rec, 10);
                    ShapeJSON := ', "x1_mm": ' + PcbDocNum('0.######', CoordToMMs(Track.X1 - Board.XOrigin)) +
                        ', "y1_mm": ' + PcbDocNum('0.######', CoordToMMs(Track.Y1 - Board.YOrigin)) +
                        ', "x2_mm": ' + PcbDocNum('0.######', CoordToMMs(Track.X2 - Board.XOrigin)) +
                        ', "y2_mm": ' + PcbDocNum('0.######', CoordToMMs(Track.Y2 - Board.YOrigin)) +
                        ', "width_mm": ' + PcbDocNum('0.######', CoordToMMs(Track.Width));
                end
                else if Kind = 'arc' then
                begin
                    Arc := P;
                    Arc.XCenter := Board.XOrigin + PrimitiveCoord(Rec, 6);
                    Arc.YCenter := Board.YOrigin + PrimitiveCoord(Rec, 7);
                    Arc.Radius := PrimitiveCoord(Rec, 8);
                    Arc.StartAngle := SafeStrToFloat(GetFieldFromPipeString(Rec, 9));
                    Arc.EndAngle := SafeStrToFloat(GetFieldFromPipeString(Rec, 10));
                    Arc.LineWidth := PrimitiveCoord(Rec, 11);
                    ShapeJSON := ', "x_mm": ' + PcbDocNum('0.######', CoordToMMs(Arc.XCenter - Board.XOrigin)) +
                        ', "y_mm": ' + PcbDocNum('0.######', CoordToMMs(Arc.YCenter - Board.YOrigin)) +
                        ', "radius_mm": ' + PcbDocNum('0.######', CoordToMMs(Arc.Radius)) +
                        ', "start_deg": ' + PcbDocNum('0.######', Arc.StartAngle) +
                        ', "end_deg": ' + PcbDocNum('0.######', Arc.EndAngle) +
                        ', "width_mm": ' + PcbDocNum('0.######', CoordToMMs(Arc.LineWidth));
                end
                else if Kind = 'fill' then
                begin
                    Fill := P;
                    Fill.X1 := Board.XOrigin + PrimitiveCoord(Rec, 6);
                    Fill.Y1 := Board.YOrigin + PrimitiveCoord(Rec, 7);
                    Fill.X2 := Board.XOrigin + PrimitiveCoord(Rec, 8);
                    Fill.Y2 := Board.YOrigin + PrimitiveCoord(Rec, 9);
                    Fill.Rotation := 0;
                    ShapeJSON := ', "x1_mm": ' + PcbDocNum('0.######', CoordToMMs(Fill.X1 - Board.XOrigin)) +
                        ', "y1_mm": ' + PcbDocNum('0.######', CoordToMMs(Fill.Y1 - Board.YOrigin)) +
                        ', "x2_mm": ' + PcbDocNum('0.######', CoordToMMs(Fill.X2 - Board.XOrigin)) +
                        ', "y2_mm": ' + PcbDocNum('0.######', CoordToMMs(Fill.Y2 - Board.YOrigin));
                end
                else
                begin
                    Region := P;
                    Contour := PCBServer.PCBContourFactory;
                    j := 6;
                    while GetFieldFromPipeString(Rec, j) <> '' do
                    begin
                        Contour.AddPoint(Board.XOrigin + PrimitiveCoord(Rec, j), Board.YOrigin + PrimitiveCoord(Rec, j + 1));
                        j := j + 2;
                    end;
                    Region.SetOutlineContour(Contour);
                    Region.Kind := eRegionKind_Copper;
                    ShapeJSON := ', "vertex_count": ' + IntToStr((j - 6) div 2);
                end;
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
                if (P.Layer <> TargetLayer) or not P.IsKeepout then Verified := False;
                Items.Add('{"unique_id": ' + JSONStr(P.UniqueId) + ', "kind": ' + JSONStr(Kind) +
                    ', "layer": ' + JSONStr(Layer2String(P.Layer)) + ', "is_keepout": ' + JSONBool(P.IsKeepout) +
                    ', "locked": ' + JSONBool(not P.Moveable) + ShapeJSON + '}');
            end;
        finally
            PCBServer.PostProcess;
        end;
        Board.GraphicallyInvalidate;
        Board.ViewManager_FullUpdate;
        Saved := False;
        if Verified then Saved := SaveBoardDocument(PcbPath);
        Result := '{"success": ' + JSONBool(Saved and Verified) + ', "saved": ' + JSONBool(Saved) +
            ', "keepout_flag_verified": ' + JSONBool(Verified) + ', "path": ' + JSONStr(PcbPath) +
            ', "added": ' + IntToStr(Added) + ', "updated": ' + IntToStr(Updated) +
            ', ' + BuildJSONArray(Items, 'keepouts') + '}';
    finally
        Spec.Free;
        Items.Free;
    end;
end;
