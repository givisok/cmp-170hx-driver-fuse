#!/bin/bash
# A100-strapped CMP 170HX SM-speed experiment: reload the NVIDIA modules with
# cmp_a100_ss=<mode> and capture the card's GSP boot log.
#
#   usage: a100-ss-test.sh <mode 0|1|2> [reset]
#     0     stock boot of the A100 card (safe state)
#     1     SM-speed override (PLMs + SS0/SS1) before GSP boot
#     2     control run: same boot flow, SS0/SS1 left alone
#     reset put WPR2/SS0/SS1 back first (needed when the previous run failed
#           with WPR2 still up; only lands while the PLMs are open)
#
# CT101 must be stopped: the reload drops every CUDA context on the host.
# A booter that never halts costs ~4 min of modprobe timeout per attempt; the
# kernel log is read from the timestamp taken right before modprobe.
set -u
mode="${1:?mode 0|1|2}"
do_reset="${2:-}"
LOG=/var/log/a100-ss-test.log
REGS=/root/a100-work/a100_regs.py

say() { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }

say "=== mode=$mode reset=${do_reset:-no} begin ==="
if pct status 101 2>/dev/null | grep -q running; then
    say "CT101 is running - refusing to reload the driver"; exit 1
fi
timeout 120 systemctl stop nvidia-persistenced
for m in nvidia_drm nvidia_uvm nvidia_modeset nvidia; do
    modprobe -r "$m" 2>&1 | sed "s/^/  modprobe -r $m: /" | tee -a "$LOG"
done
if grep -q '^nvidia ' /proc/modules; then
    say "FAILED to unload nvidia modules"; lsmod | grep nvidia | tee -a "$LOG"; exit 1
fi
python3 $REGS state 2>&1 | tee -a "$LOG"
[ "$do_reset" = reset ] && python3 $REGS reset 2>&1 | tee -a "$LOG"

start=$(date '+%F %T')
say "loading nvidia with cmp_a100_ss=$mode"
timeout 480 modprobe nvidia cmp_a100_ss="$mode" 2>&1 | tee -a "$LOG"
say "modprobe nvidia rc=${PIPESTATUS[0]} after $(( $(date +%s) - $(date -d "$start" +%s) ))s"
modprobe nvidia-modeset 2>&1 | tee -a "$LOG"
modprobe nvidia-uvm 2>&1 | tee -a "$LOG"
modprobe nvidia-drm 2>&1 | tee -a "$LOG"
sleep 2

echo "--- kernel log (GPU2 / 09:00.0) ---" | tee -a "$LOG"
journalctl -k --since "$start" --no-pager -o short-monotonic 2>/dev/null \
  | grep -E "GPU2|0000:09:00.0" \
  | grep -v -E "xp3g booter|PLM\[[0-9]+\] |s_executeBooterUcode|failed to execute Booter Load: 0xffff" \
  | cut -c1-300 | tee -a "$LOG"
echo "--- other GPUs: boot results ---" | tee -a "$LOG"
journalctl -k --since "$start" --no-pager -o short-monotonic 2>/dev/null \
  | grep -E "GPU[01] .*(normal BooterLoad|Max GSP-RM|RmInitAdapter)|probe routine failed|init failed during probe" \
  | cut -c1-220 | tee -a "$LOG"

timeout 60 nvidia-smi --query-gpu=index,name,pci.bus_id,memory.total,pcie.link.gen.current,pcie.link.width.current \
  --format=csv,noheader 2>&1 | tee -a "$LOG"
python3 $REGS state 2>&1 | tee -a "$LOG"
for b in 0000:05:00.0 0000:06:00.0; do
    python3 /root/gen3_retrain.py "$b" 2>&1 | grep -E "^after:" | sed "s/^/  $b /" | tee -a "$LOG"
done
say "=== mode=$mode end ==="
