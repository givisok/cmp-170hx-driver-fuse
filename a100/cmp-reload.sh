#!/bin/bash
# Reload the NVIDIA modules with the given module parameters and capture the GSP boot log.
#
#   usage: cmp-reload.sh [--no-ct] [--keep-stopped] [param=value ...]
#
# With no parameters the boot defaults apply (modprobe.d). The vLLM container is stopped
# for the reload: qwen-intel first (an explicit stop, so its unless-stopped policy does
# not bring it back mid-experiment), then CT 101 for the module unload. Afterwards:
#   - CT 101 is started again unless --no-ct is given;
#   - qwen-intel is started again if it was running before, unless --keep-stopped is
#     given (use that when the GPUs must stay free for tests, and start the model later
#     with:  pct exec 101 -- docker start qwen-intel).
# NB: a reboot does NOT clear the explicit stop - after --keep-stopped (or an aborted
# run) the container stays down across reboots until it is started by hand.
# Log: /var/log/cmp-reload.log, kernel part: /root/a100-work/last-reload-kernel.log
set -u
LOG=/var/log/cmp-reload.log
KLOG=/root/a100-work/last-reload-kernel.log
say() { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }
docker() { pct exec 101 -- docker "$@"; }

args=(); start_ct=1; keep_stopped=0
for a in "$@"; do
    [ "$a" = "--no-ct" ] && { start_ct=0; continue; }
    [ "$a" = "--keep-stopped" ] && { keep_stopped=1; continue; }
    args+=("$a")
done

was_running=0
if pct status 101 2>/dev/null | grep -q running; then
    for i in $(seq 1 40); do docker info >/dev/null 2>&1 && break; sleep 2; done
    [ "$(docker inspect -f '{{.State.Running}}' qwen-intel 2>/dev/null)" = "true" ] && was_running=1
fi

say "=== reload begin params: ${args[*]:-<boot defaults>} (qwen-intel was running: $was_running) ==="
if [ "$was_running" = 1 ]; then docker stop -t 5 qwen-intel >/dev/null 2>&1; fi
if pct status 101 2>/dev/null | grep -q running; then
    pct stop 101 2>/dev/null
    for i in $(seq 1 30); do pct status 101 2>/dev/null | grep -q stopped && break; sleep 1; done
fi
timeout 120 systemctl stop nvidia-persistenced
for m in nvidia_drm nvidia_uvm nvidia_modeset nvidia; do
    modprobe -r "$m" 2>&1 | sed "s/^/  modprobe -r $m: /" | tee -a "$LOG"
done
if grep -q '^nvidia ' /proc/modules; then
    say "FAILED to unload nvidia modules"; lsmod | grep nvidia | tee -a "$LOG"; exit 1
fi

start=$(date '+%F %T')
say "loading nvidia ${args[*]:-<boot defaults>}"
if [ ${#args[@]} -eq 0 ]; then
    timeout 480 modprobe nvidia 2>&1 | tee -a "$LOG"
else
    timeout 480 modprobe nvidia "${args[@]}" 2>&1 | tee -a "$LOG"
fi
say "modprobe nvidia rc=${PIPESTATUS[0]} after $(( $(date +%s) - $(date -d "$start" +%s) ))s"
modprobe nvidia-modeset 2>&1 | tee -a "$LOG"
modprobe nvidia-uvm 2>&1 | tee -a "$LOG"
modprobe nvidia-drm 2>&1 | tee -a "$LOG"
sleep 2

journalctl -k --since "$start" --no-pager -o short-monotonic 2>/dev/null \
  | grep -E "NVRM: GPU[0-9]|nvidia 0000:0[0-9a-f]:00.0: (enabling|probe)" \
  | grep -v -E "xp3g booter|s_executeBooterUcode|failed to execute Booter Load: 0xffff|LATE_PMA: region\[|GetNameString" \
  | cut -c1-320 > "$KLOG"
grep -E "POST-WRITE|WPR meta updated|normal BooterLoad|POST-BooterLoad|SEC2_DEBUG_HEAP|numFBRegions|Max GSP-RM|RmInitAdapter|A100 SS|late PMA|static-info" "$KLOG" \
  | cut -c1-280 | tee -a "$LOG"
echo "--- params / registers ---" | tee -a "$LOG"
echo "cmp_a100_ss=$(cat /sys/module/nvidia/parameters/cmp_a100_ss) cmp_lmr=$(cat /sys/module/nvidia/parameters/cmp_lmr)" | tee -a "$LOG"
python3 /root/a100-work/gpu_regs.py | grep -o -E "^0000:0[0-9a-f]:00.0|SS0=0x[0-9a-f]+|LMR=0x[0-9a-f]+" | paste -sd' ' | sed 's/ 0000:/\n0000:/g' | tee -a "$LOG"
timeout 60 nvidia-smi --query-gpu=index,name,memory.total,pcie.link.gen.current,pcie.link.width.current \
  --format=csv,noheader 2>&1 | tee -a "$LOG"
for b in $(lspci -D -d 10de:20c2 | awk '{print $1}'); do    # CMP cards by device id (any slot)
    python3 /root/gen3_retrain.py "$b" 2>&1 | grep -E '^after:' | sed "s/^/  $b /" | tee -a "$LOG"
done

if [ "$start_ct" = 1 ]; then
    pct start 101 >/dev/null 2>&1
    for i in $(seq 1 40); do docker info >/dev/null 2>&1 && break; sleep 2; done
    if [ "$was_running" = 1 ] && [ "$keep_stopped" = 0 ]; then
        docker start qwen-intel >/dev/null 2>&1 && say "qwen-intel started again (model load takes ~5 min)"
    fi
    docker ps -a --format "  {{.Names}}  {{.Status}}" 2>&1 | tee -a "$LOG"
fi
say "=== reload end ==="
