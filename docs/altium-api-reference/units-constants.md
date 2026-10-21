# Units and PCB message constants

Retrieved 2026-10-21. Status: **DOCUMENTED**, runtime unverified.

The legacy type reference defines `TCoord` as `Integer`, `TAngle` as `Double`,
`TPCBString` as `WideString`, and `TPCBObjectHandle` as `Pointer`.
`TCoordRect` exposes left/bottom/right/top coordinates. These SDK type facts do
not override DelphiScript Variant semantics or prove that a pointer-typed SDK
argument accepts an arbitrary script value.
[Official types reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-types-reference).

Use named unit conversions for positions, sizes and clearances; display units
are not the coordinate representation. The board reference describes 10,000
internal coordinate units per mil.
[Official board reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-system-interfaces-reference).

```pascal
Function CoordToMils (C : TCoord) : TReal;
Function CoordToMMs (C : TCoord) : TReal;
Function MilsToCoord (M : TReal) : TCoord;
Function MMsToCoord (M : TReal) : TCoord;
```

[Official conversion declarations](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-functions-reference).

Robot notification names include `PCBM_BeginModify`, `PCBM_EndModify` and the
historically spelled `PCBM_BoardRegisteration`. Preserve the documented spelling.
`c_BroadCast` and `c_NoEventData` are notification constants, not geometry or
object IDs. `AllLayers` is a documented layer-set constant; its historical enum
range is not a modern stack inventory.
[Official constants reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-constants-reference).
