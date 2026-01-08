#!/bin/bash

# Script: Push_to_GitHub.sh
# Description: Safely push changes to GitHub repository
# Repository: https://github.com/TheGreenOne151/TBL_Grocery_Scanner/tree/main

echo "=== Git Push Script for TBL_Grocery_Scanner ==="
echo "Repository: TheGreenOne151/TBL_Grocery_Scanner"
echo ""

# Check if we're in a git repository
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo "❌ Error: Not in a git repository!"
    echo "Please run this script from your project directory."
    exit 1
fi

# Show current status
echo "📊 Current git status:"
git status --short

# Show what will be added
echo ""
echo "📁 Files to be added:"
git diff --name-only --cached

# Get list of modified/untracked files
UNTRACKED_FILES=$(git ls-files --others --exclude-standard)
MODIFIED_FILES=$(git diff --name-only)

if [ -z "$UNTRACKED_FILES" ] && [ -z "$MODIFIED_FILES" ] && [ -z "$(git diff --name-only --cached)" ]; then
    echo ""
    echo "✅ No changes to commit. Everything is up to date!"
    exit 0
fi

echo ""
read -p "❓ Do you want to proceed with adding all changes? (y/n): " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "⏹️ Operation cancelled."
    exit 0
fi

# Add all changes
echo "📦 Adding all changes..."
git add .

# Show what was added
echo "✅ Added files:"
git diff --name-only --cached

# Commit with message
COMMIT_MSG="Ready for Render"
echo ""
echo "💾 Committing with message: \"$COMMIT_MSG\""
git commit -m "$COMMIT_MSG"

# Show commit details
echo ""
echo "📝 Latest commit:"
git log --oneline -1

# Push to GitHub
echo ""
echo "🚀 Pushing to GitHub..."
git push

# Check if push was successful
if [ $? -eq 0 ]; then
    echo ""
    echo "🎉 Successfully pushed to GitHub!"
    echo "🌐 View your repository at: https://github.com/TheGreenOne151/TBL_Grocery_Scanner"
else
    echo ""
    echo "❌ Push failed. Please check your git configuration and network connection."
    echo "You may need to set upstream branch: git push -u origin main"
fi

# Optional: Show remote URL
echo ""
echo "🔗 Remote repository:"
git remote -v
