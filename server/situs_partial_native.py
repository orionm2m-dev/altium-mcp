"""Fixed single-pad-TP Situs candidate. Pure builder/parser; never dispatches."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from situs_native_settings import _path, _literal, _pairs

TERMINAL_STAGES = frozenset({'returned','guard_failed','readback_failed','native_exception'})
STAGES = TERMINAL_STAGES | {'entered','selection_attempted','dispatch_attempted'}
BOOLS = ('success','route_attempted','selection_attempted','selection_restored',
         'lock_preroutes_verified','old_copper_preserved','return_verified',
         'before_snapshot_complete','after_snapshot_complete')
LISTS = ('before_copper_records','after_copper_records','before_selected_components','after_selected_components')
MAX_RECEIPT = 4 * 1024 * 1024


def build_partial_request(pcb_path, operation_id, component_designator, expected_net):
    pcb = _path(pcb_path,'.pcbdoc','pcb_path')
    if not isinstance(operation_id,str) or not re.fullmatch('[0-9a-f]{32}',operation_id):
        raise ValueError('Unique 32 lowercase hexadecimal operation_id required')
    if not isinstance(component_designator,str) or not re.fullmatch('TP[A-Za-z0-9_]{1,30}',component_designator):
        raise ValueError('Explicit TP component designator required')
    if (not isinstance(expected_net,str) or not expected_net or len(expected_net)>256
            or any(ord(c)<32 for c in expected_net)):
        raise ValueError('Explicit bounded single-line expected net required')
    return dict(schema_version=1,operation_id=operation_id,pcb_path=pcb,
        component_designator=component_designator,expected_net=expected_net,
        process='PCB:Autoroute',action='OnSelectedComponents',routing_strategy='Main',
        requires_lock_preroutes=True,document_save_requested=False,pid_verified_by_native=False,
        drc_verified=False,connectivity_verified=False,routing_complete=False)



def build_pair_request(pcb_path, operation_id, component_designator, partner_designator, expected_net):
    request=build_partial_request(pcb_path,operation_id,component_designator,expected_net)
    if (not isinstance(partner_designator,str)
            or not re.fullmatch('[A-Za-z][A-Za-z0-9_]{1,30}',partner_designator)
            or partner_designator.casefold()==component_designator.casefold()):
        raise ValueError('Explicit distinct partner component designator required')
    return dict(request,partner_designator=partner_designator,action='BetweenSelectedComponents')


def _request(request):
    if not isinstance(request,dict):raise ValueError('Fixed request required')
    if request.get('action') == 'BetweenSelectedComponents':
        keys=('pcb_path','operation_id','component_designator','partner_designator','expected_net')
        builder=build_pair_request
    else:
        keys=('pcb_path','operation_id','component_designator','expected_net')
        builder=build_partial_request
    if any(k not in request for k in keys):raise ValueError('Incomplete request')
    expected=builder(*(request[k] for k in keys))
    if set(request)!=set(expected) or any(type(request[k]) is not type(v) or request[k]!=v for k,v in expected.items()):
        raise ValueError('Fixed request changed')
    return expected


def build_partial_program(request, receipt_path):
    request=_request(request)
    substitutions={'__PCB__':_literal(request['pcb_path']),'__TP__':_literal(request['component_designator']),
        '__NET__':_literal(request['expected_net']),'__RECEIPT__':_literal(_path(str(receipt_path),'.json','receipt_path')),
        '__METADATA__':_literal(json.dumps(request,ensure_ascii=True,separators=(',',':'))[:-1])}
    source=SOURCE
    if request['action']=='BetweenSelectedComponents':
        substitutions['__PARTNER__']=_literal(request['partner_designator'])
        source=PAIR_SOURCE
    if not source:raise ValueError('Native candidate source is not ready')
    return re.sub('|'.join(substitutions),lambda m:substitutions[m.group()],source)


def parse_partial_receipt(data,request):
    expected=_request(request)
    if not isinstance(data,(str,bytes)) or len(data)>MAX_RECEIPT:raise ValueError('Bounded native JSON required')
    result=json.loads(data,object_pairs_hook=_pairs)
    if not isinstance(result,dict) or set(result)!=set(expected)|set(BOOLS)|set(LISTS)|{'stage','error','before_copper_count','after_copper_count'}:
        raise ValueError('Invalid native partial-route schema')
    if any(type(result[k]) is not type(v) or result[k]!=v for k,v in expected.items()):raise ValueError('Native request identity mismatch')
    if not isinstance(result['stage'],str) or result['stage'] not in STAGES or not isinstance(result['error'],str):raise ValueError('Invalid stage/error')
    if any(type(result[k]) is not bool for k in BOOLS):raise ValueError('Invalid Boolean')
    for key in LISTS:
        values=result[key]
        if not isinstance(values,list) or len(values)>10000 or any(not isinstance(v,str) or len(v)>4096 for v in values):
            raise ValueError('Invalid bounded native record list')
    for prefix in ('before','after'):
        count=result[prefix+'_copper_count']
        if type(count) is not int or count!=len(result[prefix+'_copper_records']):raise ValueError('Native count mismatch')
        selected=result[prefix+'_selected_components']
        if len(selected)!=len(set(selected)):raise ValueError('Duplicate selection record')
    preserved=not (Counter(result['before_copper_records'])-Counter(result['after_copper_records']))
    if result['old_copper_preserved'] and (not result['before_snapshot_complete'] or not result['after_snapshot_complete'] or not preserved):
        raise ValueError('Old copper preservation assertion is unsupported')
    if result['selection_restored'] and sorted(result['before_selected_components'])!=sorted(result['after_selected_components']):
        raise ValueError('Selection restoration assertion is unsupported')
    returned=result['stage']=='returned'
    if result['success']!=returned or result['return_verified']!=returned:raise ValueError('Only verified return may report success')
    if returned and (result['error'] or not all(result[k] for k in BOOLS) or not preserved):
        raise ValueError('Returned receipt lacks required native guards/readback')
    if result['stage'] in TERMINAL_STAGES-{'returned'} and not result['error']:raise ValueError('Terminal failure needs an error')
    if result['route_attempted'] and (not result['selection_attempted'] or not result['lock_preroutes_verified']):
        raise ValueError('Route attempt lacks required selection/lock guards')
    if result['stage']=='dispatch_attempted' and not result['route_attempted']:raise ValueError('Dispatch marker lacks attempt')
    if result['stage']=='selection_attempted' and not result['selection_attempted']:raise ValueError('Selection marker lacks attempt')
    unknown=(result['route_attempted'] and not returned) or (result['selection_attempted'] and not result['selection_restored'])
    fingerprints={prefix+'_copper_sha256':hashlib.sha256(json.dumps(sorted(result[prefix+'_copper_records']),ensure_ascii=True,separators=(',',':')).encode()).hexdigest() for prefix in ('before','after')}
    return dict(result,terminal=result['stage'] in TERMINAL_STAGES,outcome_unknown=unknown,retry_allowed=False,
                completion_verified=False,**fingerprints)


def read_partial_receipt(path,request):
    """Read immutable stage files; tolerate a newly created file still being written.

    Each native stage path is written once. No reader opens the destination of
    a later native SaveToFile overwrite; publication never requires UI or a new API.
    """
    path=Path(path)
    missing=dict(success=False,stage="not_observed",terminal=False,return_verified=False,
        outcome_unknown=True,retry_allowed=False,completion_verified=False)
    terminal=[Path(str(path)+'.'+stage+'.json') for stage in sorted(TERMINAL_STAGES)
              if Path(str(path)+'.'+stage+'.json').exists()]
    if len(terminal)>1:raise ValueError('Conflicting terminal receipts')
    candidates=terminal or [Path(str(path)+'.'+stage+'.json') for stage in
        ('dispatch_attempted','selection_attempted','entered') if Path(str(path)+'.'+stage+'.json').exists()]
    if not candidates:return missing
    try:
        with candidates[0].open('rb') as stream:data=stream.read(MAX_RECEIPT+1)
        receipt = parse_partial_receipt(data.decode('utf-8-sig'),request)
        expected_path = Path(str(path) + '.' + receipt['stage'] + '.json')
        if candidates[0] != expected_path:
            raise ValueError('Native stage file/payload mismatch')
        return receipt
    except (OSError,json.JSONDecodeError,UnicodeDecodeError):
        return dict(missing,stage_file_pending=True)


SOURCE = r"""// Fixed native candidate: one TP, global rules unchanged, no board save/UI.
// Host pins exact PID/FILETIME/exe and serializes. Native guards active PCB/idle.
function PBool(Value: Boolean): String;
begin if Value then Result := 'true' else Result := 'false'; end;

