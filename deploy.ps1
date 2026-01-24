# Create a guaranteed-clean deploy.ps1
$cleanScript = @'
# ============================================
# DEPLOY.PS1 - Clean Working Version
# ============================================

param(
    [ValidateSet("deploy", "rollback", "check")]
    [string]$Action = "check",

    [string]$CommitHash = $null
)

function Show-Header {
    Write-Host "=========================================" -ForegroundColor Cyan
    Write-Host "   RENDER DEPLOYMENT MANAGER   " -ForegroundColor Yellow
    Write-Host "=========================================" -ForegroundColor Cyan
    Write-Host "Current branch: $(git branch --show-current)" -ForegroundColor White
    Write-Host "Current commit: $(git rev-parse --short HEAD)" -ForegroundColor White
    Write-Host ""
}

function Check-DeploymentStatus {
    Write-Host "DEPLOYMENT STATUS" -ForegroundColor Cyan
    Write-Host "==================" -ForegroundColor Cyan

    Write-Host "`nBranch Status:" -ForegroundColor Yellow
    git status -sb

    Write-Host "`nRecent commits on dev:" -ForegroundColor Cyan
    git log dev --oneline -5

    Write-Host "`nRecent commits on main:" -ForegroundColor Cyan
    git log main --oneline -5

    Write-Host "`nChanges ready for deployment:" -ForegroundColor Yellow
    $changes = git log --oneline origin/main..dev
    if ($changes) {
        Write-Host $changes -ForegroundColor White
        Write-Host "`nTotal commits ready: $($changes.Count)" -ForegroundColor Green
    } else {
        Write-Host "No changes pending (main is up to date)" -ForegroundColor Green
    }
}

try {
    Show-Header

    switch ($Action) {
        "check" {
            Check-DeploymentStatus
        }
        "deploy" {
            Write-Host "Deploy function would run here" -ForegroundColor Yellow
        }
        "rollback" {
            if ($CommitHash) {
                Write-Host "Rollback to $CommitHash would run here" -ForegroundColor Yellow
            } else {
                Write-Host "Error: Need commit hash for rollback" -ForegroundColor Red
            }
        }
        default {
            Write-Host "Usage: .\deploy.ps1 [deploy|rollback|check]" -ForegroundColor Yellow
            Write-Host "Examples:" -ForegroundColor Cyan
            Write-Host "  .\deploy.ps1 deploy" -ForegroundColor White
            Write-Host "  .\deploy.ps1 rollback abc123" -ForegroundColor White
            Write-Host "  .\deploy.ps1 check" -ForegroundColor White
        }
    }
} catch {
    Write-Host "ERROR: $_" -ForegroundColor Red
    exit 1
}

Write-Host "`nDone!" -ForegroundColor Cyan
'@

# Save with correct encoding and line endings
$cleanScript -replace "`r?`n", "`r`n" | Out-File deploy-clean.ps1 -Encoding UTF8

Write-Host "✅ Created clean version" -ForegroundColor Green
.\deploy-clean.ps1 check
