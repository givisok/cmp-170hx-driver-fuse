#!/usr/bin/env python3
"""Dump GSP-boot related registers of a GPU through its 16 MiB BAR0.

  gpu_regs.py [BDF]     default: all three cards
Only addresses below 16 MiB are reachable this way (direct PRI aperture);
anything higher goes through the driver's register window.
"""
import mmap
import os
import struct
import sys

REGS = {
    "PLM_WPR_CFG": 0x1FA7CC, "PLM_WPR": 0x1FA7C4,
    "WPR2_LO": 0x1FA824, "WPR2_HI": 0x1FA828,
    "PLM_FEAT": 0x823804, "SS0": 0x82381C, "SS1": 0x823820, "LMR": 0x100CE0,
    "PLM_FEAT2": 0x823B00, "PLM_OPT": 0x8200FC, "PLM_RECONFIG": 0x8200D8,
    "OPT_GPC_DISABLE": 0x820350,
}
bdfs = sys.argv[1:] or ["0000:05:00.0", "0000:06:00.0", "0000:09:00.0"]
for bdf in bdfs:
    try:
        fd = os.open(f"/sys/bus/pci/devices/{bdf}/resource0", os.O_RDWR | os.O_SYNC)
    except OSError as e:
        print(bdf, "unavailable:", e)
        continue
    m = mmap.mmap(fd, 16 * 1024 * 1024)
    vals = {n: struct.unpack("<I", m[o:o + 4])[0] for n, o in REGS.items()}
    print(bdf, " ".join(f"{n}={v:#010x}" for n, v in vals.items()))
    m.close()
    os.close(fd)