function PQuote(Value: String): String;
var I, Code: Integer; Hex: String;
begin
    Result := '"'; Hex := '0123456789abcdef';
    for I := 1 to Length(Value) do
    begin
        Code := Ord(Value[I]);
        if (Value[I] = '\') or (Value[I] = '"') then Result := Result + '\' + Value[I]
        else if Code < 32 then Result := Result + '\u00' + Hex[(Code div 16)+1] + Hex[(Code mod 16)+1]
        else Result := Result + Value[I];
    end;
    Result := Result + '"';
end;

function PArray(Items: TStringList): String;
var I: Integer;
begin
    Result := '[';
    for I := 0 to Items.Count-1 do
    begin if I > 0 then Result := Result + ','; Result := Result + PQuote(Items[I]); end;
    Result := Result + ']';
end;

function PGuard(Board: IPCB_Board): Boolean;
var Active: IPCB_Board; Doc: IServerDocument; Module: IDispatch;
begin
    Result := False;
    if Client = nil then Exit;
    if PCBServer = nil then Exit;
    if Client.CurrentView = nil then Exit;
    Doc := Client.CurrentView.OwnerDocument;
    if Doc = nil then Exit;
    if not SameText(Doc.FileName, __PCB__) or not SameText(Doc.Kind, 'PCB') then Exit;
    Active := PCBServer.GetCurrentPCBBoard;
    if (Board = nil) or (Active = nil) then Exit;
    if not SameText(Active.FileName, __PCB__) or (Board.I_ObjectAddress <> Active.I_ObjectAddress) then Exit;
    Module := Client.ServerModuleByName('PCB');
    if Module = nil then Exit;
    if (Client.ProcessControl.ProcessDepth <> 0) or (Module.ProcessControl.ProcessDepth <> 0) then Exit;
    Result := True;
end;

function PLock: Boolean;
var Options: IPCB_SystemOptions;
begin
    Result := False; Options := PCBServer.SystemOptions;
    if Options = nil then Exit;
    Result := Options.LockPreRoutes;
end;

// Unique component roster and exact component selection; primitives are untouched.
function PComponents(Board: IPCB_Board; Names, Selected: TStringList; var Target: IPCB_Component): Boolean;
var Iter: IPCB_BoardIterator; Comp: IPCB_Component; Name: String; Found: Integer;
begin
    Result := False; Names.Clear; Selected.Clear; Target := nil; Found := 0;
    Iter := Board.BoardIterator_Create;
    try
        Iter.AddFilter_ObjectSet(MkSet(eComponentObject));
        Iter.AddFilter_LayerSet(AllLayers); Iter.AddFilter_Method(eProcessAll);
        Comp := Iter.FirstPCBObject;
        while Comp <> nil do
        begin
            if (Names.Count >= 10000) or (Comp.Name = nil) then Exit;
            Name := Comp.Name.Text;
            if (Name = '') or (Names.IndexOf(Name) >= 0) then Exit;
            Names.Add(Name);
            if Comp.Selected then Selected.Add(Name);
            if Name = __TP__ then begin Target := Comp; Found := Found+1; end;
            Comp := Iter.NextPCBObject;
        end;
        Names.Sort; Selected.Sort; Result := Found = 1;
    finally Board.BoardIterator_Destroy(Iter); end;
end;

function PTargetPad(Target: IPCB_Component): Boolean;
var Iter: IPCB_GroupIterator; Pad: IPCB_Pad; Count: Integer; NetOk: Boolean;
begin
    Result := False; if Target = nil then Exit;
    Count := 0; NetOk := True; Iter := Target.GroupIterator_Create;
    try
        Iter.AddFilter_ObjectSet(MkSet(ePadObject));
        Pad := Iter.FirstPCBObject;
        while Pad <> nil do
        begin
            Count := Count+1; if Count > 1 then Exit;
            if Pad.Net = nil then NetOk := False
            else if Pad.Net.Name <> __NET__ then NetOk := False;
            Pad := Iter.NextPCBObject;
        end;
        Result := (Count = 1) and NetOk;
    finally Target.GroupIterator_Destroy(Iter); end;
end;

procedure PSelect(Board: IPCB_Board; Wanted: TStringList);
var Iter: IPCB_BoardIterator; Comp: IPCB_Component;
begin
    Iter := Board.BoardIterator_Create;
    try
        Iter.AddFilter_ObjectSet(MkSet(eComponentObject));
        Iter.AddFilter_LayerSet(AllLayers); Iter.AddFilter_Method(eProcessAll);
        PCBServer.PreProcess;
        try
            Comp := Iter.FirstPCBObject;
            while Comp <> nil do
            begin Comp.Selected := Wanted.IndexOf(Comp.Name.Text) >= 0; Comp := Iter.NextPCBObject; end;
        finally PCBServer.PostProcess; end;
    finally Board.BoardIterator_Destroy(Iter); end;
end;

// Exact native-unit records, including duplicate multiplicity. No rounded mm keys.
// Covers free tracks/arcs/simple vias, not polygons or component-owned copper.
function PCopper(Board: IPCB_Board; Records: TStringList): Boolean;
var Iter: IPCB_BoardIterator; P: IPCB_Primitive; T: IPCB_Track; V: IPCB_Via; A: IPCB_Arc;
    Rec, NetName: String;
begin
    Result := False; Records.Clear; Iter := Board.BoardIterator_Create;
    try
        Iter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject, eViaObject));
        Iter.AddFilter_LayerSet(AllLayers); Iter.AddFilter_Method(eProcessAll);
        P := Iter.FirstPCBObject;
        while P <> nil do
        begin
            if (not P.InComponent) and not P.InPolygon and not P.IsKeepout then
            begin
                if Records.Count >= 10000 then Exit;
                NetName := ''; if P.Net <> nil then NetName := P.Net.Name;
                Rec := PQuote(NetName) + '|' + IntToStr(P.Layer) + '|';
                if P.ObjectId = eTrackObject then
                begin
                    T := P; Rec := 'T|' + Rec + IntToStr(T.X1) + '|' + IntToStr(T.Y1) + '|' +
                        IntToStr(T.X2) + '|' + IntToStr(T.Y2) + '|' + IntToStr(T.Width);
                end
                else if P.ObjectId = eViaObject then
                begin
                    V := P; if V.Mode <> ePadMode_Simple then Exit;
                    Rec := 'V|' + Rec + IntToStr(V.X) + '|' + IntToStr(V.Y) + '|' + IntToStr(V.Size) + '|' +
                        IntToStr(V.HoleSize) + '|' + IntToStr(V.LowLayer) + '|' + IntToStr(V.HighLayer);
                end
                else
                begin
                    A := P; Rec := 'A|' + Rec + IntToStr(A.XCenter) + '|' + IntToStr(A.YCenter) + '|' +
                        IntToStr(A.Radius) + '|' + FloatToStr(A.StartAngle) + '|' + FloatToStr(A.EndAngle) + '|' + IntToStr(A.LineWidth);
                end;
                Records.Add(Rec);
            end;
            P := Iter.NextPCBObject;
        end;
        Records.Sort; Result := True;
    finally Board.BoardIterator_Destroy(Iter); end;
