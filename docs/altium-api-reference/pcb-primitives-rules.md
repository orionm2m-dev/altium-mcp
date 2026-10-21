# PCB primitives, geometry and rules

Retrieved 2026-10-21. Status: **DOCUMENTED**, runtime unverified.

Primitive fields include layer/net ownership and the legacy keepout Boolean.
Tracks use endpoints plus width; arcs use center, radius, angular limits and
`LineWidth`. Via and pad hole sizes are separate from copper geometry. Regions
have an outer contour and holes. Rule scope expressions select participating
objects; binary scope evaluation and the board's dominant-rule lookup address
different questions. `Gap` is a coordinate quantity.
[Official design-object and rule reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-design-objects-interfaces-reference).

Catalog inheritance is deliberately not flattened. A primitive property listed
here must be resolved against the actual receiver binding; no `.NET` extension
members are inferred. Getter/setter names inside a property declaration are
metadata, not independent proof that both accessor calls resolve from PAS.

Important source boundaries: `Priority` is published as a function; the region
contour setter uses `Pgpc_vertex_list`; several rule properties omit accessors.
These declarations are preserved rather than rewritten into presumed modern
bindings. The reviewed type page has no `TRegionKind` definition: do not infer
numeric enum values or selective keepout masks from this corpus.

## IPCB_Primitive

Source: [PCB API Design Objects Interfaces reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-design-objects-interfaces-reference); section `IPCB_Primitive`.

```pascal
Procedure BeginModify;
Function BoundingRectangle : TCoordRect;
Property Component : IPCB_Component Read GetState_Component Write SetState_Component;
Procedure EndModify;
Function GetState_IsKeepout : Boolean;
Procedure GraphicallyInvalidate;
Function I_ObjectAddress : TPCBObjectHandle;
Property IsKeepout : Boolean Read GetState_IsKeepout Write SetState_IsKeepout;
Property Layer : TLayer Read GetState_Layer Write SetState_Layer;
Procedure MoveByXY (AX, AY : TCoord);
Procedure MoveToXY (AX, AY : TCoord);
Property Net : IPCB_Net Read GetState_Net Write SetState_Net;
Property ObjectId : TObjectId Read GetState_ObjectId;
Property Selected : Boolean Read GetState_Selected Write SetState_Selected;
Procedure SetState_IsKeepout (Value : Boolean);
```

`IsKeepout`: Legacy Boolean flag, not documentation of selective object restriction masks. Property accessor names do not independently prove scripting export.

## IPCB_Track

Source: [PCB API Design Objects Interfaces reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-design-objects-interfaces-reference); section `IPCB_Track`.

```pascal
Property Width : TCoord Read GetState_Width Write SetState_Width;
Property X1 : TCoord Read GetState_X1 Write SetState_X1;
Property X2 : TCoord Read GetState_X2 Write SetState_X2;
Property Y1 : TCoord Read GetState_Y1 Write SetState_Y1;
Property Y2 : TCoord Read GetState_Y2 Write SetState_Y2;
```

## IPCB_Arc

Source: [PCB API Design Objects Interfaces reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-design-objects-interfaces-reference); section `IPCB_Arc`.

```pascal
Property EndAngle : TAngle Read GetState_EndAngle Write SetState_EndAngle;
Property LineWidth : TCoord Read GetState_LineWidth Write SetState_LineWidth;
Property Radius : TCoord Read GetState_Radius Write SetState_Radius;
Property StartAngle : TAngle Read GetState_StartAngle Write SetState_StartAngle;
Property XCenter : TCoord Read GetState_CenterX Write SetState_CenterX;
Property YCenter : TCoord Read GetState_CenterY Write SetState_CenterY;
```

## IPCB_Via

Source: [PCB API Design Objects Interfaces reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-design-objects-interfaces-reference); section `IPCB_Via`.

```pascal
Property HighLayer : TLayer Read GetState_HighLayer Write SetState_HighLayer;
Property HoleSize : TCoord Read GetState_HoleSize Write SetState_HoleSize;
Property LowLayer : TLayer Read GetState_LowLayer Write SetState_LowLayer;
Property Size : TCoord Read GetState_Size Write SetState_Size;
Property X : TCoord Read GetState_XLocation Write SetState_XLocation;
Property Y : TCoord Read GetState_YLocation Write SetState_YLocation;
```

## IPCB_Pad

Source: [PCB API Design Objects Interfaces reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-design-objects-interfaces-reference); section `IPCB_Pad`.

```pascal
Property BotXSize : TCoord Read GetState_BotXSize Write SetState_BotXSize;
Property BotYSize : TCoord Read GetState_BotYSize Write SetState_BotYSize;
Property HoleSize : TCoord Read GetState_HoleSize Write SetState_HoleSize;
Property MidXSize : TCoord Read GetState_MidXSize Write SetState_MidXSize;
Property MidYSize : TCoord Read GetState_MidYSize Write SetState_MidYSize;
Property Mode : TPadMode Read GetState_Mode Write SetState_Mode;
Property Name : TPCBString Read GetState_Name Write SetState_Name;
Property Plated : Boolean Read GetState_Plated Write SetState_Plated;
Property TopXSize : TCoord Read GetState_TopXSize Write SetState_TopXSize;
Property TopYSize : TCoord Read GetState_TopYSize Write SetState_TopYSize;
Property X : TCoord Read GetState_XLocation Write SetState_XLocation;
Property Y : TCoord Read GetState_YLocation Write SetState_YLocation;
```

`Plated`: Plating flag alone does not describe the copper dimensions or the complete pad stack.

## IPCB_Region

Source: [PCB API Design Objects Interfaces reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-design-objects-interfaces-reference); section `IPCB_Region`.

```pascal
Property HoleCount : Integer Read GetHoleCount;
Property Holes [I : Integer] : Pgpc_vertex_list Read GetHole;
Property Kind : TRegionKind Read GetState_Kind Write SetState_Kind;
Property MainContour : Pgpc_vertex_list Read GetMainContour;
Property Name : TDynamicString Read GetState_Name Write SetState_Name;
Procedure SetOutlineContour (Contour : Pgpc_vertex_list)
```

`SetOutlineContour`: Legacy declaration uses Pgpc_vertex_list. Do not substitute IPCB_Contour merely because a later binding or repository example uses it; qualify separately.

## IPCB_Rule

Source: [PCB API Design Objects Interfaces reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-design-objects-interfaces-reference); section `IPCB_Rule`.

```pascal
Function CheckBinaryScope (P1, P2 : IPCB_Primitive) : Boolean;
Property DRCEnabled : Boolean
Property Name : TPCBString
Function Priority : TRulePrecedence;
Property Scope1Expression : TPCBString
Function Scope1Includes (P : IPCB_Primitive) : Boolean;
Property Scope2Expression : TPCBString
Function Scope2Includes (P : IPCB_Primitive) : Boolean;
```

`DRCEnabled`: Source omits accessors; read/write capability is not inferred.

`Name`: Source omits accessors; read/write capability is not inferred.

`Priority`: Documented as a function. This entry does not certify writable Priority property syntax.

`Scope1Expression`: Source omits accessors; read/write capability is not inferred.

`Scope2Expression`: Source omits accessors; read/write capability is not inferred.

## IPCB_ClearanceConstraint

Source: [PCB API Design Objects Interfaces reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-design-objects-interfaces-reference); section `IPCB_ClearanceConstraint`.

```pascal
Property Gap : TCoord
Function PrimitivesViolate(P1, P2 : IPCB_Primitive) : Boolean;
```

`Gap`: Source omits accessors; read/write capability is not inferred.
