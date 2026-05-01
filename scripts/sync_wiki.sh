#!/usr/bin/env bash
#
# AIM Wiki Sync Script
# 
# This script is intended to synchronize the docs/wiki directory from the main repo
# into the separate GitHub Wiki repository.
#
# Usage:
#   ./scripts/sync_wiki.sh
#

set -e

# The URL for your GitHub Wiki repo (e.g. git@github.com:SpicyDaemon/OpsMender-AI.wiki.git)
# Ensure this is set via environment variable or hardcoded below.
WIKI_REPO_URL=${WIKI_REPO_URL:-"git@github.com:SpicyDaemon/OpsMender-AI.wiki.git"}

TEMP_DIR=$(mktemp -d)
echo "Cloning wiki repository..."
git clone "$WIKI_REPO_URL" "$TEMP_DIR"

echo "Syncing markdown files..."
# Copy all markdown files from docs/wiki/ into the root of the wiki repo.
# Note: GitHub wiki serves files from the root of the wiki repo.
cp -R docs/wiki/*.md "$TEMP_DIR/"

cd "$TEMP_DIR"

if [ -n "$(git status --porcelain)" ]; then
    echo "Changes detected. Committing to wiki..."
    git add .
    git commit -m "Auto-sync from main repo: docs/wiki/"
    git push origin master
    echo "Wiki updated successfully."
else
    echo "No changes detected. Wiki is up to date."
fi

# Cleanup
rm -rf "$TEMP_DIR"
