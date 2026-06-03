#!/bin/bash
# wakeups, what's poking the CPU while the Deck is "idle".
#
# On a battery handheld, the question that matters at idle isn't "what's using 100%
# CPU" (snapshot.sh answers that), it's "what keeps WAKING the CPU up." Lots of small
# wakeups stop the chip reaching its deep low-power states, which drains battery and
# adds heat even when load looks near-zero. This is the idle-power companion to the
# thermal logger.
#
# Two modes:
#   default    , no root needed. Samples /proc/interrupts over a window and reports
#                 which interrupt sources fired most (and on which core).
#   --powertop , deeper per-process/per-device wakeup data via powertop (needs sudo).
#
# Usage:
#   ./wakeups.sh                 # 10s interrupt-delta sample (no root)
#   ./wakeups.sh 30              # 30s window
#   ./wakeups.sh --powertop      # powertop wakeup report (prompts for sudo)
#   ./wakeups.sh --save [secs]   # save a timestamped report to ../data/
#
# Read-only: only observes.

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
DATA_DIR="$SCRIPT_DIR/../data"

SAVE=0
MODE=interrupts
while [ $# -gt 0 ]; do
  case "$1" in
    --save) SAVE=1 ;;
    --powertop) MODE=powertop ;;
    [0-9]*) WINDOW="$1" ;;
    *) echo "unknown arg: $1" ;;
  esac
  shift
done
WINDOW="${WINDOW:-10}"

# Snapshot /proc/interrupts into "name total" pairs (sum across all cores).
snap_interrupts() {
  awk '
    /^[[:space:]]*[0-9A-Za-z]+:/ {
      sum=0
      # columns 2..N are per-core counts until the controller name (non-numeric) starts
      for (i=2; i<=NF; i++) { if ($i ~ /^[0-9]+$/) sum+=$i; else break }
      # label = everything from the controller column onward (last 2-3 fields)
      label=$(NF)
      key=$1 gensub(/:$/,"",1,$1)   # irq number as stable key
      gsub(/:/,"",$1)
      print $1 "|" sum "|" label
    }' /proc/interrupts
}

report_interrupts() {
  echo "================================================================"
  echo " WAKEUPS (interrupt deltas) ,  $(date '+%Y-%m-%d %H:%M:%S')"
  echo " Sampling /proc/interrupts over ${WINDOW}s. Leave the Deck idle for a true reading."
  echo "================================================================"

  declare -A before
  while IFS='|' read -r irq cnt label; do before[$irq]="$cnt|$label"; done < <(snap_interrupts)

  sleep "$WINDOW"

  echo ""
  printf "%-8s %12s   %s\n" "IRQ" "fired/${WINDOW}s" "source"
  echo "-------- ------------   ------------------------------"
  while IFS='|' read -r irq cnt label; do
    b="${before[$irq]%%|*}"
    [ -n "$b" ] || b=0
    d=$(( cnt - b ))
    [ "$d" -gt 0 ] && printf "%-8s %12d   %s\n" "$irq" "$d" "$label"
  done < <(snap_interrupts) | sort -k2 -rn | head -20

  echo ""
  echo "Reading it: high counts from timers/USB/Wi-Fi while idle are normal-ish; a single"
  echo "source firing thousands of times a second is the thing worth chasing. Pair with"
  echo "'snapshot.sh' to see if a process is behind it, or '--powertop' for per-process data."
  echo "================================================================"
}

report_powertop() {
  echo "================================================================"
  echo " WAKEUPS (powertop) ,  $(date '+%Y-%m-%d %H:%M:%S')"
  echo "================================================================"
  echo "powertop needs root; you'll be prompted for sudo."
  echo "Running a single ${WINDOW}s measurement..."
  echo ""
  # powertop --time takes seconds; CSV output is the cleanest to parse/show.
  TMPCSV=$(mktemp)
  sudo powertop --csv="$TMPCSV" --time="$WINDOW" >/dev/null 2>&1
  if [ -s "$TMPCSV" ]; then
    echo "--- Top wakeup sources (from powertop) ---"
    # Pull the "Overview of Software Power Consumers" / wakeup section heuristically.
    awk '/Wakeups|Usage|Category|Power est/{p=1} p' "$TMPCSV" | head -40
    echo ""
    echo "(Full CSV saved alongside if --save was used.)"
  else
    echo "powertop produced no output (sudo declined, or not permitted)."
    echo "Falling back to interrupt-delta mode:"
    echo ""
    report_interrupts
  fi
  rm -f "$TMPCSV"
}

run() {
  if [ "$MODE" = "powertop" ]; then report_powertop; else report_interrupts; fi
}

if [ "$SAVE" = "1" ]; then
  mkdir -p "$DATA_DIR"
  OUT="$DATA_DIR/wakeups_$(date '+%Y-%m-%d_%H%M').txt"
  run | tee "$OUT"
  echo ""
  echo "Saved to: $OUT"
else
  run
fi
