# Polygon connection style: scoped enum setter

Updated 2026-10-22. Offline validated; native qualification is pending.

`set_pcb_rules` accepts kind `PolygonConnect`, with one optional value:
`values.connect_style = "direct" | "relief" | "none"`. No thermal width,
air gap, entry count or angle setter is introduced. Omitted fields of an
existing rule remain unchanged. New rules require connect_style and an
explicit positive priority; their unspecified properties retain Altium defaults.

A PolygonConnect batch must contain only explicit PolygonConnect rows and no
deletes. It requires the exact PCB already loaded, active and idle. It does
not open, focus or switch a document. Native preflight rejects rule-kind
mismatch, duplicate names, unsupported values and conflicting final priorities.
The existing generic thermal rule must be explicitly assigned its intended
lower priority; no implicit renumbering is requested by this tool.

Example for an existing single generic PolygonConnect at priority 1:

```json
[
  {"name":"PolygonConnect","kind":"PolygonConnect","priority":2},
  {"name":"GND_Via_Direct","kind":"PolygonConnect","priority":1,
   "scope1":"IsVia And InNet('GND')","scope2":"All","enabled":true,
   "values":{"connect_style":"direct"}}
]
```

The response includes actual `connect_style`, scopes and priority, plus
`saved` and `polygon_style_verified`. Style/scope/enabled readback occurs
before save; priorities are checked again after sorting. Errors or timeouts
must be reconciled from native logs and saved files, never blindly replayed.
There is no automatic rollback of an interrupted transaction.

Qualification requires a scratch copy and saved Rules6 comparison: only the
new scoped rule and explicit generic priority change may differ. The generic
thermal parameters and all unrelated data must persist unchanged. A subsequent
native pour must demonstrate direct GND-via contact and retained pad thermals;
a setter/getter match alone does not prove effective object-rule matching.

Sources checked 2026-10-22:

- [Official design-object reference](https://www.altium.com/cn/documentation/altium-dxp-developer/pcb-api-design-objects-interfaces-reference?version=3.0): IPCB_PolygonConnectStyleRule.ConnectStyle uses TPlaneConnectStyle.
- [Official type reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-types-reference): enum values eReliefConnectToPlane, eDirectConnectToPlane, eNoConnect.

These are historical API descriptions, not proof of current DelphiScript
runtime compatibility. Existing rule receivers follow the tool's established
IPCB_Rule property-access pattern. Offline unit tests and static preflight do
not compile or run the script in Altium.
