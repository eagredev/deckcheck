#!/bin/bash
# Thermal + top-process logger for the Steam Deck.
# Samples temps, fan, power draw, CPU idle, and the top process once a minute.
#
# Usage:
#   ./thermal_log.sh            # logs for 60 minutes (default)
#   ./thermal_log.sh 30         # logs for 30 minutes
#
# Output: a timestamped CSV in ../data/ relative to this script.
# Run in the background so it survives over a long window:
#   nohup ./thermal_log.sh 60 >/dev/null 2>&1 &

MINUTES=${1:-60}

# Resolve data dir relative to this script, so it works wherever the tree is moved.
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
DATA_DIR="$SCRIPT_DIR/../data"
mkdir -p "$DATA_DIR"
LOG="$DATA_DIR/thermal_log_$(date '+%Y-%m-%d_%H%M').csv"

echo "timestamp,apu_edge_C,nvme_C,battery_C,fan_rpm,apu_watts,cpu_idle_pct,top_proc,top_cpu_pct" > "$LOG"
echo "Logging to: $LOG  (${MINUTES} samples, 1/min)"

for i in $(seq 1 "$MINUTES"); do
  ts=$(date '+%H:%M:%S')

  # Temps from sensors (fall back gracefully if a line is missing)
  s=$(sensors 2>/dev/null)
  apu=$(echo "$s"   | awk '/^edge:/        {gsub(/\+|°C/,"",$2); print $2; exit}')
  nvme=$(echo "$s"  | awk '/^Composite:/   {gsub(/\+|°C/,"",$2); print $2; exit}')
  batt=$(echo "$s"  | awk '/Battery Temp:/ {gsub(/\+|°C/,"",$3); print $3; exit}')
  fan=$(echo "$s"   | awk '/System Fan:/   {print $3; exit}')
  watts=$(echo "$s" | awk '/slowPPT:/      {print $2; exit}')

  # CPU idle %
  idle=$(top -bn1 2>/dev/null | awk -F',' '/Cpu\(s\)/{for(j=1;j<=NF;j++) if($j ~ /id/){gsub(/[^0-9.]/,"",$j); print $j; exit}}')

  # Top non-logger CPU process (exclude this script, top, sensors, ps itself)
  read -r tproc tcpu < <(ps -eo comm,%cpu --sort=-%cpu 2>/dev/null \
    | awk 'NR>1 && $1!~/^(top|ps|sensors|thermal_log|awk)$/ {print $1, $2; exit}')

  echo "$ts,$apu,$nvme,$batt,$fan,$watts,$idle,$tproc,$tcpu" >> "$LOG"
  sleep 60
done

echo "DONE $(date '+%H:%M:%S')" >> "$LOG"
echo "Finished. Results: $LOG"
