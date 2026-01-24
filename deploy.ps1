# ============================================
# DEPLOY.PS1 - Render Deployment Manager
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

function Deploy-ToRender {
    Write-Host "DEPLOYMENT MODE" -ForegroundColor Cyan
    Write-Host "================" -ForegroundColor Cyan

    # 1. Update dev branch
    Write-Host "`n[1/5] Updating dev branch..." -ForegroundColor Yellow
    git checkout dev
    git pull origin dev
    Write-Host "OK - Dev branch updated" -ForegroundColor Green

    # 2. Show changes
    Write-Host "`n[2/5] Checking changes..." -ForegroundColor Yellow
    $changes = git log --oneline origin/main..dev
    if (-not $changes) {
        Write-Host "WARNING - No changes to deploy!" -ForegroundColor Red
        Write-Host "Current main is already up to date with dev." -ForegroundColor Yellow
        return
    }

    Write-Host "`nChanges to deploy:" -ForegroundColor Cyan
    Write-Host $changes -ForegroundColor White
    Write-Host "`nTotal commits: $($changes.Count)" -ForegroundColor Yellow

    # 3. Save current main commit
    $currentMainCommit = git rev-parse --short origin/main
    Write-Host "`nCurrent main commit: $currentMainCommit" -ForegroundColor Gray

    # 4. Ask for confirmation
    Write-Host "`n[3/5] Confirmation" -ForegroundColor Yellow
    $confirmation = Read-Host "Proceed with deployment to Render? (yes/no)"
    if ($confirmation -ne 'yes') {
        Write-Host "ERROR - Deployment cancelled" -ForegroundColor Red
        return
    }

    # 5. Deploy to main
    Write-Host "`n[4/5] Merging dev -> main..." -ForegroundColor Yellow
    git checkout main
    git pull origin main

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm"
    $mergeMessage = "Deploy to Render: $timestamp`n`nChanges:`n$($changes -join "`n")"

    git merge dev --no-ff -m $mergeMessage

    $newCommitHash = git rev-parse --short HEAD
    Write-Host "OK - New main commit: $newCommitHash" -ForegroundColor Green

    # 6. Push to main
    Write-Host "`n[5/5] Pushing to main..." -ForegroundColor Yellow
    git push origin main
    Write-Host "OK - Push complete" -ForegroundColor Green

    # 7. Return to dev
    git checkout dev

    # 8. Success message
    Write-Host "`n" -NoNewline
    Write-Host "="*50 -ForegroundColor Green
    Write-Host "SUCCESS - DEPLOYMENT COMPLETE!" -ForegroundColor Green
    Write-Host "="*50 -ForegroundColor Green
    Write-Host "`nSummary:" -ForegroundColor Cyan
    Write-Host "- Previous: $currentMainCommit" -ForegroundColor White
    Write-Host "- New:      $newCommitHash" -ForegroundColor White
    Write-Host "- Changes:  $($changes.Count) commits" -ForegroundColor White
    Write-Host "`nRender will auto-deploy within 1-2 minutes." -ForegroundColor Yellow
    Write-Host "Dashboard: https://dashboard.render.com/web/tbl-grocery-scanner" -ForegroundColor Cyan
}

function Rollback-FromRender {
    Write-Host "ROLLBACK MODE" -ForegroundColor Magenta
    Write-Host "==============" -ForegroundColor Magenta

    if (-not $CommitHash) {
        Write-Host "ERROR - Please specify a commit hash to rollback to" -ForegroundColor Red
        Write-Host "Usage: .\deploy.ps1 rollback COMMIT-HASH" -ForegroundColor Yellow
        Write-Host "`nRecent commits on main:" -ForegroundColor Cyan
        git log main --oneline -10
        return
    }

    Write-Host "`nRollback to commit: $CommitHash" -ForegroundColor Yellow
    Write-Host "Feature coming soon! For now, manually run:" -ForegroundColor White
    Write-Host "  git checkout main" -ForegroundColor Gray
    Write-Host "  git pull origin main" -ForegroundColor Gray
    Write-Host "  git reset --hard $CommitHash" -ForegroundColor Gray
    Write-Host "  git push origin main --force" -ForegroundColor Gray
}

# ============================================
# MAIN EXECUTION
# ============================================

try {
    Show-Header

    switch ($Action) {
        "check" {
            Check-DeploymentStatus
        }
        "deploy" {
            Deploy-ToRender
        }
        "rollback" {
            Rollback-FromRender
        }
        default {
            Write-Host "Usage: .\deploy.ps1 [deploy|rollback|check]" -ForegroundColor Yellow
            Write-Host "`nExamples:" -ForegroundColor Cyan
            Write-Host "  .\deploy.ps1 deploy           # Deploy dev -> main" -ForegroundColor White
            Write-Host "  .\deploy.ps1 rollback abc123  # Rollback to commit abc123" -ForegroundColor White
            Write-Host "  .\deploy.ps1 check            # Check deployment status" -ForegroundColor White
        }
    }
} catch {
    Write-Host "ERROR: $_" -ForegroundColor Red
    exit 1
}

Write-Host "`nDone!" -ForegroundColor Cyan
