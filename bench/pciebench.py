"""PCIe bandwidth per card: pinned H2D / D2H and card-to-card copies (P2P is off on
this rig, so card-to-card goes through host memory). Made for the slot experiment:
the A100-strapped card sat on a Gen3 x8 port (x16 is ~23-24 GB/s, x8 ~12 GB/s).

Run it in the vLLM image with the model container stopped, like gpubench.py:

  pct exec 101 -- docker run --rm --runtime=nvidia --entrypoint python3 \
      -v /root/pciebench.py:/pciebench.py:ro vllm-sm80:q38fn /pciebench.py
"""
import itertools
import subprocess
import time

import torch

MB = 1 << 20
SIZE = 256 * MB          # bytes per transfer
ITERS = 20

FIELDS = "index,name,pci.bus_id,pcie.link.gen.current,pcie.link.width.current"


def smi():
    """nvidia-smi rows as {index: (name, bus id, gen, width)}, or {} without it."""
    try:
        out = subprocess.run(["nvidia-smi", f"--query-gpu={FIELDS}", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=30).stdout
        rows = {}
        for line in out.strip().splitlines():
            idx, name, bus, gen, width = [c.strip() for c in line.split(",")]
            dom, rest = bus.split(":", 1)      # 00000000:05:00.0 or 0000:05:00.0
            rows[int(idx)] = (name, f"{int(dom, 16):04x}:{rest}", gen, width)
        return rows
    except Exception:  # noqa: BLE001
        return {}


def rate(fn, sync_dev):
    """GB/s over ITERS asynchronous transfers, synced on the device that receives."""
    fn()
    torch.cuda.synchronize(sync_dev)
    t0 = time.perf_counter()
    for _ in range(ITERS):
        fn()
    torch.cuda.synchronize(sync_dev)
    return SIZE * ITERS / (time.perf_counter() - t0) / 1e9


info = smi()
n = torch.cuda.device_count()
for i in range(n):
    if i in info:
        print(f"nvidia-smi GPU{i}: {' | '.join(info[i][1:])}  [{info[i][0]}]")
host = torch.empty(SIZE // 2, dtype=torch.float16, pin_memory=True)
gpus = [torch.empty(SIZE // 2, dtype=torch.float16, device=i) for i in range(n)]
for i in range(n):
    p = torch.cuda.get_device_properties(i)
    print(f"GPU{i} [{p.name}]  H2D={rate(lambda: gpus[i].copy_(host, non_blocking=True), i):.1f} GB/s"
          f"  D2H={rate(lambda: host.copy_(gpus[i], non_blocking=True), i):.1f} GB/s")
for i, j in itertools.permutations(range(n), 2):
    print(f"GPU{i}->GPU{j}  {rate(lambda: gpus[j].copy_(gpus[i], non_blocking=True), j):.1f} GB/s")
