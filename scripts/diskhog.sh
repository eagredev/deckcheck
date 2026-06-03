#!/bin/bash
# diskhog, what's eating disk space and disk WRITES on the Steam Deck.
#
# Two questions, one tool:
#   1. What's taking up SPACE?        (biggest dirs by size)
#   2. What's causing write WEAR?     (per-process lifetime writes + file-count hotspots)
#
# The file-count hotspots matter because huge piles of tiny files (game prefixes,
# decomp trees, node_modules) are what make indexers/sync tools thrash, exactly the
# baloo problem deckcheck was born diagnosing. On an SSD, sustained writes = wear, so
# "who has written the most since boot" is a genuinely useful question.
#
# Usage:
#   ./diskhog.sh                 # scan $HOME
#   ./diskhog.sh /some/path      # scan a specific path
#   ./diskhog.sh --save [path]   # also save a timestamped report to ../data/
#
# Read-only: only observes, never deletes anything.

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
DATA_DIR="$SCRIPT_DIR/../data"

SAVE=0
if [ "$1" = "--save" ]; then SAVE=1; shift; fi
TARGET="${1:-$HOME}"

report() {
  echo "================================================================"
  echo " DISKHOG ,  $(date '+%Y-%m-%d %H:%M:%S') ,  scanning: $TARGET"
  echo "================================================================"

  echo ""
  echo "--- Filesystem usage (where's the space?) ---"
  df -h "$TARGET" 2>/dev/null

  echo ""
  echo "--- Top 15 directories by SIZE under $TARGET ---"
  echo "    (one level deep; follow up by re-running on a subdir)"
  du -h --max-depth=1 "$TARGET" 2>/dev/null | sort -rh | head -16

  echo ""
  echo "--- Top 12 directories by FILE COUNT under $TARGET ---"
  echo "    (huge file counts are what make indexers/sync tools thrash)"
  for d in "$TARGET"/*/ "$TARGET"/.*/; do
    [ -d "$d" ] || continue
    case "$d" in */./|*/../) continue;; esac
    n=$(find "$d" -type f 2>/dev/null | wc -l)
    printf "%10d  %s\n" "$n" "$d"
  done | sort -rn | head -12

  echo ""
  echo "--- Top processes by LIFETIME DISK WRITES (since they started) ---"
  echo "    (sustained writes = SSD wear; the baloo offender lived here)"
  for pid in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
    io=/proc/$pid/io
    [ -r "$io" ] || continue
    wb=$(awk '/^write_bytes:/{print $2}' "$io" 2>/dev/null)
    [ -n "$wb" ] && [ "$wb" -gt 0 ] 2>/dev/null || continue
    comm=$(cat /proc/$pid/comm 2>/dev/null)
    # human-readable MB
    mb=$(( wb / 1048576 ))
    printf "%8d MB  pid=%-7s %s\n" "$mb" "$pid" "$comm"
  done | sort -rn | head -12

  echo ""
  echo "--- Total disk writes since boot (per device, MB) ---"
  awk '$3 ~ /^(nvme0n1|mmcblk0)$/ {printf "  %-10s %d MB written\n", $3, $10/2048}' /proc/diskstats

  echo ""
  echo "================================================================"
  echo " End of diskhog. Tip: re-run on a flagged subdir to drill down,"
  echo " e.g.  ./diskhog.sh ~/.local/share/Steam"
  echo "================================================================"
}

if [ "$SAVE" = "1" ]; then
  mkdir -p "$DATA_DIR"
  OUT="$DATA_DIR/diskhog_$(date '+%Y-%m-%d_%H%M').txt"
  report | tee "$OUT"
  echo ""
  echo "Saved to: $OUT"
else
  report
fi
