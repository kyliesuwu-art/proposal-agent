param(
    [Parameter(Mandatory = $true)][string]$InputListPath,
    [Parameter(Mandatory = $true)][string]$OutputPath
)

$ErrorActionPreference = 'Stop'

$app = $null
$presentation = $null
try {
    $app = New-Object -ComObject PowerPoint.Application
    $app.Visible = $true
    $presentation = $app.Presentations.Add($true)
    foreach ($path in Get-Content -LiteralPath $InputListPath) {
        $resolved = (Resolve-Path -LiteralPath $path).Path
        [void]$presentation.Slides.InsertFromFile($resolved, $presentation.Slides.Count, 1, 1)
    }
    $targetDir = (Resolve-Path -LiteralPath (Split-Path -Parent $OutputPath)).Path
    $presentation.SaveAs((Join-Path $targetDir (Split-Path -Leaf $OutputPath)))
}
finally {
    if ($presentation) { $presentation.Close() }
    if ($app) { $app.Quit() }
}
