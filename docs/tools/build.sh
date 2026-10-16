#!/bin/bash
# Build document downloads and index.html.
# Usage: docs/tools/build.sh [output folder, default docs/build]
# Requires Git, Pandoc 3.11, Poppler and Chrome or Chromium.
set -euo pipefail
shopt -s nullglob
T="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(git -C "$T" rev-parse --show-toplevel)"
OUT="${1:-$ROOT/docs/build}"
mkdir -p "$OUT" && OUT="$(cd "$OUT" && pwd)"
CHROME="${CHROME:-$(command -v google-chrome chromium | head -1 || true)}"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
rm -f "$OUT"/CRAFT-*.pdf "$OUT/index.html"

version() {  # version <path>: "<date>|<commit>" of its last change, or "<today>|draft"
  if [[ -n "$(git -C "$ROOT" status --porcelain -- "$1")" ]]; then
    LC_ALL=C date '+%-d %B %Y|draft'
  else
    LC_ALL=C git -C "$ROOT" log -1 --format='%cd|%h' --date='format:%-d %B %Y' -- "$1"
  fi
}

pdf() {  # pdf <prepared.md> <label> <out.pdf>
  local head="$WORK/head.html" html="$WORK/page.html"
  { echo "<style>"; cat "$T/print.css"
    echo "@page { @bottom-left { content: \"CRAFT  $2\";"
    echo "  font: 7.5pt Charter, \"Charis SIL\", Charis, Georgia, serif; color: #555; } }"
    echo "</style>"; } > "$head"
  pandoc -f gfm -t html5 -s --columns=10000 --syntax-highlighting=none \
    --metadata pagetitle="CRAFT $2" -H "$head" "$1" -o "$html"
  # The keychain and first-run flags keep Chrome from waiting on a prompt on a macOS CI runner;
  # perl's alarm stops a Chrome that hangs anyway, and its log is shown.
  perl -e 'alarm shift; exec @ARGV' 120 "$CHROME" --headless=new --disable-gpu --no-pdf-header-footer \
    --user-data-dir="$WORK/chrome" --use-mock-keychain --password-store=basic --no-first-run \
    --no-default-browser-check --print-to-pdf="$3" "file://$html" 2> "$WORK/chrome.log" \
    || { tail -40 "$WORK/chrome.log" >&2; echo "Chrome failed on $2" >&2; exit 1; }
}

build() {  # build <source path> <name> <footer label> [section number]
  local out="$OUT/CRAFT-$2.pdf" date commit
  IFS='|' read -r date commit <<< "$(version "$1")"
  [[ -n "$commit" ]] || { echo "$1: not in git" >&2; exit 1; }
  if [[ -n "${4:-}" ]]; then
    python3 "$T/prepare.py" "$ROOT/$1" "$WORK/doc.md" "$date" --section "$4"
  else
    python3 "$T/prepare.py" "$ROOT/$1" "$WORK/doc.md" "$date"
  fi
  pdf "$WORK/doc.md" "$3" "$out"
  if [[ "$1" == docs/spec ]]; then  # second pass: add section page numbers to the index
    python3 "$T/prepare.py" "$ROOT/$1" "$WORK/doc.md" "$date" "$out"
    pdf "$WORK/doc.md" "$3" "$out"
  fi
  local pages; pages="$(pdfinfo "$out" | awk '/^Pages/{print $2}')"
  printf '%s\t%s\t%s\t%s\n' "CRAFT-$2.pdf" "$pages" "$date" "$1" >> "$WORK/manifest.tsv"
  printf '%-44s %3s pages  %s, %s\n' "CRAFT-$2.pdf" "$pages" "$date" "$commit"
}

build docs/spec Specification "Specification"
for source in "$ROOT"/docs/spec/*.md; do
  section_metadata="$(python3 - "$source" <<'PY'
import re
import sys
from pathlib import Path
p = Path(sys.argv[1])
m = re.fullmatch(r"# (\d+)\. (.+)", p.read_text().splitlines()[0])
if not m:
    raise SystemExit(f"{p}: invalid section title")
joining = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with"}
words = re.findall(r"[A-Za-z0-9]+", m[2])
slug = "-".join(word.lower() if i and word.lower() in joining else word.capitalize()
                for i, word in enumerate(words))
print(f"{int(m[1])}\t{int(m[1]):02}-{slug}\t{m[1]} {m[2]}")
PY
  )"
  IFS=$'\t' read -r number name label <<< "$section_metadata"
  build "${source#"$ROOT"/}" "$name" "$label" "$number"
done
build docs/API-Contract.md API-Contract "API Contract"
build docs/Dependencies-and-Integration.md Dependencies-and-Integration "Dependencies and Integration"

python3 "$T/links.py" "$ROOT/docs/index.md" "$WORK/manifest.tsv" "$WORK/board.md" "$(git -C "$ROOT" rev-parse HEAD)"
{ echo '<meta name="viewport" content="width=device-width, initial-scale=1">'
  echo "<style>"; cat "$T/site.css"; echo "</style>"; } > "$WORK/site-head.html"
pandoc -f gfm -t html5 -s --columns=10000 -M document-css=false --metadata pagetitle="CRAFT Documents" \
  -H "$WORK/site-head.html" "$WORK/board.md" -o "$OUT/index.html"
