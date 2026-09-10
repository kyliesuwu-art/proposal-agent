param(
    [Parameter(Mandatory = $true)][string]$InputPath,
    [Parameter(Mandatory = $true)][string]$OutputDir
)

$input = (Resolve-Path -LiteralPath $InputPath).Path
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$app = $null
$presentation = $null
try {
    $app = New-Object -ComObject PowerPoint.Application
    $app.Visible = $true
    # Open read-only and do not save any change back to the delivered deck.
    $presentation = $app.Presentations.Open($input, $true, $true, $false)
    $presentation.SaveAs((Join-Path (Resolve-Path -LiteralPath $OutputDir).Path 'slide.png'), 18)
    Get-ChildItem -LiteralPath $OutputDir -Filter '*.PNG' | Sort-Object Name | Select-Object -ExpandProperty FullName
}
finally {
    if ($presentation) { $presentation.Close() }
    if ($app) { $app.Quit() }
}
