#!/usr/bin/env bash
# Rebuilds public/fonts/inter.woff2 from the full Inter 4 variable font (SIL OFL 1.1).
# Usage: scripts/subset-inter.sh [path/to/Inter.ttf]   (needs fonttools and brotli)
set -euo pipefail
app_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_font="${1:-$app_root/fonts-src/inter.ttf}"
target="$app_root/public/fonts/inter.woff2"
pyftsubset "$source_font" \
  --unicodes='U+0000-024F,U+0300-036F,U+0400-04FF,U+2000-206F,U+20A0-20CF,U+2100-214F,U+2190-21FF,U+2200-22FF,U+25A0-25FF,U+FEFF,U+FFFD' \
  --layout-features='kern,liga,calt,ccmp,locl,mark,mkmk,rvrn,tnum,pnum,case,frac,numr,dnom,sups,subs,zero' \
  --flavor=woff2 \
  --output-file="$target"
echo "Wrote $target"
