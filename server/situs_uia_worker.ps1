# Bounded, exact-window UI Automation worker. Input and output are JSON over pipes.
# No foreground activation, screen coordinates, keystrokes, scripts or retries.
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$attempted = $false
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $q = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $walker = [System.Windows.Automation.TreeWalker]::RawViewWalker
    function Runtime-Key($element) { return (($element.GetRuntimeId()) -join ',') }
    function Assert-Session {
        $process = [Diagnostics.Process]::GetProcessById([int]$q.identity.pid)
        if ($process.HasExited -or $process.StartTime.ToUniversalTime().ToFileTimeUtc() -ne [long]$q.identity.creation_filetime -or
            -not [String]::Equals($process.MainModule.FileName, [string]$q.identity.exe, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Pinned process identity changed'
        }
        $rootNow = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr][long]$q.dialog.hwnd)
        $mainNow = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr][long]$q.main.hwnd)
        if ($rootNow.Current.ProcessId -ne $q.identity.pid -or $rootNow.Current.NativeWindowHandle -ne $q.dialog.hwnd -or
            $rootNow.Current.Name -cne $q.dialog.title -or $rootNow.Current.ClassName -cne $q.dialog.class -or
            -not $rootNow.Current.IsEnabled -or $rootNow.Current.IsOffscreen -or
            $mainNow.Current.ProcessId -ne $q.identity.pid -or $mainNow.Current.Name -cne $q.main.title -or
            $mainNow.Current.ClassName -cne 'TDocumentForm') { throw 'Exact dialog or main-window identity changed' }
        return $rootNow
    }
    function Read-Element($element, $parentKey) {
        $c = $element.Current
        $patterns = @($element.GetSupportedPatterns() | ForEach-Object { $_.ProgrammaticName })
        $label = ''
        if ($null -ne $c.LabeledBy) { $label = $c.LabeledBy.Current.Name }
        $row = [ordered]@{ runtime_id=@($element.GetRuntimeId()); parent_runtime_id=$parentKey;
            hwnd=[long]$c.NativeWindowHandle; pid=$c.ProcessId; name=$c.Name; class=$c.ClassName;
            automation_id=$c.AutomationId; control_type=$c.ControlType.ProgrammaticName;
            enabled=$c.IsEnabled; offscreen=$c.IsOffscreen; password=$c.IsPassword; label=$label;
            patterns=$patterns; states=[ordered]@{} }
        if ($c.IsPassword) { return $row }
        if ($patterns -contains 'TogglePatternIdentifiers.Pattern') {
            $row.states.toggle = [int]($element.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Current.ToggleState)
        }
        if ($patterns -contains 'ValuePatternIdentifiers.Pattern') {
            $value = $element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current
            $row.states.value = $value.Value
            $row.states.value_readonly = $value.IsReadOnly
        }
        if ($patterns -contains 'RangeValuePatternIdentifiers.Pattern') {
            $range = $element.GetCurrentPattern([System.Windows.Automation.RangeValuePattern]::Pattern).Current
            $row.states.range = [ordered]@{value=$range.Value; minimum=$range.Minimum; maximum=$range.Maximum;
                readonly=$range.IsReadOnly; small_change=$range.SmallChange; large_change=$range.LargeChange}
        }
        if ($patterns -contains 'SelectionItemPatternIdentifiers.Pattern') {
            $selection = $element.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Current
            $row.states.selected = $selection.IsSelected
            $row.states.selection_container = Runtime-Key $selection.SelectionContainer
        }
        if ($patterns -contains 'ExpandCollapsePatternIdentifiers.Pattern') {
            $row.states.expand = [int]($element.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern).Current.ExpandCollapseState)
        }
        if ($patterns -contains 'GridItemPatternIdentifiers.Pattern') {
            $grid = $element.GetCurrentPattern([System.Windows.Automation.GridItemPattern]::Pattern).Current
            $row.states.grid = [ordered]@{row=$grid.Row; column=$grid.Column; row_span=$grid.RowSpan; column_span=$grid.ColumnSpan}
        }
        if ($patterns -contains 'TextPatternIdentifiers.Pattern' -and $row.control_type -eq 'ControlType.Document') {
            $limit = [int]$q.text_limit
            $text = $element.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern).DocumentRange.GetText($limit + 1)
            $row.states.text_truncated = $text.Length -gt $limit
            $row.states.text = $text.Substring(0, [Math]::Min($text.Length, $limit))
        }
        return $row
    }
    function Read-Snapshot {
        $root = Assert-Session
        $rows = [Collections.Generic.List[object]]::new()
        $elements = @{}
        $queue = [Collections.Generic.Queue[object]]::new()
        $queue.Enqueue(@($root, '', 0))
        $complete = $true
        while ($queue.Count -gt 0) {
            $item = $queue.Dequeue()
            $element = $item[0]; $parent = $item[1]; $depth = [int]$item[2]
            if ($rows.Count -ge 512 -or $depth -gt 24) {
                if ($q.operation -eq 'inspect' -and $q.allow_partial) { $complete = $false; break }
                throw 'Accessibility tree limit exceeded; no partial snapshot accepted'
            }
            $row = Read-Element $element $parent
            $key = $row.runtime_id -join ','
            if ($elements.ContainsKey($key)) { throw 'Duplicate accessibility identity' }
            $elements[$key] = $element
            $rows.Add($row)
            # Foreign renderer descendants are read-only and explicitly identified.
            $child = $walker.GetFirstChild($element)
            while ($null -ne $child) {
                if (($rows.Count + $queue.Count) -ge 512) {
                    if ($q.operation -eq 'inspect' -and $q.allow_partial) { $complete = $false; break }
                    throw 'Accessibility queue limit exceeded; no partial snapshot accepted'
                }
                $queue.Enqueue(@($child, $key, ($depth + 1)))
                $child = $walker.GetNextSibling($child)
            }
        }
        $null = Assert-Session
        return @{nodes=@($rows.ToArray()); elements=$elements; complete=$complete}
    }
    function Canonical($value) {
        if ($null -eq $value) { return 'null' }
        if ($value -is [string] -or $value -is [ValueType]) { return (ConvertTo-Json -InputObject $value -Compress) }
        if ($value -is [Collections.IDictionary]) {
            $parts = @($value.Keys | Sort-Object | ForEach-Object { (Canonical ([string]$_)) + ':' + (Canonical $value[$_]) })
            return '{' + ($parts -join ',') + '}'
        }
        if ($value -is [pscustomobject]) {
            $parts = @($value.PSObject.Properties.Name | Sort-Object | ForEach-Object { (Canonical ([string]$_)) + ':' + (Canonical $value.$_) })
            return '{' + ($parts -join ',') + '}'
        }
        if ($value -is [Collections.IEnumerable]) {
            $parts = @($value | ForEach-Object { Canonical $_ })
            return '[' + ($parts -join ',') + ']'
        }
        throw 'Unsupported snapshot value'
    }
    $before = Read-Snapshot
    if ($q.operation -eq 'inspect') {
        @{success=$true; input_attempted=$false; nodes=$before.nodes; complete=$before.complete} | ConvertTo-Json -Depth 25 -Compress
        exit 0
    }
    if ($q.operation -notin @('toggle','value','range','select','expand','invoke')) { throw 'Unsupported worker operation' }
    if ((Canonical $before.nodes) -cne (Canonical $q.expected_nodes)) { throw 'Accessibility snapshot changed before action; no input' }
    $key = $q.runtime_id -join ','
    if (-not $before.elements.ContainsKey($key)) { throw 'Observed accessibility element no longer exists' }
    $element = $before.elements[$key]
    $row = @($before.nodes | Where-Object { ($_.runtime_id -join ',') -ceq $key })
    if ($row.Count -ne 1 -or $row[0].pid -ne $q.identity.pid -or -not $row[0].enabled -or $row[0].offscreen -or $row[0].password) {
        throw 'Mutation requires one enabled visible same-process non-password element'
    }
    $null = Assert-Session
    # Obtain the requested pattern before declaring any input attempt.
    switch ($q.operation) {
        'toggle' { $pattern = $element.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern) }
        'value' { $pattern = $element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern) }
        'range' { $pattern = $element.GetCurrentPattern([System.Windows.Automation.RangeValuePattern]::Pattern) }
        'select' { $pattern = $element.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern) }
        'expand' { $pattern = $element.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern) }
        'invoke' { $pattern = $element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern) }
    }
    # Re-read the exact element immediately before its single operation.
    if ((Canonical (Read-Element $element $row[0].parent_runtime_id)) -cne (Canonical $row[0])) { throw 'Element changed immediately before action' }
    $null = Assert-Session
    $attempted = $true
    [Console]::Out.WriteLine('{"event":"input_attempted"}')
    [Console]::Out.Flush()
    switch ($q.operation) {
        'toggle' { $pattern.Toggle() }
        'value' { $pattern.SetValue([string]$q.value) }
        'range' { $pattern.SetValue([double]$q.value) }
        'select' { $pattern.Select() }
        'expand' { $pattern.Expand() }
        'invoke' { $pattern.Invoke() }
    }
    $after = $null; $readbackError = $null
    try { $after = Read-Snapshot } catch { $readbackError = $_.Exception.Message }
    @{success=$true; input_attempted=$true; operation_returned=$true; nodes=$(if ($after) {$after.nodes} else {@()});
        complete=($null -ne $after); readback_error=$readbackError} | ConvertTo-Json -Depth 25 -Compress
} catch {
    @{success=$false; input_attempted=$attempted; error=$_.Exception.Message; retry_performed=$false} | ConvertTo-Json -Depth 8 -Compress
    exit 1
}


