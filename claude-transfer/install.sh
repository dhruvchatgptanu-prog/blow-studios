#!/usr/bin/env bash
# Sets up Claude Code on this Mac with the plugins and skills
# you use in your claude.ai cloud sessions.
#
# Usage:
#   bash install.sh                 # plugins + public skills
#   bash install.sh ~/Downloads     # also install any skill .zip files found there
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="$HOME/.claude/skills"
ZIP_DIR="${1:-}"

# --- find the claude command -------------------------------------------
if ! command -v claude >/dev/null 2>&1; then
  if [ -x "$HOME/.local/bin/claude" ]; then
    export PATH="$HOME/.local/bin:$PATH"
  else
    echo "Claude Code is not installed. Install it first:"
    echo "  curl -fsSL https://claude.ai/install.sh | bash"
    exit 1
  fi
fi
echo "Using $(command -v claude)"

# --- 1. marketplaces ---------------------------------------------------
echo
echo "== Adding plugin marketplaces"
for m in anthropics/claude-plugins-official anthropics/knowledge-work-plugins anthropics/skills; do
  claude plugin marketplace add "$m" >/dev/null 2>&1 && echo "  + $m" || echo "  = $m (already added or unavailable)"
done
claude plugin marketplace update >/dev/null 2>&1 || true

# --- 2. plugins --------------------------------------------------------
echo
echo "== Installing plugins"
ok=(); failed=()
while IFS='|' read -r name _; do
  [[ -z "$name" || "$name" == \#* ]] && continue
  if claude plugin install "$name" >/dev/null 2>&1; then
    ok+=("$name"); echo "  + $name"
  else
    failed+=("$name"); echo "  x $name"
  fi
done < "$HERE/plugins.txt"

# --- 3. your own skills (optional) -------------------------------------
if [ -n "$ZIP_DIR" ]; then
  echo
  echo "== Installing skill zips from $ZIP_DIR"
  mkdir -p "$SKILLS_DIR"
  shopt -s nullglob
  for z in "$ZIP_DIR"/*.zip; do
    tmp="$(mktemp -d)"
    unzip -q "$z" -d "$tmp"
    skill_md="$(find "$tmp" -name SKILL.md -maxdepth 3 | head -1)"
    if [ -z "$skill_md" ]; then rm -rf "$tmp"; continue; fi
    src="$(dirname "$skill_md")"
    name="$(sed -n 's/^name:[[:space:]]*//p' "$skill_md" | head -1)"
    name="${name:-$(basename "$z" .zip)}"
    if [ -e "$SKILLS_DIR/$name" ]; then
      mv "$SKILLS_DIR/$name" "$SKILLS_DIR/$name.backup-$(date +%Y%m%d-%H%M%S)"
    fi
    cp -R "$src" "$SKILLS_DIR/$name"
    echo "  + $name"
    rm -rf "$tmp"
  done
fi

# --- summary -----------------------------------------------------------
echo
echo "Installed ${#ok[@]} plugins. ${#failed[@]} were not found in the local marketplaces."
if [ ${#failed[@]} -gt 0 ]; then
  echo "Inside Claude Code, run /plugin, open Discover, and search for these by name:"
  printf '  - %s\n' "${failed[@]}"
fi
echo
echo "Done. Start Claude Code and type /plugin and /skills to check."
echo "Plugins that connect to outside services ask you to sign in on first use."
