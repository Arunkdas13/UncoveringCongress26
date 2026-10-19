#!/usr/bin/env bash
# Pull every upstream input into ./work. Safe to re-run.
set -euo pipefail
mkdir -p work
if [ -d work/ftm/.git ]; then
  git -C work/ftm pull -q --ff-only
else
  git clone -q --depth 1 https://github.com/sabbadoo32/follow-the-money-2026 work/ftm
fi
curl -fsSL --retry 3 -o work/legislators-current.json \
  https://raw.githubusercontent.com/unitedstates/congress-legislators/gh-pages/legislators-current.json
# FEC bulk files (optional: the build runs without them, with a coarser money breakdown)
curl -fsSL --retry 3 -o work/weball26.zip https://www.fec.gov/files/bulk-downloads/2026/weball26.zip || echo "weball26.zip not fetched"
curl -fsSL --retry 3 -o work/cn26.zip     https://www.fec.gov/files/bulk-downloads/2026/cn26.zip     || echo "cn26.zip not fetched"
ls -la work