end;

function PContainsOld(BeforeItems, AfterItems: TStringList): Boolean;
var I, J: Integer;
begin
    I := 0; J := 0;
    while (I < BeforeItems.Count) and (J < AfterItems.Count) do
    begin
        if BeforeItems[I] = AfterItems[J] then begin I := I+1; J := J+1; end
        else if BeforeItems[I] > AfterItems[J] then J := J+1
        else begin Result := False; Exit; end;
    end;
    Result := I = BeforeItems.Count;
end;

function PReceipt(Stage, ErrorCode: String; Success, RouteAttempted, SelectionAttempted, SelectionRestored,
    LockVerified, CopperPreserved, ReturnVerified, BeforeComplete, AfterComplete: Boolean;
    BeforeCopper, AfterCopper, BeforeSelected, AfterSelected: TStringList): Boolean;
var Lines: TStringList; StagePath: String;
begin
    Result := False; StagePath := __RECEIPT__ + '.' + Stage + '.json';
    if FileExists(StagePath) then Exit;
    Lines := TStringList.Create;
    try
        Lines.Text := __METADATA__ + ',"stage":' + PQuote(Stage) + ',"error":' + PQuote(ErrorCode) +
            ',"success":' + PBool(Success) + ',"route_attempted":' + PBool(RouteAttempted) +
            ',"selection_attempted":' + PBool(SelectionAttempted) + ',"selection_restored":' + PBool(SelectionRestored) +
            ',"lock_preroutes_verified":' + PBool(LockVerified) + ',"old_copper_preserved":' + PBool(CopperPreserved) +
            ',"return_verified":' + PBool(ReturnVerified) + ',"before_snapshot_complete":' + PBool(BeforeComplete) +
            ',"after_snapshot_complete":' + PBool(AfterComplete) + ',"before_copper_count":' + IntToStr(BeforeCopper.Count) +
            ',"after_copper_count":' + IntToStr(AfterCopper.Count) + ',"before_copper_records":' + PArray(BeforeCopper) +
            ',"after_copper_records":' + PArray(AfterCopper) + ',"before_selected_components":' + PArray(BeforeSelected) +
            ',"after_selected_components":' + PArray(AfterSelected) + '}';
        Lines.SaveToFile(StagePath); Result := True;
    finally Lines.Free; end;
