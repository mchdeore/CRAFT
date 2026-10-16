#!/bin/bash
# Render every docs/spec/images/*.mmd to an SVG beside it. Run after editing a diagram source.
# Usage: docs/tools/diagrams.sh
# Requires Node.js with npm, network access to the npm registry, and Chrome (CHROME selects it).
set -euo pipefail
shopt -s nullglob
T="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(git -C "$T" rev-parse --show-toplevel)"
CHROME="${CHROME:-$(command -v google-chrome chromium | head -1 || true)}"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
printf '{ "executablePath": "%s", "headless": "new" }\n' "$CHROME" > "$WORK/puppeteer.json"
for source in "$ROOT"/docs/spec/images/*.mmd; do
  PUPPETEER_SKIP_DOWNLOAD=1 npm exec --yes \
    --package=@mermaid-js/mermaid-cli@11.12.0 --package=mermaid@11.12.0 -- \
    mmdc -q -i "$source" -o "${source%.mmd}.svg" -c "$T/mermaid.json" -p "$WORK/puppeteer.json" -b white
  echo "rendered ${source#"$ROOT"/}"
done
