// Read-only qualification. Enumeration: official System API / IClient.
// Client.GetCount/GetServerModule, IServerModule.DocumentCount/Documents,
// IServerDocument.FileName/Kind/Modified. Current SDK metadata independently checked.
// Pascal bindings remain unqualified until an explicit native probe.
function InvQuote(Value: String): String;
var N, Code: Integer; Hex: String;
begin
    Result := '"'; Hex := '0123456789abcdef';
    for N := 1 to Length(Value) do
    begin
        Code := Ord(Value[N]);
        if (Value[N] = '\') or (Value[N] = '"') then Result := Result + '\' + Value[N]
        else if Code < 32 then Result := Result + '\u00' + Hex[(Code div 16) + 1] + Hex[(Code mod 16) + 1]
        else Result := Result + Value[N];
    end;
    Result := Result + '"';
end;

function InvBool(Value: Boolean): String;
begin if Value then Result := 'true' else Result := 'false'; end;

function InvGuard(OriginalBoard: IPCB_Board): Boolean;
var ActiveBoard: IPCB_Board; Doc: IServerDocument; PcbModule: IServerModule;
begin
    Result := False;
    if Client = nil then Exit;
    if PCBServer = nil then Exit;
    if Client.CurrentView = nil then Exit;
    Doc := Client.CurrentView.OwnerDocument;
    if Doc = nil then Exit;
    if not SameText(Doc.FileName, 'C:\qualification\sample.PcbDoc') or not SameText(Doc.Kind, 'PCB') then Exit;
    ActiveBoard := PCBServer.GetCurrentPCBBoard;
    if (OriginalBoard = nil) or (ActiveBoard = nil) then Exit;
    if OriginalBoard.I_ObjectAddress <> ActiveBoard.I_ObjectAddress then Exit;
    if not SameText(ActiveBoard.FileName, 'C:\qualification\sample.PcbDoc') then Exit;
    PcbModule := Client.GetServerModuleByName('PCB');
    if PcbModule = nil then Exit;
    if (Client.ProcessControl.ProcessDepth <> 0) or (PcbModule.ProcessControl.ProcessDepth <> 0) then Exit;
    Result := True;
end;

function InvCollect(var Complete: Boolean): String;
var I, J, ModuleCount, DocumentCount: Integer; Module: IServerModule; Doc: IServerDocument;
begin
    Complete := False; Result := '[';
    ModuleCount := Client.GetCount;
    if (ModuleCount < 0) or (ModuleCount > 4096) then Exit;
    for I := 0 to ModuleCount - 1 do
    begin
        Module := Client.GetServerModule(I);
        if Module = nil then Exit;
        DocumentCount := Module.DocumentCount;
        if (DocumentCount < 0) or (DocumentCount > 100000) then Exit;
        if I > 0 then Result := Result + ',';
        Result := Result + '{"module_index":' + IntToStr(I) + ',"module":' + InvQuote(Module.ModuleName) + ',"documents":[';
        for J := 0 to DocumentCount - 1 do
        begin
            Doc := Module.Documents[J];
            if Doc = nil then Exit;
            if J > 0 then Result := Result + ',';
            Result := Result + '{"document_index":' + IntToStr(J) + ',"kind":' + InvQuote(Doc.Kind) +
                ',"path":' + InvQuote(Doc.FileName) + ',"modified":' + InvBool(Doc.Modified) + '}';
        end;
        if Module.DocumentCount <> DocumentCount then Exit;
        Result := Result + ']}';
    end;
    if Client.GetCount <> ModuleCount then Exit;
    Result := Result + ']'; Complete := True;
end;

procedure RunGetOpenDocuments;
var Board: IPCB_Board; First, Second, ErrorCode, Payload: String;
    CompleteFirst, CompleteSecond, Complete: Boolean; Lines: TStringList;
begin
    // Per-request file must not exist. The host serializes and pins exact process.
    if FileExists('C:\work\altium-mcp\dev\probes\open_documents\static_fixture\f41a2b2b68f44ac8b85e8fd88ee88cab.json') then Exit;
    Complete := False; ErrorCode := ''; Payload := '[]';
    try
        if PCBServer = nil then ErrorCode := 'pcb_server_missing'
        else
        begin
            Board := PCBServer.GetCurrentPCBBoard;
            if not InvGuard(Board) then ErrorCode := 'exact_current_pcb_or_idle_guard'
            else
            begin
                First := InvCollect(CompleteFirst);
                Second := InvCollect(CompleteSecond);
                Complete := CompleteFirst and CompleteSecond and (First = Second) and InvGuard(Board);
                if Complete then Payload := Second else ErrorCode := 'inventory_or_current_pcb_changed_or_incomplete';
            end;
        end;
    except
        ErrorCode := 'native_inventory_exception'; Complete := False;
    end;
    Lines := TStringList.Create;
    try
        Lines.Text := '{"schema_version":1,"request_id":' + InvQuote('f41a2b2b68f44ac8b85e8fd88ee88cab') +
            ',"success":' + InvBool(Complete) + ',"complete":' + InvBool(Complete) +
            ',"pcb_path":' + InvQuote('C:\qualification\sample.PcbDoc') + ',"current_pcb_preserved":' + InvBool(Complete) +
            ',"document_save_requested":false,"document_close_requested":false,"pid_verified_by_native":false,' +
            '"scope":"loaded_server_document_containers","error":' + InvQuote(ErrorCode) + ',"modules":' + Payload + '}';
        if not FileExists('C:\work\altium-mcp\dev\probes\open_documents\static_fixture\f41a2b2b68f44ac8b85e8fd88ee88cab.json') then Lines.SaveToFile('C:\work\altium-mcp\dev\probes\open_documents\static_fixture\f41a2b2b68f44ac8b85e8fd88ee88cab.json');
    finally Lines.Free; end;
end;
