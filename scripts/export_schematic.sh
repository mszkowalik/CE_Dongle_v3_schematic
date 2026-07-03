#!/usr/bin/env bash
# Export KiCad schematic data for analysis.
# Outputs go to exports/schematic/ relative to the repo root.

set -euo pipefail

KICAD_CLI="/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO_ROOT/src"
SCH="$SRC/CE_Dongle_V3.kicad_sch"
OUT="$REPO_ROOT/exports/schematic"

if [[ ! -f "$KICAD_CLI" ]]; then
    echo "ERROR: kicad-cli not found at $KICAD_CLI" >&2
    exit 1
fi

if [[ ! -f "$SCH" ]]; then
    echo "ERROR: schematic not found at $SCH" >&2
    exit 1
fi

mkdir -p "$OUT/netlist"
mkdir -p "$OUT/bom"
mkdir -p "$OUT/svg"

echo "==> Exporting KiCad netlist..."
"$KICAD_CLI" sch export netlist \
    --format kicadxml \
    --output "$OUT/netlist/netlist.xml" \
    "$SCH"

echo "==> Exporting BOM (CSV)..."
"$KICAD_CLI" sch export bom \
    --output "$OUT/bom/bom.csv" \
    "$SCH"

echo "==> Exporting schematic sheets to SVG..."
"$KICAD_CLI" sch export svg \
    --output "$OUT/svg" \
    "$SCH"

echo ""
echo "Exports written to: $OUT"
echo ""
echo "Contents:"
find "$OUT" -type f | sort
