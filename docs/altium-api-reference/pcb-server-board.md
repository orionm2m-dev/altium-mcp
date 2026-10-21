# PCB server, board and iteration

Retrieved 2026-10-21. Status: **DOCUMENTED**, runtime unverified.

`PCBServer` is the documented gateway. `GetCurrentPCBBoard` may represent a PCB
or PCB library, so an implementation must check the actual target before editing.
Factory creation and insertion are separate operations. Pair `PreProcess` with
`PostProcess` and register changes with the appropriate robot messages. Iterators
must be destroyed after enumeration, including error paths. The board's dominant
rule functions help inspect the rule selected for actual objects, not just rule
names or nominal priorities.
[Official PCB system reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-system-interfaces-reference).

`AddFilter_Method(eProcessAll)` includes objects according to the iterator's
method; free-only/component enumeration uses different documented values.
Use `MkSet(...)` for script object/layer sets.
[Official iterator reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-design-objects-interfaces-reference).

The DRC options type is documented, but its existence does not supply a callable
board accessor. The negative compatibility catalog records the failed
`IPCB_Board.GetState_DesignRuleCheckerOptions` receiver/member pair. Do not infer
that a missing runtime binding can be repaired with a type cast.

## Original read-only illustration

This count example uses the documented iterator lifecycle and an exact filename
guard. It has not been executed. A negative result means the requested current
document or iterator was unavailable. It does not open or save a document.

```pascal
Function CountTracksOnCurrentBoard(ExpectedFile : String) : Integer;
Var
    TargetBoard : IPCB_Board;
    TrackIterator : IPCB_BoardIterator;
    TrackObject : IPCB_Primitive;
Begin
    Result := -1;
    If PCBServer = Nil Then Exit;
    TargetBoard := PCBServer.GetCurrentPCBBoard;
    If TargetBoard = Nil Then Exit;
    If TargetBoard.FileName <> ExpectedFile Then Exit;
    TrackIterator := TargetBoard.BoardIterator_Create;
    If TrackIterator = Nil Then Exit;
    Try
        TrackIterator.AddFilter_ObjectSet(MkSet(eTrackObject));
        TrackIterator.AddFilter_LayerSet(AllLayers);
        TrackIterator.AddFilter_Method(eProcessAll);
        Result := 0;
        TrackObject := TrackIterator.FirstPCBObject;
        While TrackObject <> Nil Do
        Begin
            Result := Result + 1;
            TrackObject := TrackIterator.NextPCBObject;
        End;
    Finally
        TargetBoard.BoardIterator_Destroy(TrackIterator);
    End;
End;
```

## IPCB_ServerInterface

Source: [PCB API System Interfaces reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-system-interfaces-reference); section `IPCB_ServerInterface`.

```pascal
Function GetCurrentPCBBoard : IPCB_Board;
Function GetPCBBoardByPath (APath : TPCBString) : IPCB_Board;
Function PCBContourFactory (AArcResolution : TCoord; Const ACX : TCoord = 0; Const ACY : TCoord = 0; Const ARotation : TAngle = 0) : IPCB_Contour;
Function PCBObjectFactory(Const AObjectId : TObjectId; Const ADimensionKind : TDimensionKind = eNoDimension; Const ACreationMode : TObjectCreationMode = eCreate_Default) : IPCB_Primitive;
Function PCBRuleFactory(Const ARuleKind : TRuleKind) : IPCB_Rule;
Procedure PostProcess;
Procedure PreProcess;
Procedure SendMessageToRobots(Source, Destination : Pointer; MessageID : Word; MessageData : Pointer);
```

## IPCB_Board

Source: [PCB API System Interfaces reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-system-interfaces-reference); section `IPCB_Board`.

```pascal
Procedure AddPCBObject(PCBObject : IPCB_Primitive);
Function BoardIterator_Create : IPCB_BoardIterator;
Procedure BoardIterator_Destroy(Var AIterator : IPCB_BoardIterator);
Property BoardOutline : IPCB_BoardOutline Read GetState_BoardOutline;
Property CurrentLayer : TLayer Read GetState_CurrentLayer;
Property FileName : TPCBString Read GetState_FileName;
Function FindDominantRuleForObject(APrimitive : IPCB_Primitive; ARuleKind : TRuleKInd) : IPCB_Rule;
Function FindDominantRuleForObjectPair(APrimitive1, APrimitive2 : IPCB_Primitive; ARuleKind : TRuleKInd) : IPCB_Rule;
Function GetPrimitiveCount(AObjSet : TObjectSet; LayerSet : TLayerSet; AMethod : TIterationMethod) :Integer;
Function SpatialIterator_Create : IPCB_SpatialIterator;
Procedure SpatialIterator_Destroy(Var AIterator : IPCB_SpatialIterator);
Property XOrigin : TCoord Read GetState_XOrigin Write SetState_XOrigin;
Property YOrigin : TCoord Read GetState_YOrigin Write SetState_YOrigin;
```

## IPCB_BoardIterator

Source: [PCB API Design Objects Interfaces reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-design-objects-interfaces-reference); section `IPCB_BoardIterator`.

```pascal
Procedure AddFilter_LayerSet (ALayerSet : TLayerSet);
Procedure AddFilter_Method (AMethod : TIterationMethod);
Procedure AddFilter_ObjectSet (AObjectSet : TObjectSet);
Function FirstPCBObject : IPCB_Primitive;
Function NextPCBObject : IPCB_Primitive
```

`NextPCBObject`: Source omits the terminating semicolon; preserved as published.

## IPCB_DesignRuleCheckerOptions

Source: [PCB API System Interfaces reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-system-interfaces-reference); section `IPCB_DesignRuleCheckerOptions`.

```pascal
Property MaxViolationCount : Integer
```

`MaxViolationCount`: Source omits accessors and does not establish a DelphiScript path to obtain this interface. IPCB_Board.GetState_DesignRuleCheckerOptions has separate negative runtime evidence.
