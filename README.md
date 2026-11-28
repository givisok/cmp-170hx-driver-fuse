# cmp-170hx-driver-fuse

**English** | [Русский](README.ru.md)

> **Read this first: the cards must be fuse-burned.** Everything in this repository is built for
> NVIDIA CMP 170HX cards **after the OTP fuse burn** (a commercial unlock; the burn itself is not part
> of this repository, and it cannot be undone — OTP fuses are written once). The burn is what turns a
> stock card (8 GiB, 74 SM, PCIe Gen1/Gen2) into the hardware described below: 80 GiB of HBM2e,
> 106–112 SM, PCIe Gen3 and full SM throughput. On an unburned card none of the results, module
> options or boot fixes documented here apply — that is a different, far more modest configuration
> (64 GiB, 74 SM, Gen2).

Patched NVIDIA driver and boot fixes for a rig of **3× NVIDIA CMP 170HX (GA100)** after a commercial
OTP fuse burn: 80 GiB of HBM2e per card, 106–112 SM, PCIe Gen3 and full SM throughput — including one
card that was burned/re-strapped as an **A100 PCIe 80GB**.

Everything here runs on the Proxmox host right now (2026-11-28): nvidia-open **615.71.09** + modules
from [amoghmunikote/cmpunlocker v0.5](https://github.com/amoghmunikote/cmpunlocker) with local patches,
and a self-built kernel `7.0.14-29-z2p-pve`.

## Result

| | GPU0 — `05:00.0` | GPU1 — `06:00.0` | GPU2 — `09:00.0` |
|---|---|---|---|
| Strap | CMP 170HX (`10de:20c2`) | CMP 170HX (`10de:20c2`) | **A100 PCIe 80GB** (`10de:20b5`) |
| UUID | `GPU-2f74a495…` | `GPU-295351c7…` | `GPU-ec614963…` |
| Memory (nvidia-smi / CUDA) | 81920 MiB / 79 GiB | 81920 MiB / 79 GiB | 81920 MiB / 79 GiB |
| SM | 112 | 106 | 106 |
| PCIe | Gen3 x16 | Gen3 x16 | Gen3 x8 (LnkCap x16) |
| Max SM clock / power limit | 1695 MHz / 250 W | 1695 MHz / 250 W | 1410 MHz / 300 W |
| matmul 8192² fp16 | ~212 TFLOPS | ~214 TFLOPS | **~214 TFLOPS** |

Before the fix GPU2 computed **23× slower** (9.2 TFLOPS, an 8192² matmul in 119 ms instead of 5.1 ms),
and the model on the PP=3 pipeline kept waiting on exactly that rank. On the CMP pair a program could
only use 63 GiB out of 80. After two fixes all three cards are alike: ~214 TFLOPS and 79 GiB.

## What is in here

```
README.md                        this file, in English
README.ru.md                     the same in Russian: what was done, how it works, how to reproduce
LICENSE                          GPL-2.0 (the base project, cmpunlocker, is GPL-2.0)
cmpunlocker/                     driver working tree (= /root/cmpunlocker-0.5 on the host)
  driver/patches/                all patches against open-gpu-kernel-modules 615.71.09
  driver/build.sh                module build (80gb profile by default)
  common/constants.yaml          memory profiles; the read-constants.py validator depends on it
  LOCAL-CHANGES.md               how this tree differs from upstream v0.5
kernel/                          patches for the Proxmox kernel (z2p) and a Makefile fix
host/                            what lives on the host: services, boot scripts, modprobe.d
  HOST-README.md                 host operations documentation: history, traps, upgrades
a100/                            tools used to find and fix the SM speed of the A100 card
bench/gpubench.py                SM throughput measurement per card
bench/pciebench.py               PCIe bandwidth per card (not yet run on the rig)
docs/
  a100-sm-speed-unlock.md        why the card was slow and how it is fixed
  memory-80g-cmp.md              why the CMP pair had 63 GiB and how it became 79
  rig-and-history.md             hardware, topology, timeline from stock to the current state
  boot-logs/                     boot logs: the working path, failed attempts, the card swap test
```

The documents under `docs/`, `host/`, `a100/` and `kernel/` are written in Russian.

## What we did

The short version; details in [docs/rig-and-history.md](docs/rig-and-history.md).

1. **Starting point.** 3× CMP 170HX: 8 GiB, 74 SM, PCIe Gen1/Gen2. A fuse scan (2026-11-14) showed
   that some blocks were disabled by SKU segmentation and some were defective.
2. **Driver for unburned cards.** cmpunlocker v0.5 + local patches: 64 GiB, BAR1 32 GB (kernel quirks
   9901/9902, `-z2p` build), Gen2 retrain, automatic module rebuilds for new kernels.
3. **Fuse burn.** First we finished the memory ourselves (64 → 80 GiB), then gave the cards to a
   service ("the maximum possible"): the result is 112/106/106 SM, 80 GiB everywhere, Gen3 fuses on
   the 0x20c2 pair, and one card (ec61) fully re-strapped as an A100 PCIe 80GB.
4. **Rebuild for the new state.** The scrameta/cmpunlocker_fused fork did not fit (a broken patch +
   Booter 0x29 on the burned cards). Stayed on v0.5: 80gb profile, Gen3 values in `pcie-gen2.patch`,
   a boot-time BAR1 fix for the A100 card (the BIOS does not lay out its BARs), Gen3 retrain of the
   CMP pair.
5. **Symptom.** The model became much slower: the A100 card delivers 9.2 TFLOPS.
6. **Diagnosis.** The card's SM speed is throttled: the `FEATURE_OVERRIDE_SM_SPEED` registers
   (SS0 `0x82381c` / SS1 `0x823820`) are 0 on stock, while on the CMP pair the cmpunlocker patch
   writes `0x88888888` / `0x8` there. The write is protected by PLM, which can only be opened through
   the SEC2 bootloader.
7. **Fix.** Module parameter `cmp_a100_ss=1`: before the GSP is loaded we open the needed PLMs with
   the same mechanism cmpunlocker uses, restore WPR2, write SS0/SS1 — and the card boots normally.
8. **Verification.** Cold boot, 214 TFLOPS measured, vLLM on the three cards comes up by itself.
9. **Memory on the CMP pair (63 → 79 GiB).** The driver takes the memory size from the
   `NV_PFB_PRI_MMU_LOCAL_MEMORY_RANGE` register, and 64 GiB was written there following the profile
   for unburned cards. Parameter `cmp_lmr=0x28b` (80 GiB, like a real A100) + a full-size pattern
   memory test. Details in [docs/memory-80g-cmp.md](docs/memory-80g-cmp.md).

## How it boots

Cards are looked up by device id, so they can sit in any slot.

```
BIOS (Above 4G Decoding = Enabled)
 └─ kernel 7.0.14-29-z2p-pve
     ├─ quirk 9902: BAR1 of the CMP cards → 32 GB before bus numbering   (does not fire for the
     │                                                                     A100 card: the BIOS
     │                                                                     leaves its BAR0 unassigned)
     ├─ modprobe nvidia  (options nvidia cmp_a100_ss=1 cmp_lmr=0x28b)
     │    ├─ CMP cards: full cmpunlocker cycle (PLM, SS0/SS1, CFG1/LMR=80 GiB, SM-RECONFIG, PCIe)
     │    └─ A100 card: fails its first probe — the BIOS did not assign its BARs
     └─ gen3.service → /usr/local/sbin/gen3-boot
          1. A100 card: REBAR 128G → 32G (setpci 0xbc0) + remove/rescan
             → probe → the cmp_a100_ss path (PLM → restore WPR2 → SS0/SS1 → GSP)
          1a. a card still without a driver → its PLX board is re-enumerated
              (bridge windows off, root port removed, rescan — the kernel then plans the windows
               afresh; otherwise the board's 32-bit window lands inside the MMCONFIG range)
          2. waits for all 3 cards → Gen3 retrain of the CMP cards (/root/gen3_retrain.py)
          3. restart CT 101 (vLLM) so that /dev/nvidia* are mounted again
```

## Reproduction

Scripts and paths assume the upstream layout: the driver in `/root/cmpunlocker-0.5`, tools in
`/root/a100-work`. The order on an identical machine:

1. **Kernel.** Patches from `kernel/` into `patches/kernel/` of the pve-kernel sources,
   `KREL_EXTRA=-z2p`, build the `.deb` — the procedure and the traps are in
   [host/HOST-README.md](host/HOST-README.md).
2. **Modules.** The `cmpunlocker/` tree → `/root/cmpunlocker-0.5` (user space: the NVIDIA .run of the
   same version), then
   `cp host/cmpunlocker-kernel-modules /usr/local/sbin/ && /usr/local/sbin/cmpunlocker-kernel-modules --kernel "$(uname -r)" --force`
   — builds the modules for the current kernel without touching the running driver. The
   `host/kernel-postinst/` hook and `host/systemd/cmpunlocker-modules.{service,timer}` do the same
   for new kernels.
3. **Module options.** `cp host/modprobe.d/cmpunlocker-a100-ss.conf /etc/modprobe.d/`:
   `cmp_a100_ss=1` (SM speed of the A100 card) and `cmp_lmr=0x28b` (80 GiB on CMP cards after the
   burn; for unburned cards `cmp_lmr` is not needed — they really have 64 GiB).
4. **Boot fixes.** `cp host/gen3_retrain.py /root/`, `cp host/gen3-boot /usr/local/sbin/`,
   `cp host/systemd/gen3.service /etc/systemd/system/ && systemctl enable gen3.service`.
5. `reboot`.

## Checking after boot

```bash
cat /sys/module/nvidia/parameters/cmp_a100_ss            # 1
nvidia-smi --query-gpu=index,name,memory.total,pcie.link.gen.current,pcie.link.width.current --format=csv
journalctl -b -k | grep -E "GPU2.*(A100|BooterLoad)"
#  A100 WPR2 restore want=0x1ffffe00/0x00000000 read=0x1ffffe00/0x00000000
#  A100 SS unlock SS0 0x00000000->0x88888888 SS1 0x00000000->0x00000008
#  normal BooterLoad status=0x0
journalctl -b -k | grep -E "POST-WRITE|SEC2_DEBUG_HEAP"       # CMP: LMR=0x28b, publicBytes=0x13d30f0000
journalctl -b -u gen3.service                              # finished, CT101 rebooted
grep -a "link " /var/log/gen3.log                          # speed and width of every card's link
```

SM throughput: `bench/gpubench.py`; memory: `a100/memtest.py`; PCIe: `bench/pciebench.py` (torch,
inside a CUDA container; stop vLLM for the test).

## Experiments and rollback

* `a100/cmp-reload.sh [param=value …]` — reload the modules without a reboot, with any parameters
  (`cmp_lmr=0x28b` and so on), and snapshot the boot log of all cards; it stops vLLM and starts it
  back itself.
* `a100/a100-ss-test.sh <0|1|2> [reset]` — the same for the A100 card only: `0` stock (throttled),
  `1` the working mode, `2` a control run without the SS write.
* `a100/memtest.py <device>` — full-memory pattern test (in a CUDA container).
* Do not edit the patches `sec2-postbl-plm-ss-cfg.patch`, `booter-verify.patch`,
  `a100-ss-param.patch` by hand: `a100/mkpatches.py` regenerates them from the pristine tarball, and
  `a100/deploy.py` lays them out and validates (`--revert` to go back).
* To roll back only the SM unlock: delete `/etc/modprobe.d/cmpunlocker-a100-ss.conf`, `reboot` — the
  A100 card boots stock again (slow, but working).

## What remains

* The A100 card runs at **x8**. This is a defect of the card itself — the AC-coupling capacitors on
  the lanes of its edge connector; only lanes 0–7 work. Proven by swapping cards (2026-11-27/28): in
  a slot where a CMP always trains x16 this card gives x4, and the CMP in its slot gives x16. The fix
  is soldering, not software.
* P2P between the cards does not work on this hardware (X99 + PLX, NCCL goes through SHM) — the story
  is in [host/HOST-README.md](host/HOST-README.md).

## License and credits

GPL-2.0 (see `LICENSE`), the same as the base project. The base project and the authors of its
features are [amoghmunikote/cmpunlocker](https://github.com/amoghmunikote/cmpunlocker) (v0.5, commit
`17535a0`) and its [CREDITS.md](cmpunlocker/CREDITS.md). The idea of the volatile Gen3 retrain and of
native P2P is [scrameta/cmpunlocker_fused](https://github.com/scrameta/cmpunlocker_fused) (the fork
itself never worked on these cards). Fuse scanning — [matstatman/gpcprobe](https://github.com/matstatman/gpcprobe).
Driver — [NVIDIA/open-gpu-kernel-modules](https://github.com/NVIDIA/open-gpu-kernel-modules) 615.71.09.
