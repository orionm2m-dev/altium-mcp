param([Parameter(Mandatory=$true)][string]$AltiumSystem,
      [Parameter(Mandatory=$true)][string]$CompilerPath,
      [string]$DotNetHost = 'dotnet')
$ErrorActionPreference = 'Stop'
$output = Join-Path $PSScriptRoot 'build'
New-Item -ItemType Directory -Path $output -Force | Out-Null
$references = @{}
foreach($family in @('Microsoft.NETCore.App','Microsoft.WindowsDesktop.App')) {
    $root=Join-Path $AltiumSystem ('DotNet\Runtime\shared\'+$family)
    $runtime = Get-ChildItem -LiteralPath $root -Directory | Sort-Object { [version]$_.Name } -Descending | Select-Object -First 1
    if(-not $runtime) { throw ('Installed runtime missing: '+$family) }
    foreach($file in Get-ChildItem -LiteralPath $runtime.FullName -Filter '*.dll') {
        $stream=[IO.File]::OpenRead($file.FullName)
        try {
            $pe=[Reflection.PortableExecutable.PEReader]::new($stream)
            try { if($pe.HasMetadata -and -not $references.ContainsKey($file.Name)) { $references[$file.Name]=$file.FullName } }
            finally { $pe.Dispose() }
        } finally { $stream.Dispose() }
    }
}
$arguments = @('/nostdlib+','/target:library','/platform:x64','/warnaserror+','/optimize+',('/out:"'+$output+'\NativeSitusControl.dll"'))
foreach($name in @('Altium.SDK.dll','Altium.SDK.Interfaces.dll','Altium.Edp.Interfaces.dll','Altium.Dxp.Interfaces.dll')) { $arguments += ('/reference:"'+(Join-Path $AltiumSystem $name)+'"') }
$arguments += @($references.Values | Sort-Object | ForEach-Object { '/reference:"'+$_+'"' })
$arguments += ('"'+(Join-Path $PSScriptRoot 'NativeTransport.cs')+'"')
$arguments += ('"'+(Join-Path $PSScriptRoot 'NativeSitusControl.cs')+'"')
$arguments += ('"'+(Join-Path $PSScriptRoot 'SitusSettings.cs')+'"')
$arguments | Set-Content -LiteralPath "$output\compile.rsp" -Encoding utf8
& $DotNetHost $CompilerPath /nologo /noconfig "@$output\compile.rsp"
if ($LASTEXITCODE -ne 0) { throw "Compile failed: $LASTEXITCODE" }
[pscustomobject]@{status='COMPILED_NOT_LOADED'; sha256=(Get-FileHash -LiteralPath "$output\NativeSitusControl.dll").Hash; native_calls_performed=$false; installed=$false} | ConvertTo-Json
