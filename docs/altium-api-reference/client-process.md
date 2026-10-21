# Client documents and process dispatch

Retrieved 2026-10-21. Status: **DOCUMENTED**, runtime unverified.

The client API separates a document from its view. Current view access does not
establish that a different opened PCB is the intended target. Resolve and compare
actual document identity before mutations.

Process parameters persist in the shared buffer after execution. Clear it with
`ResetParameters`, add the required values, then dispatch the named process.
`RunProcess` has no return value in its declaration; a returned tool response is
not proof of native operation completion. The documented `Client.SendMessage`
redraw example supplies one call form, not the entire command parameter catalog.
[Official System API](https://www.altium.com/documentation/altium-dxp-developer/system-api).

The same source states that scripting exposes a subset of SDK interfaces. An
SDK declaration must therefore remain DOCUMENTED until the actual installed
binding and behavior are qualified. In particular, UI, save, close, routing and
DRC commands need their own completion/readback checks.

## IClient

Source: [System API](https://www.altium.com/documentation/altium-dxp-developer/system-api); section `IClient`.

```pascal
Procedure CloseDocument(ADocument : IServerDocument);
Property CurrentView : IServerDocumentView Read GetCurrentView Write SetCurrentView;
Function GetCurrentView : IServerDocumentView;
Function GetDocumentByPath(Const AFilePath : WideString) : IServerDocument;
Function OpenDocument (Const AKind, AFileName : PChar) : IServerDocument;
Procedure ShowDocument (ADocument : IServerDocument);
```

Documented example only: `Client.SendMessage('PCB:Zoom', 'Action=Redraw', 255, Client.CurrentView);`

`SendMessage`: The source example establishes this call form only, not a complete method declaration or all accepted process parameters.

## IServerDocumentView

Source: [System API](https://www.altium.com/documentation/altium-dxp-developer/system-api); section `IServerDocumentView`.

```pascal
Function GetOwnerDocument : IServerDocument;
```

## IServerDocument

Source: [System API](https://www.altium.com/documentation/altium-dxp-developer/system-api); section `IServerDocument`.

```pascal
Property FileName : Widestring Read GetFileName;
```

## Process functions

```pascal
Procedure AddIntegerParameter(Const Name: String; Value: Integer);
Procedure AddStringParameter(Const Name, Value: String);
Procedure ResetParameters;
Procedure RunProcess(Const Command: String);
```

Original illustration of parameter isolation; not executed:

```pascal
ResetParameters;
AddStringParameter('Action', 'Redraw');
RunProcess('PCB:Zoom');
```
