#!/usr/bin/env python3
"""Regenerate the cmpunlocker patches for the A100-strapped CMP 170HX SM-speed path.

Starts from the pristine 615.71.09 tarball, applies the KNOWN-GOOD versions of
sec2-postbl-plm-ss-cfg.patch and booter-verify.patch, layers the A100 changes on
top as exact-string edits (every anchor must match exactly once) and re-diffs
against pristine, so hunk headers are always correct. Also emits the new
a100-ss-param.patch and applies the whole new PATCH_ORDER to a fresh tree as a
dry run. Nothing under /root/cmpunlocker-0.5 is modified here; outputs land in
/root/a100-work/out for review.
"""
import pathlib
import re
import shutil
import subprocess
import sys

W = pathlib.Path('/root/a100-work')
TAR = '/root/cmpunlocker-0.5/driver/.build/open-gpu-kernel-modules-615.71.09.tar.gz'
TOP = 'open-gpu-kernel-modules-615.71.09'
REPO = pathlib.Path('/root/cmpunlocker-0.5')
PDIR = REPO / 'driver' / 'patches'
GOOD_SEC2 = W / 'in' / 'sec2-good-backup.patch'
# upstream v0.5 (commit 17535a0) version; the deployed one is already regenerated from it
GOOD_BV = W / 'in' / 'booter-verify-upstream.patch'
OUT = W / 'out'
NEW_PATCH = 'a100-ss-param.patch'

GSP_H = 'src/nvidia/generated/g_kernel_gsp_nvoc.h'
GSP_C = 'src/nvidia/src/kernel/gpu/gsp/kernel_gsp.c'
TU102_C = 'src/nvidia/src/kernel/gpu/gsp/arch/turing/kernel_gsp_tu102.c'
NVPCI_C = 'kernel-open/nvidia/nv-pci.c'
FILES = [GSP_H, GSP_C, TU102_C, NVPCI_C]