end;

procedure RunSelectedTPRoute;
var Board: IPCB_Board; Target, CheckTarget: IPCB_Component;
    Names, CheckNames, BeforeSelected, CheckSelected, AfterSelected, Wanted, BeforeCopper, AfterCopper: TStringList;
    Stage, ErrorCode: String;
    SelectionAttempted, RouteAttempted, SelectionRestored, LockVerified, CopperPreserved,
    ReturnVerified, BeforeComplete, AfterComplete, SelectionCaptured: Boolean;
begin
    if FileExists(__RECEIPT__) or FileExists(__RECEIPT__ + '.entered.json') then Exit;
    Names := TStringList.Create; CheckNames := TStringList.Create;
    BeforeSelected := TStringList.Create; CheckSelected := TStringList.Create;
    AfterSelected := TStringList.Create; Wanted := TStringList.Create;
    BeforeCopper := TStringList.Create; AfterCopper := TStringList.Create;
    Board := nil; Target := nil; SelectionAttempted := False; RouteAttempted := False;
    SelectionRestored := False; LockVerified := False; CopperPreserved := False; ReturnVerified := False;
    BeforeComplete := False; AfterComplete := False; SelectionCaptured := False;
    Stage := 'guard_failed'; ErrorCode := 'exact_pcb_or_idle_guard';
    try
        try
            if not PReceipt('entered','',False,False,False,False,False,False,False,False,False,BeforeCopper,AfterCopper,BeforeSelected,AfterSelected) then Exit;
            if PCBServer = nil then Exit;
            Board := PCBServer.GetCurrentPCBBoard;
            if not PGuard(Board) then Exit;
            ErrorCode := 'lock_preroutes_must_already_be_true';
            LockVerified := PLock; if not LockVerified then Exit;
            ErrorCode := 'exact_unique_single_pad_tp_and_net_required';
            if not PComponents(Board,Names,BeforeSelected,Target) then Exit;
            SelectionCaptured := True;
            if not PTargetPad(Target) then Exit;
            ErrorCode := 'bounded_simple_free_copper_snapshot_required';
            BeforeComplete := PCopper(Board,BeforeCopper); if not BeforeComplete then Exit;
            if not PGuard(Board) or not PLock then Exit;
            // Persist possible selection mutation before changing any Selected property.
            SelectionAttempted := True;
            if not PReceipt('selection_attempted','',False,False,True,False,True,False,False,True,False,BeforeCopper,AfterCopper,BeforeSelected,AfterSelected) then Exit;
            Wanted.Add(__TP__); PSelect(Board,Wanted);
            ErrorCode := 'selection_readback_or_identity_failed';
            if not PGuard(Board) then Exit;
            if not PComponents(Board,CheckNames,CheckSelected,CheckTarget) then Exit;
            if (Names.Text <> CheckNames.Text) or (Wanted.Text <> CheckSelected.Text) then Exit;
            if Target.I_ObjectAddress <> CheckTarget.I_ObjectAddress then Exit;
            if not PTargetPad(CheckTarget) or not PLock then Exit;
            RouteAttempted := True;
            if not PReceipt('dispatch_attempted','',False,True,True,False,True,False,False,True,False,BeforeCopper,AfterCopper,BeforeSelected,AfterSelected) then Exit;
            // Only the installed nonmodal selected-component action; no invented parameters.
            ResetParameters;
            AddStringParameter('Action','OnSelectedComponents');
            RunProcess('PCB:Autoroute');
            Stage := 'readback_failed'; ErrorCode := 'exact_return_or_idle_failed';
            if not PGuard(Board) then Exit;
            ErrorCode := 'lock_preroutes_changed_after_return';
            if not PLock then Exit;
            ErrorCode := 'old_free_copper_changed_or_snapshot_incomplete';
            AfterComplete := PCopper(Board,AfterCopper); if not AfterComplete then Exit;
            CopperPreserved := PContainsOld(BeforeCopper,AfterCopper); if not CopperPreserved then Exit;
            Stage := 'returned'; ErrorCode := '';
        except
            Stage := 'native_exception'; ErrorCode := 'native_exception_no_retry';
        end;
    finally
        // Never modify selection on a different document or while native work is active.
        try
            if SelectionCaptured and PGuard(Board) then
            begin
                if PComponents(Board,CheckNames,CheckSelected,CheckTarget) then
                begin
                    if (Names.Text = CheckNames.Text) and (Target.I_ObjectAddress = CheckTarget.I_ObjectAddress) then
                    begin
                        if SelectionAttempted then PSelect(Board,BeforeSelected);
                        if PGuard(Board) then
                            if PComponents(Board,CheckNames,AfterSelected,CheckTarget) then
                                SelectionRestored := (Names.Text = CheckNames.Text) and (BeforeSelected.Text = AfterSelected.Text);
                    end;
                end;
            end;
        except SelectionRestored := False; end;
        if Stage = 'returned' then
        begin
            if SelectionRestored and PGuard(Board) then ReturnVerified := True
            else begin Stage := 'readback_failed'; ErrorCode := 'selection_not_restored_or_final_identity_failed'; end;
        end;
        if not PReceipt(Stage,ErrorCode,ReturnVerified,RouteAttempted,SelectionAttempted,SelectionRestored,
            LockVerified,CopperPreserved,ReturnVerified,BeforeComplete,AfterComplete,BeforeCopper,AfterCopper,BeforeSelected,AfterSelected) then
            ReturnVerified := False;
        AfterCopper.Free; BeforeCopper.Free; Wanted.Free; AfterSelected.Free;
        CheckSelected.Free; BeforeSelected.Free; CheckNames.Free; Names.Free;
    end;
