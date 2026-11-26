#!/usr/bin/env python3
"""Deploy the regenerated A100 SM-speed patches into /root/cmpunlocker-0.5.

  /root/a100-work/deploy.py           back up + deploy + validate (no build)
  /root/a100-work/deploy.py --revert  put the pre-A100 patches/config back
"""
import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path('/root/cmpunlocker-0.5')
PDIR = REPO / 'driver' / 'patches'
BUILD_SH = REPO / 'driver' / 'build.sh'
CONSTANTS = REPO / 'common' / 'constants.yaml'
OUT = pathlib.Path('/root/a100-work/out')
BACKUP = pathlib.Path('/root/cmpunlocker-0.5-backups/pre-a100-20261127')
NEW = ('sec2-postbl-plm-ss-cfg.patch', 'booter-verify.patch', 'a100-ss-param.patch')

ORDER_OLD = '    booter-verify.patch\n    late-pma.patch\n'
ORDER_NEW = '    booter-verify.patch\n    a100-ss-param.patch\n    late-pma.patch\n'
YAML_OLD = '  booter_verify:\n    patch: booter-verify.patch\n    registers: {}\n'
YAML_NEW = YAML_OLD + '  a100_sm_speed_param:\n    patch: a100-ss-param.patch\n    registers: {}\n'


def edit(path, old, new, what):
    text = path.read_text()
    if text.count(old) != 1:
        sys.exit('%s: anchor for "%s" matched %d times' % (path, what, text.count(old)))
    path.write_text(text.replace(old, new))


def validate():
    r = subprocess.run(
        ['python3', str(REPO / 'tools' / 'read-constants.py'), str(CONSTANTS),
         str(PDIR), str(BUILD_SH), '80gb'], capture_output=True, text=True)
    print('read-constants.py rc=%d' % r.returncode)
    print((r.stdout + r.stderr).strip())
    return r.returncode == 0


if '--revert' in sys.argv:
    for name in ('sec2-postbl-plm-ss-cfg.patch', 'booter-verify.patch'):
        shutil.copy2(BACKUP / 'patches' / name, PDIR / name)
    (PDIR / 'a100-ss-param.patch').unlink(missing_ok=True)
    shutil.copy2(BACKUP / 'build.sh', BUILD_SH)
    shutil.copy2(BACKUP / 'constants.yaml', CONSTANTS)
    print('reverted patches, build.sh and constants.yaml to the pre-A100 state')
    sys.exit(0 if validate() else 1)

# ---- backup of everything this is about to touch
BACKUP.mkdir(parents=True, exist_ok=True)
if not (BACKUP / 'patches').exists():
    shutil.copytree(PDIR, BACKUP / 'patches')
    shutil.copy2(BUILD_SH, BACKUP / 'build.sh')
    shutil.copy2(CONSTANTS, BACKUP / 'constants.yaml')
    print('backup written to %s' % BACKUP)
else:
    print('backup already present: %s' % BACKUP)

for name in NEW:
    shutil.copy2(OUT / name, PDIR / name)
    print('deployed %s' % name)

edit(BUILD_SH, ORDER_OLD, ORDER_NEW, 'PATCH_ORDER')
edit(CONSTANTS, YAML_OLD, YAML_NEW, 'unlocks entry')
sys.exit(0 if validate() else 1)
