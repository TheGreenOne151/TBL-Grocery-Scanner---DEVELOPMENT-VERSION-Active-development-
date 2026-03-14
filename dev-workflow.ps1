param(
    [string]$Action,
    [string]$Name
)

function Activate-Venv {
    Write-Host "Activating virtual environment..."
    if (Test-Path .\venv\Scripts\activate) {
        & .\venv\Scripts\activate
    } else {
        Write-Host "❌ Virtual environment not found. Run 'python -m venv venv' first."
        exit 1
    }
}

function Start-Experiment {
    if (-not $Name) {
        Write-Host "❌ Provide a branch name: -Name feature-something"
        return
    }
    git checkout maintainable-baseline
    git pull
    git checkout -b $Name
    Activate-Venv
    Write-Host "🚀 Starting app locally..."
    python elegant_app.py
}

function Commit-Work {
    if (-not $Name) {
        Write-Host "❌ Provide a commit message: -Name 'Your commit message'"
        return
    }
    git add -A
    git commit -m $Name
    git push -u origin HEAD
    Write-Host "✅ Committed and pushed: $Name"
}

function Merge-To-Baseline {
    if (-not $Name) {
        Write-Host "❌ Provide branch name to merge: -Name feature-name"
        return
    }
    git checkout maintainable-baseline
    git pull
    git merge $Name --no-ff  # --no-ff preserves feature branch history
    git push
    Write-Host "✅ Merged $Name into maintainable-baseline"
}

function Tag-Release {
    if (-not $Name) {
        $Name = "release-" + (Get-Date -Format "yyyy-MM-dd-HHmm")
        Write-Host "Using auto-generated tag: $Name"
    }
    git tag -a $Name -m "Release $Name"
    git push stable $Name
    Write-Host "✅ Production release tagged: $Name"
}

function Restore-Stable {
    Write-Host "⚠️  RESTORING TO STABLE ROLLBACK - This will overwrite history!"
    $confirmation = Read-Host "Are you sure? (y/N)"
    if ($confirmation -eq 'y') {
        git checkout maintainable-baseline
        git reset --hard stable-google-lens-rollback
        git push origin maintainable-baseline --force
        git push stable maintainable-baseline:main --force
        Write-Host "✅ Production and baseline restored to stable rollback."
    } else {
        Write-Host "Restore cancelled."
    }
}

function Publish-To-Production {
    Write-Host "📦 Publishing maintainable-baseline to production repo..."
    git checkout maintainable-baseline
    git pull  # Ensure latest
    git push stable maintainable-baseline:main --force
    Write-Host "✅ Production updated. Render will auto-deploy in 2-3 minutes."
}

function Show-Status {
    git status -sb
    Write-Host ""
    Write-Host "Current branch: $(git branch --show-current)"
}

switch ($Action) {
    "start"   { Start-Experiment }
    "commit"  { Commit-Work }
    "merge"   { Merge-To-Baseline }
    "release" { Tag-Release }
    "restore" { Restore-Stable }
    "publish" { Publish-To-Production }
    "status"  { Show-Status }
    default {
        Write-Host ""
        Write-Host "🚀 TBL Grocery Scanner Dev Workflow"
        Write-Host "════════════════════════════════════"
        Write-Host "Usage:"
        Write-Host "  .\dev-workflow.ps1 start -Name feature-name    # New feature branch"
        Write-Host "  .\dev-workflow.ps1 commit -Name 'message'      # Commit changes"
        Write-Host "  .\dev-workflow.ps1 merge -Name feature-name    # Merge to baseline"
        Write-Host "  .\dev-workflow.ps1 publish                     # Deploy to Render"
        Write-Host "  .\dev-workflow.ps1 release -Name v2.4.0        # Tag release"
        Write-Host "  .\dev-workflow.ps1 status                       # Show git status"
        Write-Host "  .\dev-workflow.ps1 restore                      # Emergency rollback"
        Write-Host ""
        Write-Host "Current Status:"
        Show-Status
    }
}