end;
"""


# The pair variant replaces two entire, explicitly delimited routines of the
# frozen single-TP source. No textual substitutions inside unrelated safeguards.
# Changing the old template requires reviewing this derivative and its hash.
_SINGLE_SOURCE_SHA256 = '6616e557e1f79433a9a05612ece5394a7841cad27ab5ed433c585ee5762c962a'

PAIR_COMPONENT_ROUTINES = r"""function PComponents(Board: IPCB_Board; Names, Selected: TStringList;
    var Target, Partner: IPCB_Component): Boolean;
var Iter: IPCB_BoardIterator; Comp: IPCB_Component; Name: String; Found, FoundPartner: Integer;
begin
    Result := False; Names.Clear; Selected.Clear;
    Target := nil; Partner := nil; Found := 0; FoundPartner := 0;
    Iter := Board.BoardIterator_Create;
    try
        Iter.AddFilter_ObjectSet(MkSet(eComponentObject));
        Iter.AddFilter_LayerSet(AllLayers); Iter.AddFilter_Method(eProcessAll);
        Comp := Iter.FirstPCBObject;
        while Comp <> nil do
        begin
            if (Names.Count >= 10000) or (Comp.Name = nil) then Exit;
            Name := Comp.Name.Text;
            if (Name = '') or (Names.IndexOf(Name) >= 0) then Exit;
            Names.Add(Name);
            if Comp.Selected then Selected.Add(Name);
            if Name = __TP__ then begin Target := Comp; Found := Found+1; end;
            if Name = __PARTNER__ then begin Partner := Comp; FoundPartner := FoundPartner+1; end;
            Comp := Iter.NextPCBObject;
        end;
        Names.Sort; Selected.Sort;
        if (Found <> 1) or (FoundPartner <> 1) then Exit;
        Result := Target.I_ObjectAddress <> Partner.I_ObjectAddress;
    finally Board.BoardIterator_Destroy(Iter); end;
