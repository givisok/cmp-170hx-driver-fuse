#!/usr/bin/env python3
"""Volatile PCIe Gen3 override + retrain for CMP 170HX (GA100).

Register sequence is a faithful copy of scrameta/cmpunlocker_fused
pcie-gen2-probe-retrain.patch (target_gen==2 branch) with the Gen3
values: LINK_CONFIG_0[19:18]=1 (REVERSED encoding: 0=Gen4,1=Gen3,
2=Gen2), LNKCTL2 TLS=8.0GT/s on GPU + upstream bridge, retrain via
upstream LNKCTL RL bit. All writes are volatile (SRAM/config), gone
on power cycle.

Usage: gen3_retrain.py [BDF]      e.g. gen3_retrain.py 0000:05:00.0
"""
import mmap
import os
import struct
import subprocess
import sys
import time

BDF = sys.argv[1] if len(sys.argv) > 1 else "0000:05:00.0"
SYS = f"/sys/bus/pci/devices/{BDF}"

R = {  # BAR0 offsets (16 MiB window)
    "LINK_CAP": 0x88084, "LINK_CTRL_STATUS": 0x88088, "LINK_CTRL_2": 0x880a8,
    "CYA_0": 0x8C2C0, "LINK_CONFIG_0": 0x8C040, "LTSSM_OVR": 0x8872C,
    "OPT_DIS_GEN3": 0x820250, "OPT_BOOT_GEN3": 0x820580, "OPT_MAGIC": 0x820520,
    "OPT_GEN23": 0x82057C,
}

fd = os.open(f"{SYS}/resource0", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 16 * 1024 * 1024)


def rd(name):
    o = R[name]
    return struct.unpack("<I", m[o:o + 4])[0]


def wr(name, val):
    o = R[name]
    m[o:o + 4] = struct.pack("<I", val & 0xFFFFFFFF)
    return rd(name)


def setpci(dev, reg, val, mask=None):
    if mask is not None:
        subprocess.run(["setpci", "-s", dev, f"{reg}={val}:{mask}"], check=False)
    else:
        subprocess.run(["setpci", "-s", dev, f"{reg}={val}"], check=False)


def link_sta(dev):
    out = subprocess.run(["setpci", "-s", dev, "CAP_EXP+12.w"],
                         capture_output=True, text=True).stdout.strip()
    try:
        return int(out, 16)
    except ValueError:
        return -1


upstream = os.path.basename(os.path.dirname(os.readlink(f"{SYS}")))
print(f"gpu={BDF} upstream={upstream}")

opt_dis, opt_boot, magic = rd("OPT_DIS_GEN3"), rd("OPT_BOOT_GEN3"), rd("OPT_MAGIC")
gen3_ok = (opt_dis & 1) == 0 and (opt_boot & 1) == 0
gen4_ok = gen3_ok and magic != 0xFFFFFFFF and (magic & 0x02000000) == 0
print(f"fuse gates: OPT_DIS_GEN3={opt_dis:#010x} OPT_BOOT_GEN3={opt_boot:#010x} "
      f"MAGIC_D={magic:#010x} -> gen3_ok={gen3_ok} gen4_ok={gen4_ok}")

print(f"before: STAT={rd('LINK_CTRL_STATUS'):#010x} CAP={rd('LINK_CAP'):#010x} "
      f"LC2={rd('LINK_CTRL_2'):#010x} LCfg={rd('LINK_CONFIG_0'):#010x} "
      f"CYA0={rd('CYA_0'):#010x}")

if not gen3_ok:
    print("!! fuse bits say Gen3 disabled (OPT_DIS_GEN3/OPT_BOOT_GEN3 bit0 set)")

# 1. clear DIS_G2 (CYA_0 bit2) — allow rate change at all
wr("CYA_0", rd("CYA_0") & ~(1 << 2))
# 2. LINK_CONFIG_0[19:18] = 1 -> Gen3 (reversed encoding)
wr("LINK_CONFIG_0", (rd("LINK_CONFIG_0") & ~0x000C0000) | (0x1 << 18))
# 3. trigger XVE override / LTSSM poke, as the fork does
wr("LTSSM_OVR", 0x6)
time.sleep(0.05)

# 4. LNKCTL2 target speed = 8.0GT/s on GPU and upstream bridge
setpci(BDF, "CAP_EXP+30.w", 3, "000f")
setpci(upstream, "CAP_EXP+30.w", 3, "000f")
# 5. retrain via upstream LNKCTL Retrain Link bit
ctl = link_sta_ctl = None
out = subprocess.run(["setpci", "-s", upstream, "CAP_EXP+10.w"],
                     capture_output=True, text=True).stdout.strip()
try:
    ctl = int(out, 16)
except ValueError:
    pass
if ctl is not None:
    setpci(upstream, "CAP_EXP+10.w", f"{ctl | 0x20:04x}")

ok = False
for kick in range(5):
    for i in range(15):
        time.sleep(0.2)
        sta = link_sta(BDF)
        if sta > 0 and (sta & 0xF) >= 3:
            ok = True
            break
    if ok:
        break
    # re-kick: target speed again + upstream retrain bit
    setpci(BDF, "CAP_EXP+30.w", 3, "000f")
    setpci(upstream, "CAP_EXP+30.w", 3, "000f")
    out2 = subprocess.run(["setpci", "-s", upstream, "CAP_EXP+10.w"], capture_output=True, text=True).stdout.strip()
    try:
        setpci(upstream, "CAP_EXP+10.w", f"{int(out2, 16) | 0x20:04x}")
    except ValueError:
        pass

sta = link_sta(BDF)
print(f"after:  LNKSTA={sta:#06x} (speed=Gen{sta & 0xF if sta > 0 else '?'}, "
      f"width=x{(sta >> 4) & 0x1F if sta > 0 else '?'})  trained={'YES' if ok else 'NO'}")
print(f"regs:   STAT={rd('LINK_CTRL_STATUS'):#010x} CAP={rd('LINK_CAP'):#010x} "
      f"LC2={rd('LINK_CTRL_2'):#010x} LCfg={rd('LINK_CONFIG_0'):#010x} "
      f"CYA0={rd('CYA_0'):#010x}")
