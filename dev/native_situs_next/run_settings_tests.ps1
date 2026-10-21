param([Parameter(Mandatory=$true)][string]$AltiumSystem,
      [Parameter(Mandatory=$true)][string]$CompilerPath,
      [string]$DotNetHost = 'dotnet')
$ErrorActionPreference = 'Stop'
$output = Join-Path $PSScriptRoot 'settings_test_build'
New-Item -ItemType Directory -Path $output -Force | Out-Null
$references = @{}
$runtime = $null
foreach($family in @('Microsoft.NETCore.App','Microsoft.WindowsDesktop.App')) {
    $runtimeRoot = Join-Path $AltiumSystem ('DotNet\Runtime\shared\'+$family)
    $selectedRuntime = Get-ChildItem -LiteralPath $runtimeRoot -Directory | Sort-Object { [version]$_.Name } -Descending | Select-Object -First 1
    if(-not $selectedRuntime) { throw ('Installed runtime missing: '+$family) }
    if($family -eq 'Microsoft.NETCore.App') { $runtime = $selectedRuntime.Name }
    foreach($file in Get-ChildItem -LiteralPath $selectedRuntime.FullName -Filter '*.dll') {
        $stream = [IO.File]::OpenRead($file.FullName)
        try {
            $pe = [Reflection.PortableExecutable.PEReader]::new($stream)
            try { if($pe.HasMetadata -and -not $references.ContainsKey($file.Name)) { $references[$file.Name]=$file.FullName } }
            finally { $pe.Dispose() }
        } finally { $stream.Dispose() }
    }
}
$assembly = Join-Path $output 'SitusSettingsTests.dll'
$arguments = @('/nostdlib+','/target:exe','/platform:x64','/warnaserror+','/optimize+',('/out:"'+$assembly+'"'))
foreach($name in @('Altium.SDK.dll','Altium.SDK.Interfaces.dll','Altium.Edp.Interfaces.dll','Altium.Dxp.Interfaces.dll')) {
    $arguments += ('/reference:"'+(Join-Path $AltiumSystem $name)+'"')
}
$arguments += @($references.Values | Sort-Object | ForEach-Object { '/reference:"'+$_+'"' })
$arguments += ('"'+(Join-Path $PSScriptRoot 'SitusSettings.cs')+'"')
$arguments += ('"'+(Join-Path $PSScriptRoot 'tests\SitusSettingsTests.cs')+'"')
$responseFile = Join-Path $output 'tests.rsp'
[IO.File]::WriteAllLines($responseFile,$arguments)
& $DotNetHost $CompilerPath /nologo /noconfig ('@'+$responseFile)
if($LASTEXITCODE -ne 0) { throw "Offline compile failed: $LASTEXITCODE" }
$config = @{ runtimeOptions = @{ tfm='net8.0'; framework=@{name='Microsoft.NETCore.App';version=$runtime} } } | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText((Join-Path $output 'SitusSettingsTests.runtimeconfig.json'),$config)
# The test entry point calls only fake surfaces; it never constructs native objects.
& $DotNetHost $assembly $AltiumSystem
if($LASTEXITCODE -ne 0) { throw "Offline tests failed: $LASTEXITCODE" }