end;

// A one-pad TP and exactly one partner pad on its net define one shared net.
// The partner may have other pads; they must not duplicate the target net.
function PPairPads(Target, Partner: IPCB_Component;
    var TargetPad, PartnerPad: IPCB_Pad): Boolean;
var Iter: IPCB_GroupIterator; Pad: IPCB_Pad; Count, Matched: Integer;
begin
    Result := False; TargetPad := nil; PartnerPad := nil;
    if (Target = nil) or (Partner = nil) then Exit;
    Count := 0; Iter := Target.GroupIterator_Create;
    try
        Iter.AddFilter_ObjectSet(MkSet(ePadObject));
        Pad := Iter.FirstPCBObject;
        while Pad <> nil do
        begin
            Count := Count+1; if Count > 1 then Exit;
            if Pad.Net = nil then Exit;
            if Pad.Net.Name <> __NET__ then Exit;
            TargetPad := Pad; Pad := Iter.NextPCBObject;
        end;
        if (Count <> 1) or (TargetPad = nil) then Exit;
    finally Target.GroupIterator_Destroy(Iter); end;
    Count := 0; Matched := 0; Iter := Partner.GroupIterator_Create;
    try
        Iter.AddFilter_ObjectSet(MkSet(ePadObject));
        Pad := Iter.FirstPCBObject;
        while Pad <> nil do
        begin
            Count := Count+1; if Count > 10000 then Exit;
            if Pad.Net <> nil then
                if Pad.Net.Name = __NET__ then
                begin
                    Matched := Matched+1; if Matched > 1 then Exit;
                    PartnerPad := Pad;
                end;
            Pad := Iter.NextPCBObject;
        end;
        if (Matched <> 1) or (PartnerPad = nil) then Exit;
        Result := TargetPad.I_ObjectAddress <> PartnerPad.I_ObjectAddress;
    finally Partner.GroupIterator_Destroy(Iter); end;
