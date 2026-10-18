# test_sheet_search.ps1
# Exercises the four /sheet-search endpoints.

$base = "https://tbl-grocery-scanner.onrender.com"

function Show-Result {
    param($label, $json)
    Write-Host ""
    Write-Host "=== $label ===" -ForegroundColor Cyan
    try {
        $obj = $json | ConvertFrom-Json
        $obj | Format-List
    } catch {
        Write-Host "raw: $json" -ForegroundColor Yellow
    }
}

# --- Shopper: product-form search ---
$r = Invoke-RestMethod -Uri "$base/sheet-search?q=coffees&limit=3"
Write-Host "=== /sheet-search?q=coffees ===" -ForegroundColor Cyan
Write-Host "query: $($r.query)"
Write-Host "source: $($r.source)"
Write-Host "total_matches: $($r.total_matches)"
Write-Host "showing: $($r.showing)"
Write-Host "first brand: $($r.results[0].brand)  score: $($r.results[0].overall_tbl_score)  grade: $($r.results[0].grade)"

Write-Host ""
Write-Host "=== /sheet-search?q=cane sugar ===" -ForegroundColor Cyan
$r = Invoke-RestMethod -Uri "$base/sheet-search?q=cane%20sugar&limit=20"
Write-Host "total_matches: $($r.total_matches)"
$r.results | Select-Object brand, category, overall_tbl_score | Format-Table -AutoSize

Write-Host ""
Write-Host "=== /sheet-search?q=coffee&certs=fair_trade ===" -ForegroundColor Cyan
$r = Invoke-RestMethod -Uri "$base/sheet-search?q=coffee&certs=fair_trade&limit=5"
Write-Host "total_matches: $($r.total_matches)"
$r.results | Select-Object brand, overall_tbl_score, grade | Format-Table -AutoSize

# --- Researcher: taxonomy search ---
Write-Host ""
Write-Host "=== /sheet-search/taxonomy?q=en:dairies ===" -ForegroundColor Cyan
$r = Invoke-RestMethod -Uri "$base/sheet-search/taxonomy?q=en:dairies&limit=5"
Write-Host "source: $($r.source)"
Write-Host "total_matches: $($r.total_matches)"
$r.results | Select-Object brand, category, overall_tbl_score | Format-Table -AutoSize

# --- Autocomplete ---
Write-Host ""
Write-Host "=== /sheet-search/tokens?prefix=cof ===" -ForegroundColor Cyan
$r = Invoke-RestMethod -Uri "$base/sheet-search/tokens?prefix=cof"
$r.tokens | Format-Table -AutoSize

Write-Host ""
Write-Host "=== /sheet-search/tokens (full catalog, top 10) ===" -ForegroundColor Cyan
$r = Invoke-RestMethod -Uri "$base/sheet-search/tokens?limit=10"
$r.tokens | Format-Table -AutoSize

Write-Host ""
Write-Host "=== /sheet-search/taxonomies?prefix=en:d ===" -ForegroundColor Cyan
$r = Invoke-RestMethod -Uri "$base/sheet-search/taxonomies?prefix=en:d"
$r.tokens | Format-Table -AutoSize

# --- Error cases ---
Write-Host ""
Write-Host "=== Error: no q, no certs ===" -ForegroundColor Cyan
try {
    Invoke-RestMethod -Uri "$base/sheet-search" | Out-Null
} catch {
    Write-Host "status: $($_.Exception.Response.StatusCode.value__)"
    Write-Host "detail: $($_.ErrorDetails.Message)"
}

Write-Host ""
Write-Host "=== Error: bogus cert ===" -ForegroundColor Cyan
try {
    Invoke-RestMethod -Uri "$base/sheet-search?certs=bogus" | Out-Null
} catch {
    Write-Host "status: $($_.Exception.Response.StatusCode.value__)"
    Write-Host "detail: $($_.ErrorDetails.Message)"
}

Write-Host ""
Write-Host "=== Nonsense q (should be 200 with 0) ===" -ForegroundColor Cyan
$r = Invoke-RestMethod -Uri "$base/sheet-search?q=zzznope"
Write-Host "total_matches: $($r.total_matches)"