def run(cmd, cwd=None, check=True, stdin=None):
    r = subprocess.run(cmd, cwd=cwd, stdin=stdin, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit('FAILED: %s\n%s%s' % (cmd, r.stdout, r.stderr))
    return r


def extract(dest, files=None):
    dest.mkdir(parents=True, exist_ok=True)
    members = [TOP + '/' + f for f in files] if files else [TOP]
    run(['tar', '-xzf', TAR, '-C', str(dest)] + members)


def edit(text, old, new, what):
    n = text.count(old)
    if n != 1:
        sys.exit('edit "%s": anchor matched %d times (want 1)' % (what, n))
    return text.replace(old, new)


def rmc(path):
    if path.exists():
        shutil.rmtree(path)


# ---------------------------------------------------------------- kernel_gsp.c
GATE_OLD = r'''static NvBool
_kgspSec2PostblTimingEnabled(OBJGPU *pGpu)
{
    NvU32 devId = pGpu->idInfo.PCIDeviceID >> 16;
    return (devId == SEC2_POSTBL_TIMING_CMP_170HX_8GB_PCI_DEVICE_ID ||
            devId == SEC2_POSTBL_TIMING_CMP_170HX_10GB_PCI_DEVICE_ID);
}
'''

GATE_NEW = r'''// Defined in kernel-open/nvidia/nv-pci.c. 0: the A100-strapped card boots stock.
// 1: SM-speed override. 2: control run (same boot flow, SS0/SS1 left alone).
extern int cmp_a100_ss;
// Same file. -1 keeps the profile LOCAL_MEMORY_RANGE for the CMP cards; >= 0 overrides it.
extern int cmp_lmr;

static NvBool
_kgspSec2PostblTimingEnabled(OBJGPU *pGpu)
{
    NvU32 devId = pGpu->idInfo.PCIDeviceID >> 16;
    return (devId == SEC2_POSTBL_TIMING_CMP_170HX_8GB_PCI_DEVICE_ID ||
            devId == SEC2_POSTBL_TIMING_CMP_170HX_10GB_PCI_DEVICE_ID ||
            (devId == SEC2_POSTBL_TIMING_A100_STRAPPED_PCI_DEVICE_ID &&
             cmp_a100_ss != 0));
}
'''

MAIN_LOOP = '        for (plmIdx = 0; plmIdx < 12; plmIdx++)\n'

# CMP POST-WRITE block. 0x00100ce0 is NV_PFB_PRI_MMU_LOCAL_MEMORY_RANGE (LOWER_SCALE
# [3:0], LOWER_MAG [9:4]); the usable FB size the driver works with is mag << (scale + 20),
# so 0x20b = 32 * 2 GiB = 64 GiB.
POSTW_STOCK_OLD = '''            NvU32 cfg1Value;
            NvU32 lmrValue;

            if (devId == SEC2_POSTBL_TIMING_CMP_170HX_8GB_PCI_DEVICE_ID || devId == 0x20B5U)
'''
POSTW_STOCK_NEW = '''            NvU32 cfg1Value;
            NvU32 lmrValue;
            NvU32 cfg1Stock = GPU_REG_RD32(pGpu, 0x009a0204U);
            NvU32 lmrStock  = GPU_REG_RD32(pGpu, 0x00100ce0U);

            if (devId == SEC2_POSTBL_TIMING_CMP_170HX_8GB_PCI_DEVICE_ID || devId == 0x20B5U)
'''

POSTW_OVR_OLD = '''                lmrValue  = 0x0000028AU;
            }

            GPU_REG_WR32(pGpu, 0x0082381cU, 0x88888888U);
'''
POSTW_OVR_NEW = r'''                lmrValue  = 0x0000028AU;
            }

            //
            // 0x00100ce0 is NV_PFB_PRI_MMU_LOCAL_MEMORY_RANGE: LOWER_SCALE [3:0],
            // LOWER_MAG [9:4]. The driver sizes the GSP firmware region from it
            // (mag << (scale + 20)), so 0x20b = 32 * 2 GiB = 64 GiB, which is what the
            // 8gb profile assumes (8 FBP x 8 GiB). Cards whose fuse burn enabled 10 FBPs
            // (20 FBPA x 4 GiB) get the full 80 GiB with cmp_lmr=0x28b (40 * 2 GiB), the
            // value a real A100 80GB carries.
            //
            if (cmp_lmr >= 0)
                lmrValue = (NvU32)cmp_lmr;

            GPU_REG_WR32(pGpu, 0x0082381cU, 0x88888888U);
'''

POSTW_PRINT_OLD = r'''                      "CFG1=0x%08x LMR=0x%08x (devId=0x%x)\n",
                      GPU_REG_RD32(pGpu, 0x0082381cU),
                      GPU_REG_RD32(pGpu, 0x00823820U),
                      GPU_REG_RD32(pGpu, 0x009a0204U),
                      GPU_REG_RD32(pGpu, 0x00100ce0U),
                      devId);
'''
POSTW_PRINT_NEW = r'''                      "CFG1=0x%08x LMR=0x%08x (devId=0x%x) stock CFG1=0x%08x LMR=0x%08x\n",
                      GPU_REG_RD32(pGpu, 0x0082381cU),
                      GPU_REG_RD32(pGpu, 0x00823820U),
                      GPU_REG_RD32(pGpu, 0x009a0204U),
                      GPU_REG_RD32(pGpu, 0x00100ce0U),
                      devId, cfg1Stock, lmrStock);
'''

A100_BLOCK = r'''        if (devId == SEC2_POSTBL_TIMING_A100_STRAPPED_PCI_DEVICE_ID && cmp_a100_ss != 0)
        {
            /*
             * A100-strapped card: memory geometry is native, so only the
             * SM-speed override is needed. WPR_CFG and WPR are opened first
             * because the WPR2 address registers can only be put back down
             * between booter runs while their PLMs are open; FEAT unlocks
             * SS0/SS1. The CMP-only CFG1/LMR, SM-RECONFIG and PCIe sequences
             * are skipped. cmp_a100_ss == 2 leaves SS0/SS1 alone (control run).
             */
            static const NvU32 a100PlmIdx[] = { 0U, 2U, 3U };
            NvU32 a100Idx;

            NV_PRINTF(LEVEL_ERROR,
                      "SEC2_DEBUG: A100 mode=%d stock SS0=0x%08x SS1=0x%08x "
                      "PLM WPR_CFG=0x%08x WPR=0x%08x FEAT=0x%08x\n",
                      cmp_a100_ss,
                      GPU_REG_RD32(pGpu, 0x0082381cU), GPU_REG_RD32(pGpu, 0x00823820U),
                      GPU_REG_RD32(pGpu, 0x001fa7ccU), GPU_REG_RD32(pGpu, 0x001fa7c4U),
                      GPU_REG_RD32(pGpu, 0x00823804U));

            for (a100Idx = 0; a100Idx < NV_ARRAY_ELEMENTS(a100PlmIdx); a100Idx++)
            {
                NvBool opened = NV_FALSE;
                plmIdx = a100PlmIdx[a100Idx];
                for (attempt = 0; attempt < 2 && !opened; attempt++)
                {
                    GPU_REG_WR32(pGpu, 0x001fa824U, wpr2Lo);
                    GPU_REG_WR32(pGpu, 0x001fa828U, wpr2Hi);

                    plmStatus = kgspSec2PostblTimingRefillPayload(pGpu, pKernelGsp,
                        plmTable[plmIdx].addr, plmTable[plmIdx].value);
                    if (plmStatus != NV_OK)
                        continue;

                    plmStatus = kgspExecuteBooterLoad_HAL(pGpu, pKernelGsp,
                        memdescGetPhysAddr(SEC2_POSTBL_TIMING_WPR_META_DESC(pKernelGsp), AT_GPU, 0));

                    NV_PRINTF(LEVEL_ERROR,
                              "SEC2_DEBUG: A100 PLM[%u] %s(0x%x) attempt=%u status=0x%x "
                              "reg=0x%08x WPR2=0x%08x/0x%08x\n",
                              plmIdx, plmTable[plmIdx].name, plmTable[plmIdx].addr,
                              attempt, plmStatus, GPU_REG_RD32(pGpu, plmTable[plmIdx].addr),
                              GPU_REG_RD32(pGpu, 0x001fa824U), GPU_REG_RD32(pGpu, 0x001fa828U));

                    if (GPU_REG_RD32(pGpu, plmTable[plmIdx].addr) == plmTable[plmIdx].value)
                        opened = NV_TRUE;
                }
                if (!opened)
                    NV_PRINTF(LEVEL_ERROR,
                              "SEC2_DEBUG: A100 FAILED to open %s after 2 attempts\n",
                              plmTable[plmIdx].name);
            }

            GPU_REG_WR32(pGpu, 0x001fa824U, wpr2Lo);
            GPU_REG_WR32(pGpu, 0x001fa828U, wpr2Hi);
            NV_PRINTF(LEVEL_ERROR,
                      "SEC2_DEBUG: A100 WPR2 restore want=0x%08x/0x%08x read=0x%08x/0x%08x\n",
                      wpr2Lo, wpr2Hi,
                      GPU_REG_RD32(pGpu, 0x001fa824U), GPU_REG_RD32(pGpu, 0x001fa828U));

            if (cmp_a100_ss == 1)
            {
                NvU32 ss0Before = GPU_REG_RD32(pGpu, 0x0082381cU);
                NvU32 ss1Before = GPU_REG_RD32(pGpu, 0x00823820U);

                GPU_REG_WR32(pGpu, 0x0082381cU, 0x88888888U);
                GPU_REG_WR32(pGpu, 0x00823820U, 0x00000008U);
                NV_PRINTF(LEVEL_ERROR,
                          "SEC2_DEBUG: A100 SS unlock SS0 0x%08x->0x%08x SS1 0x%08x->0x%08x\n",
                          ss0Before, GPU_REG_RD32(pGpu, 0x0082381cU),
                          ss1Before, GPU_REG_RD32(pGpu, 0x00823820U));
            }

            plmStatus = kgspSec2PostblTimingRebuildStockSignature(pGpu, pKernelGsp);
            if (plmStatus != NV_OK)
            {
                NV_PRINTF(LEVEL_ERROR,
                          "SEC2_DEBUG: A100 rebuild stock signature failed: 0x%x\n", plmStatus);
                return plmStatus;
            }

            NV_CHECK_OK_OR_RETURN(LEVEL_ERROR, kgspPopulateWprMeta_HAL(pGpu, pKernelGsp, pGspFw));
            NV_PRINTF(LEVEL_ERROR,
                      "SEC2_DEBUG: A100 WPR meta updated fbSize=0x%016llx wprStart=0x%016llx "
                      "wprEnd=0x%016llx heapOffset=0x%016llx heapSize=0x%016llx "
                      "WPR2=0x%08x/0x%08x\n",
                      SEC2_POSTBL_TIMING_WPR_META(pKernelGsp)->fbSize,
                      SEC2_POSTBL_TIMING_WPR_META(pKernelGsp)->gspFwWprStart,
                      SEC2_POSTBL_TIMING_WPR_META(pKernelGsp)->gspFwWprEnd,
                      SEC2_POSTBL_TIMING_WPR_META(pKernelGsp)->gspFwHeapOffset,
                      SEC2_POSTBL_TIMING_WPR_META(pKernelGsp)->gspFwHeapSize,
                      GPU_REG_RD32(pGpu, 0x001fa824U), GPU_REG_RD32(pGpu, 0x001fa828U));
            goto a100Done;
        }

'''

LABEL_OLD = r'''                  SEC2_POSTBL_TIMING_WPR_META(pKernelGsp)->gspFwHeapOffset,
                  SEC2_POSTBL_TIMING_WPR_META(pKernelGsp)->gspFwHeapSize);
    }
'''

LABEL_NEW = r'''                  SEC2_POSTBL_TIMING_WPR_META(pKernelGsp)->gspFwHeapOffset,
                  SEC2_POSTBL_TIMING_WPR_META(pKernelGsp)->gspFwHeapSize);

a100Done:;
    }
'''

# superseded by the A100 block above (it was unreachable anyway: the gate
# excluded 0x20B5, and with cmp_a100_ss the block exits through a100Done)
DEAD_OLD = r'''        {
            NvU32 devIdA100 = pGpu->idInfo.PCIDeviceID >> 16;
            if (devIdA100 == 0x20B5U)
            {
                GPU_REG_WR32(pGpu, 0x0082381cU, 0x88888888U);
                GPU_REG_WR32(pGpu, 0x00823820U, 0x00000008U);
                NV_PRINTF(LEVEL_ERROR,
                          "SEC2_DEBUG: A100-strapped SS0/SS1 unlock: SS0=0x%08x SS1=0x%08x\n",
                          GPU_REG_RD32(pGpu, 0x0082381cU),
                          GPU_REG_RD32(pGpu, 0x00823820U));
            }
        }

'''

# --------------------------------------------------------- kernel_gsp_tu102.c
BV_DEFINE_OLD = '#define SEC2_DEBUG_PRI_MMU_LMR                      0x00100ce0\n'
BV_DEFINE_NEW = (
    '#define SEC2_DEBUG_PRI_MMU_LMR                      0x00100ce0\n'
    '#define SEC2_DEBUG_PRI_MMU_WPR2_LO                  0x001fa824\n'
    '#define SEC2_DEBUG_PRI_MMU_WPR2_HI                  0x001fa828\n'
    '\n'
    '// defined in kernel-open/nvidia/nv-pci.c (0 = A100-strapped card left stock)\n'
    'extern int cmp_a100_ss;\n')

BV_STATUS_OLD = r'''        if (devId == 0x20C2 || devId == 0x2082)
            NV_PRINTF(LEVEL_ERROR,
                      "SEC2_DEBUG: normal BooterLoad status=0x%x\n", status);
'''
BV_STATUS_NEW = r'''        if (devId == 0x20C2 || devId == 0x2082 || (devId == 0x20B5 && cmp_a100_ss != 0))
            NV_PRINTF(LEVEL_ERROR,
                      "SEC2_DEBUG: normal BooterLoad status=0x%x WPR2=0x%08x/0x%08x\n", status,
                      GPU_REG_RD32(pGpu, SEC2_DEBUG_PRI_MMU_WPR2_LO),
                      GPU_REG_RD32(pGpu, SEC2_DEBUG_PRI_MMU_WPR2_HI));
'''

BV_VERIFY_OLD = '        if ((devId == 0x20C2 || devId == 0x2082) && status == NV_OK)\n'
BV_VERIFY_NEW = (
    '        if ((devId == 0x20C2 || devId == 0x2082 ||\n'
    '             (devId == 0x20B5 && cmp_a100_ss != 0)) && status == NV_OK)\n')

# ------------------------------------------------------------------ nv-pci.c
# module_param()/MODULE_PARM_DESC() need this header; nothing in the
# nv-pci.c include chain pulls it in.
NVPCI_INC_OLD = '#include <linux/kernel.h>\n'
NVPCI_INC_NEW = '#include <linux/kernel.h>\n#include <linux/moduleparam.h>\n'
NVPCI_OLD = 'extern int NVreg_ExcludeAllGpus;\n'
NVPCI_NEW = r'''extern int NVreg_ExcludeAllGpus;

// cmpunlocker: SM-speed override for the A100-strapped CMP 170HX (10de:20b5), opt-in.
//   0  the card boots stock, SS0/SS1 keep the fuse grade (default)
//   1  open the WPR_CFG/WPR/FEAT PLMs through the SEC2 post-bootloader payload
//      and write FEATURE_OVERRIDE_SM_SPEED SS0=0x88888888 SS1=0x8 before GSP boot
//   2  control run: same PLM opens and boot flow, SS0/SS1 left alone
// Read by kernel_gsp.c during GSP boot.
int cmp_a100_ss = 0;
module_param(cmp_a100_ss, int, 0444);
MODULE_PARM_DESC(cmp_a100_ss,
    "cmpunlocker: A100-strapped CMP 170HX SM-speed unlock (0=off, 1=SS0/SS1 override, 2=control)");

// cmpunlocker: NV_PFB_PRI_MMU_LOCAL_MEMORY_RANGE for the CMP 170HX (10de:20c2, 10de:2082).
//   -1   profile value of the built-in geometry (8gb: 0x20b = 64 GiB) - the default
//   >= 0  value written to 0x00100ce0 before the GSP boot; the driver sizes the
//        firmware region and the usable FB from it (mag[9:4] << (scale[3:0] + 20)).
//        0x28b = 80 GiB for cards whose fuse burn enabled 10 FBPs.
int cmp_lmr = -1;
module_param(cmp_lmr, int, 0444);
MODULE_PARM_DESC(cmp_lmr,
    "cmpunlocker: CMP 170HX LOCAL_MEMORY_RANGE override (-1=profile 0x20b/64GiB, 0x28b=80GiB)");
'''

PARAM_PATCH_HEAD = '''cmpunlocker: module parameter cmp_a100_ss for the A100-strapped CMP 170HX

The SM-speed (FEATURE_OVERRIDE_SM_SPEED) path in sec2-postbl-plm-ss-cfg.patch
needs to be opt-in: with the parameter at its default of 0 the 10de:20b5 card
goes through the stock GSP boot, so a boot can never be wedged by it. The
experiments are done by reloading the modules with cmp_a100_ss=1 or 2.

'''


def main():
    for d in ('a', 'b', 'test'):
        rmc(W / d)
    OUT.mkdir(parents=True, exist_ok=True)

    extract(W / 'a', FILES)
    extract(W / 'b', FILES)
    root = W / 'b' / TOP
    run(['patch', '-p1', '--no-backup-if-mismatch'], cwd=root, stdin=open(GOOD_SEC2))
    run(['patch', '-p1', '--no-backup-if-mismatch'], cwd=root, stdin=open(GOOD_BV))

    # ---- kernel_gsp.c
    p = root / GSP_C
    gsp = p.read_text()
    gsp = edit(gsp,
               '#define SEC2_POSTBL_TIMING_CMP_170HX_10GB_PCI_DEVICE_ID 0x2082\n',
               '#define SEC2_POSTBL_TIMING_CMP_170HX_10GB_PCI_DEVICE_ID 0x2082\n'
               '#define SEC2_POSTBL_TIMING_A100_STRAPPED_PCI_DEVICE_ID  0x20B5\n',
               'A100 device id define')
    gsp = edit(gsp, GATE_OLD, GATE_NEW, 'gate')
    gsp = edit(gsp, MAIN_LOOP, A100_BLOCK + MAIN_LOOP, 'A100 block')
    gsp = edit(gsp, POSTW_STOCK_OLD, POSTW_STOCK_NEW, 'POST-WRITE stock reads')
    gsp = edit(gsp, POSTW_OVR_OLD, POSTW_OVR_NEW, 'POST-WRITE cmp_lmr override')
    gsp = edit(gsp, POSTW_PRINT_OLD, POSTW_PRINT_NEW, 'POST-WRITE print')
    gsp = edit(gsp, LABEL_OLD, LABEL_NEW, 'a100Done label')
    gsp = edit(gsp, DEAD_OLD, '', 'dead A100 SS block')
    p.write_text(gsp)

    # ---- kernel_gsp_tu102.c
    p = root / TU102_C
    tu = p.read_text()
    tu = edit(tu, BV_DEFINE_OLD, BV_DEFINE_NEW, 'booter-verify defines')
    tu = edit(tu, BV_STATUS_OLD, BV_STATUS_NEW, 'booter status print')
    tu = edit(tu, BV_VERIFY_OLD, BV_VERIFY_NEW, 'booter verify print')
    p.write_text(tu)

    # ---- kernel-open/nvidia/nv-pci.c
    p = root / NVPCI_C
    text = edit(p.read_text(), NVPCI_INC_OLD, NVPCI_INC_NEW, 'moduleparam include')
    p.write_text(edit(text, NVPCI_OLD, NVPCI_NEW, 'module parameter'))

    # ---- diffs against pristine
    def mkdiff(rel):
        r = run(['diff', '-Naur', '--label', 'a/' + rel, '--label', 'b/' + rel,
                 str(W / 'a' / TOP / rel), str(W / 'b' / TOP / rel)], check=False)
        if r.returncode != 1:
            sys.exit('diff for %s returned %d (expected 1 = differs)' % (rel, r.returncode))
        return r.stdout

    (OUT / 'sec2-postbl-plm-ss-cfg.patch').write_text(mkdiff(GSP_H) + mkdiff(GSP_C))
    (OUT / 'booter-verify.patch').write_text(mkdiff(TU102_C))
    (OUT / NEW_PATCH).write_text(PARAM_PATCH_HEAD + mkdiff(NVPCI_C))

    # ---- dry run: the whole new PATCH_ORDER on a fresh tree
    build_sh = (REPO / 'driver' / 'build.sh').read_text()
    m = re.search(r'PATCH_ORDER=\(\n(.*?)\n\)', build_sh, re.S)
    order = [l.strip() for l in m.group(1).splitlines() if l.strip()]
    if NEW_PATCH not in order:
        order.insert(order.index('booter-verify.patch') + 1, NEW_PATCH)
    extract(W / 'test')
    troot = W / 'test' / TOP
    bad = 0
    for name in order:
        src = OUT / name if (OUT / name).exists() else PDIR / name
        with open(src) as fh:
            r = run(['patch', '-p1', '--no-backup-if-mismatch', '--input=-'],
                    cwd=troot, stdin=fh, check=False)
        note = ' (NEW from out/)' if src.parent == OUT else ''
        print('--- %s%s rc=%d' % (name, note, r.returncode))
        print(r.stdout.rstrip())
        if r.stderr.strip():
            print(r.stderr.rstrip())
        bad += r.returncode
    rej = list(troot.rglob('*.rej'))
    print('\nPATCH_ORDER (%d): %s' % (len(order), ' '.join(order)))
    print('rejected hunks files: %d, patch rc sum: %d' % (len(rej), bad))
    gsp_test = (troot / GSP_C).read_text()
    for needle in ('cmp_a100_ss', 'cmp_lmr', 'a100Done:;', 'A100 WPR2 restore', 'extern int cmp_a100_ss;'):
        print('  kernel_gsp.c has %-24r : %s' % (needle, needle in gsp_test))
    print('  nv-pci.c has module_param(cmp_a100_ss : %s' %
          ('module_param(cmp_a100_ss' in (troot / NVPCI_C).read_text()))
    return 1 if (rej or bad) else 0


if __name__ == '__main__':
    sys.exit(main())
