# Sourced by start.sh and stop.sh: `snapshot "label"` prints CPU, memory, GPU and what Fleck holds.
snapshot() {
  local page; page=$(sysctl -n hw.pagesize)
  echo "=== $1 ($(date +%H:%M:%S)) ==="
  top -l 2 -n 0 -s 1 | grep "CPU usage" | tail -1 | sed 's/^/CPU      /'
  vm_stat | awk -v p="$page" -v tot="$(sysctl -n hw.memsize)" '
    /Pages active/ {a=$3} /Pages wired/ {w=$4} /occupied by compressor/ {c=$5}
    END {printf "Memory   %.1f GB used of %.0f GB (active+wired+compressed)\n", (a+w+c)*p/2^30, tot/2^30}'
  memory_pressure | tail -1 | sed 's/^/Memory   /'
  ioreg -r -d 1 -c IOAccelerator | grep -o '"PerformanceStatistics" = {[^}]*}' | tr ',' '\n' | awk -F= '
    /"Device Utilization %"/ {u=$2} /"In use system memory"/ {m=$2}
    END {printf "GPU      %s%% busy, %.1f GB in use\n", u, m/2^30}'
  if curl -sf localhost:11434/api/tags >/dev/null; then
    local models; models=$(ollama ps | awk 'NR>1 {printf "%s (%s %s) ", $1, $3, $4}')
    echo "Ollama   ${models:-no models loaded}"
  else
    echo "Ollama   not running"
  fi
  local docker_mem; docker_mem=$(docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' 2>/dev/null | grep '^fleck-' | awk '{print $1, $2}' | paste -sd, - | sed 's/,/, /g')
  echo "Docker   ${docker_mem:-no Fleck containers}"
  local helper; helper=$(pgrep -f "Fleck Helper.app/Contents/MacOS" | head -1)
  if [ -n "$helper" ]; then echo "Helper   running, $(( $(ps -o rss= -p "$helper") / 1024 )) MB"; else echo "Helper   not running"; fi
  echo
}
