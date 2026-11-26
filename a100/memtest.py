#!/usr/bin/env python3
"""Whole-memory integrity test for one GPU.

Fills (almost) all free memory with a position-unique pattern in 1 GiB chunks, then
verifies every chunk. Detects address aliasing (two chunks backed by the same physical
memory: the later write wins, the earlier chunk fails verification) and unreadable
ranges. Run it once per GPU; a GPU whose top memory is not really addressable fails on
some chunks or on allocation.

  memtest.py <cuda device index> [headroom GiB, default 4]
"""
import sys
import time

import torch


def s64(v):
    return v - (1 << 64) if v >= (1 << 63) else v


def wrap(v):
    """Python int -> int64 two's complement scalar (torch rejects out-of-range scalars)."""
    v &= (1 << 64) - 1
    return s64(v)


C0 = s64(0x9E3779B97F4A7C15)
C1 = s64(0xBF58476D1CE4E5B9)
C2 = s64(0x94D049BB133111EB)


def mix(buf, off, salt, scratch):
    """buf <- splitmix64-like hash of (off + salt), in place; one scratch tensor."""
    torch.add(off, wrap((salt + 1) * C0), out=buf)
    torch.bitwise_right_shift(buf, 30, out=scratch)
    buf.bitwise_xor_(scratch)
    buf.mul_(C1)
    torch.bitwise_right_shift(buf, 27, out=scratch)
    buf.bitwise_xor_(scratch)
    buf.mul_(C2)
    torch.bitwise_right_shift(buf, 31, out=scratch)
    buf.bitwise_xor_(scratch)


def main():
    dev = int(sys.argv[1])
    headroom = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    gib = 1 << 30
    torch.cuda.set_device(dev)
    free, total = torch.cuda.mem_get_info(dev)
    p = torch.cuda.get_device_properties(dev)
    n = int((free - headroom * gib) // gib)
    n = max(n, 0)
    n_elems = gib // 8
    print(f"GPU{dev} [{p.name}] SMs={p.multi_processor_count} total={total / gib:.2f} GiB "
          f"free={free / gib:.2f} GiB -> testing {n} GiB in {n} chunks", flush=True)
    off = torch.arange(n_elems, dtype=torch.int64, device=dev)
    scratch = torch.empty(n_elems, dtype=torch.int64, device=dev)
    ref = torch.empty(n_elems, dtype=torch.int64, device=dev)

    t0 = time.time()
    chunks = [torch.empty(n_elems, dtype=torch.int64, device=dev) for _ in range(n)]
    print(f"allocated {n} GiB in {time.time() - t0:.1f}s", flush=True)

    t0 = time.time()
    for i, c in enumerate(chunks):
        mix(c, off, i, scratch)
    torch.cuda.synchronize()
    tw = time.time() - t0

    t0 = time.time()
    bad = []
    for i, c in enumerate(chunks):
        mix(ref, off, i, scratch)
        if not torch.equal(c, ref):
            bad.append(i)
    torch.cuda.synchronize()
    tr = time.time() - t0

    gib_bytes = n * gib
    verdict = "PASS" if not bad else f"FAIL ({len(bad)} bad chunks: {bad[:32]})"
    print(f"memtest {verdict}: {n} GiB, write {tw:.1f}s ({gib_bytes / tw / 2**30:.0f} GiB/s), "
          f"verify {tr:.1f}s", flush=True)
    del chunks, ref, scratch, off
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
