[CmdletBinding()]
param(
    [string] $Source = (Join-Path $PSScriptRoot 'PDFs'),
    [string] $CbzSource = (Join-Path $PSScriptRoot 'CBZs'),
    [string] $Output = (Join-Path $PSScriptRoot 'Output'),
    [string] $ComicVineApiKey = $env:COMICVINE_API_KEY,
    [ValidateRange(1, 32)]
    [int] $Jobs = 1,
    [switch] $RefreshMetadata,
    [switch] $Force
)

$arguments = @(
    (Join-Path $PSScriptRoot 'build_cbz.py')
    '--source', $Source
    '--cbz-source', $CbzSource
    '--output', $Output
    '--jobs', $Jobs
)
if ($ComicVineApiKey) { $arguments += @('--api-key', $ComicVineApiKey) }
if ($RefreshMetadata) { $arguments += '--refresh-metadata' }
if ($Force) { $arguments += '--force' }

& python @arguments
exit $LASTEXITCODE
