#!/bin/bash
# One-shot system snapshot for the Steam Deck.
# A quick "what's running / what's hot / what's busy right now" picture, run this
# FIRST when something feels off, before deciding whether a longer log is needed.
#
# Usage:
#   ./snapshot.sh              # print to screen
#   ./snapshot.sh --save       # also save a timestamped copy to ../data/
#
# Read-only: it only observes, never changes anything.

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
DATA_DIR="$SCRIPT_DIR/../data"

SAVE=0
[ "$1" = "--save" ] && SAVE=1

snapshot() {
  echo "================================================================"
  echo " STEAM DECK SNAPSHOT ,  $(date '+%Y-%m-%d %H:%M:%S')"
  echo "================================================================"

  echo ""
  echo "--- Uptime & load (load avg over 1/5/15 min) ---"
  uptime

  echo ""
  echo "--- CPU usage right now (idle % is the trustworthy figure) ---"
  top -bn1 2>/dev/null | grep -i "Cpu(s)"

  echo ""
  echo "--- Top 8 processes by CPU ---"
  ps -eo pid,comm,%cpu,%mem --sort=-%cpu 2>/dev/null | head -9

  echo ""
  echo "--- Top 8 processes by memory ---"
  ps -eo pid,comm,%mem,%cpu --sort=-%mem 2>/dev/null | head -9

  echo ""
  echo "--- Memory & swap ---"
  free -h

  echo ""
  echo "--- Temps / fan / power ---"
  sensors 2>/dev/null | grep -iE "edge:|Composite:|Battery Temp:|System Fan:|slowPPT:" \
    || echo "(sensors unavailable)"

  echo ""
  echo "--- Disk space (real filesystems only) ---"
  df -h 2>/dev/null | awk 'NR==1 || $1 ~ /^\/dev\//'

  echo ""
  echo "--- Disk write rate (5s sample, KB/s) ---"
  p=$(awk '$3=="nvme0n1"{print $8}' /proc/diskstats); sleep 5
  c=$(awk '$3=="nvme0n1"{print $8}' /proc/diskstats)
  echo "  nvme0n1: $(( (c-p)/2/5 )) KB/s"

  echo ""
  echo "--- Scheduled jobs that fire on a timer (system) ---"
  systemctl list-timers --no-pager 2>/dev/null | head -8

  echo ""
  echo "--- Baloo (KDE indexer) status, a repeat offender ---"
  balooctl6 status 2>/dev/null | head -3 || echo "(balooctl6 not found)"

  echo ""
  echo "================================================================"
  echo " End of snapshot"
  echo "================================================================"
}

if [ "$SAVE" = "1" ]; then
  mkdir -p "$DATA_DIR"
  OUT="$DATA_DIR/snapshot_$(date '+%Y-%m-%d_%H%M').txt"
  snapshot | tee "$OUT"
  echo ""
  echo "Saved to: $OUT"
else
  snapshot
fi
