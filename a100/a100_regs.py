#!/usr/bin/env python3
"""Read (and put back) the GSP-boot state registers of the A100-strapped
CMP 170HX at 09:00.0, through BAR0.

  a100_regs.py state    print WPR2 / PLM / SS0 / SS1
  a100_regs.py reset    WPR2 back "down" (HI=0) and SS0/SS1 back to the stock
                        fuse grade. Writes only land while the PLMs are open,
                        i.e. after a cmp_a100_ss run; a rejected write shows up
                        as an unchanged readback.

Only volatile state is touched; a power cycle clears all of it anyway.
"""
import mmap
import os
import struct
import sys

BDF = "0000:09:00.0"
REGS = {
    "WPR2_LO": 0x1FA824, "WPR2_HI": 0x1FA828,
    "PLM_WPR_CFG": 0x1FA7CC, "PLM_WPR": 0x1FA7C4, "PLM_FEAT": 0x823804,
    "SS0": 0x82381C, "SS1": 0x823820,
}
# as read on a fresh boot of this card
STOCK_WPR2_LO, STOCK_SS0, STOCK_SS1 = 0x1FFFFE00, 0x02042073, 0x00000000

fd = os.open(f"/sys/bus/pci/devices/{BDF}/resource0", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 16 * 1024 * 1024)


def rd(name):
    o = REGS[name]
    return struct.unpack("<I", m[o:o + 4])[0]


def wr(name, val):
    o = REGS[name]
    m[o:o + 4] = struct.pack("<I", val & 0xFFFFFFFF)


def state():
    return " ".join(f"{n}={rd(n):#010x}" for n in REGS)


mode = sys.argv[1] if len(sys.argv) > 1 else "state"
print("before:", state())
if mode == "reset":
    wr("WPR2_HI", 0)
    wr("WPR2_LO", STOCK_WPR2_LO)
    wr("SS0", STOCK_SS0)
    wr("SS1", STOCK_SS1)
    print("after: ", state())