end;

"""

PAIR_ROUTE_ROUTINE = r"""procedure RunSelectedTPRoute;
var Board: IPCB_Board; Target, CheckTarget, Partner, CheckPartner: IPCB_Component;
    TargetPad, PartnerPad, CheckTargetPad, CheckPartnerPad: IPCB_Pad;
    Names, CheckNames, BeforeSelected, CheckSelected, AfterSelected, Wanted, BeforeCopper, AfterCopper: TStringList;
    Stage, ErrorCode: String;
    SelectionAttempted, RouteAttempted, SelectionRestored, LockVerified, CopperPreserved,
    ReturnVerified, BeforeComplete, AfterComplete, SelectionCaptured: Boolean;
begin
    if FileExists(__RECEIPT__) or FileExists(__RECEIPT__ + '.entered.json') then Exit;
    Names := TStringList.Create; CheckNames := TStringList.Create;
    BeforeSelected := TStringList.Create; CheckSelected := TStringList.Create;
    AfterSelected := TStringList.Create; Wanted := TStringList.Create;
    BeforeCopper := TStringList.Create; AfterCopper := TStringList.Create;
    Board := nil; Target := nil; Partner := nil; TargetPad := nil; PartnerPad := nil;
    SelectionAttempted := False; RouteAttempted := False;
    SelectionRestored := False; LockVerified := False; CopperPreserved := False; ReturnVerified := False;
    BeforeComplete := False; AfterComplete := False; SelectionCaptured := False;
    Stage := 'guard_failed'; ErrorCode := 'exact_pcb_or_idle_guard';
    try
        try
            if not PReceipt('entered','',False,False,False,False,False,False,False,False,False,BeforeCopper,AfterCopper,BeforeSelected,AfterSelected) then Exit;
            if PCBServer = nil then Exit;
            Board := PCBServer.GetCurrentPCBBoard;
            if not PGuard(Board) then Exit;
            ErrorCode := 'lock_preroutes_must_already_be_true';
            LockVerified := PLock; if not LockVerified then Exit;
            ErrorCode := 'exact_unique_tp_partner_and_single_shared_net_required';
            if not PComponents(Board,Names,BeforeSelected,Target,Partner) then Exit;
            if not PPairPads(Target,Partner,TargetPad,PartnerPad) then Exit;
            SelectionCaptured := True;
            ErrorCode := 'bounded_simple_free_copper_snapshot_required';
            BeforeComplete := PCopper(Board,BeforeCopper); if not BeforeComplete then Exit;
            if not PGuard(Board) or not PLock then Exit;
            // Persist the possible selection mutation before changing selection.
            SelectionAttempted := True;
            if not PReceipt('selection_attempted','',False,False,True,False,True,False,False,True,False,BeforeCopper,AfterCopper,BeforeSelected,AfterSelected) then Exit;
            Wanted.Add(__TP__); Wanted.Add(__PARTNER__); Wanted.Sort;
            PSelect(Board,Wanted);
            ErrorCode := 'pair_selection_readback_or_identity_failed';
            if not PGuard(Board) then Exit;
            if not PComponents(Board,CheckNames,CheckSelected,CheckTarget,CheckPartner) then Exit;
            if (Names.Text <> CheckNames.Text) or (Wanted.Text <> CheckSelected.Text) then Exit;
            if (Target.I_ObjectAddress <> CheckTarget.I_ObjectAddress) or
                (Partner.I_ObjectAddress <> CheckPartner.I_ObjectAddress) then Exit;
            if not PPairPads(CheckTarget,CheckPartner,CheckTargetPad,CheckPartnerPad) then Exit;
            if (TargetPad.I_ObjectAddress <> CheckTargetPad.I_ObjectAddress) or
                (PartnerPad.I_ObjectAddress <> CheckPartnerPad.I_ObjectAddress) then Exit;
            if not PLock then Exit;
            RouteAttempted := True;
            if not PReceipt('dispatch_attempted','',False,True,True,False,True,False,False,True,False,BeforeCopper,AfterCopper,BeforeSelected,AfterSelected) then Exit;
            // Installed native pair action; no dialog, guessed parameters or rule edits.
            ResetParameters;
            AddStringParameter('Action','BetweenSelectedComponents');
            RunProcess('PCB:Autoroute');
            Stage := 'readback_failed'; ErrorCode := 'exact_return_or_idle_failed';
            if not PGuard(Board) then Exit;
            ErrorCode := 'lock_preroutes_changed_after_return';
            if not PLock then Exit;
            ErrorCode := 'pair_roster_or_pad_identity_changed_after_return';
            if not PComponents(Board,CheckNames,CheckSelected,CheckTarget,CheckPartner) then Exit;
            if (Names.Text <> CheckNames.Text) or (Wanted.Text <> CheckSelected.Text) then Exit;
            if (Target.I_ObjectAddress <> CheckTarget.I_ObjectAddress) or
                (Partner.I_ObjectAddress <> CheckPartner.I_ObjectAddress) then Exit;
            if not PPairPads(CheckTarget,CheckPartner,CheckTargetPad,CheckPartnerPad) then Exit;
            if (TargetPad.I_ObjectAddress <> CheckTargetPad.I_ObjectAddress) or
                (PartnerPad.I_ObjectAddress <> CheckPartnerPad.I_ObjectAddress) then Exit;
            ErrorCode := 'old_free_copper_changed_or_snapshot_incomplete';
            AfterComplete := PCopper(Board,AfterCopper); if not AfterComplete then Exit;
            CopperPreserved := PContainsOld(BeforeCopper,AfterCopper); if not CopperPreserved then Exit;
            Stage := 'returned'; ErrorCode := '';
        except
            Stage := 'native_exception'; ErrorCode := 'native_exception_no_retry';
        end;
    finally
        // Restore only while the exact original board, components and pads still exist.
        try
            if SelectionCaptured and PGuard(Board) then
            begin
                if PComponents(Board,CheckNames,CheckSelected,CheckTarget,CheckPartner) then
                begin
                    if (Names.Text = CheckNames.Text) and
                        (Target.I_ObjectAddress = CheckTarget.I_ObjectAddress) and
                        (Partner.I_ObjectAddress = CheckPartner.I_ObjectAddress) then
                    begin
                        if PPairPads(CheckTarget,CheckPartner,CheckTargetPad,CheckPartnerPad) then
                            if (TargetPad.I_ObjectAddress = CheckTargetPad.I_ObjectAddress) and
                                (PartnerPad.I_ObjectAddress = CheckPartnerPad.I_ObjectAddress) then
                            begin
                                if SelectionAttempted then PSelect(Board,BeforeSelected);
                                if PGuard(Board) then
                                    if PComponents(Board,CheckNames,AfterSelected,CheckTarget,CheckPartner) then
                                        if PPairPads(CheckTarget,CheckPartner,CheckTargetPad,CheckPartnerPad) then
                                            SelectionRestored := (Names.Text = CheckNames.Text) and
                                                (BeforeSelected.Text = AfterSelected.Text) and
                                                (Target.I_ObjectAddress = CheckTarget.I_ObjectAddress) and
                                                (Partner.I_ObjectAddress = CheckPartner.I_ObjectAddress) and
                                                (TargetPad.I_ObjectAddress = CheckTargetPad.I_ObjectAddress) and
                                                (PartnerPad.I_ObjectAddress = CheckPartnerPad.I_ObjectAddress);
                            end;
                    end;
                end;
            end;
        except SelectionRestored := False; end;
        if Stage = 'returned' then
        begin
            if SelectionRestored and PGuard(Board) then ReturnVerified := True
            else begin Stage := 'readback_failed'; ErrorCode := 'selection_not_restored_or_final_identity_failed'; end;
        end;
        if not PReceipt(Stage,ErrorCode,ReturnVerified,RouteAttempted,SelectionAttempted,SelectionRestored,
            LockVerified,CopperPreserved,ReturnVerified,BeforeComplete,AfterComplete,BeforeCopper,AfterCopper,BeforeSelected,AfterSelected) then
            ReturnVerified := False;
        AfterCopper.Free; BeforeCopper.Free; Wanted.Free; AfterSelected.Free;
        CheckSelected.Free; BeforeSelected.Free; CheckNames.Free; Names.Free;
    end;
end;
"""


def _build_pair_source(single_source):
    if hashlib.sha256(single_source.encode()).hexdigest() != _SINGLE_SOURCE_SHA256:
        raise ValueError('Single-TP source changed: pair derivative requires review')
    component_start='function PComponents('
    selection_start='procedure PSelect('
    route_start='procedure RunSelectedTPRoute;'
    for marker in (component_start,selection_start,route_start):
        if single_source.count(marker)!=1:
            raise ValueError('Ambiguous pair source routine boundary')
    first=single_source.index(component_start)
    middle=single_source.index(selection_start)
    last=single_source.index(route_start)
    if not first < middle < last:
        raise ValueError('Invalid pair source routine ordering')
    return (single_source[:first] + PAIR_COMPONENT_ROUTINES +
            single_source[middle:last] + PAIR_ROUTE_ROUTINE)


PAIR_SOURCE = _build_pair_source(SOURCE)
