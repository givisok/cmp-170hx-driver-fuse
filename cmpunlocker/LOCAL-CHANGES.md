# Чем это дерево отличается от upstream

База: [amoghmunikote/cmpunlocker](https://github.com/amoghmunikote/cmpunlocker) **v0.5**
(тег `v0.5`, коммит `17535a0`, 2026-10-05), драйвер open-gpu-kernel-modules 615.71.09.
Всё, что не перечислено ниже, — байт-в-байт как в upstream (включая `README.md`, `install.sh`,
`LICENSE`, `docs/`, `tests/`, `tools/`).

## Изменённые файлы

| Файл | Что изменено |
|---|---|
| `common/constants.yaml` | Добавлен профиль памяти **`80gb`** (`stock 8192 → unlocked 81920 MiB`, `cfg1 0x02779000`, `lmr 0x0000020B`, `fb_bytes 0x0000001400000000`, label 80GB); заодно YAML перезаписан без кавычек (смысл тот же). |
| `driver/build.sh` | Профиль по умолчанию `8gb` → **`80gb`**; маркер проверки геометрии `0x1000000000ULL` → `0x1400000000ULL`; в `PATCH_ORDER` добавлены `a100-ss-param.patch`, `0011-p2p-bar1.patch`, `0013-skip-mailbox-peer-preinit.patch`, `0015-bar1p2p-readcap-override.patch`; убраны `ecc-enable.patch`, `ecc-fbpa-static.patch`, `ecc-reporting.patch` (файлы остались в `patches/`, но не применяются). |
| `driver/patches/pcie-gen2.patch` | Gen2 → **Gen3**: `PCIE_GEN2_LINK_SPEED = 0x3`, `LINK_CONFIG_0[19:18] = 1` в обоих местах (кодирование обратное: 0 = Gen4, 1 = Gen3, 2 = Gen2). |
| `driver/patches/bar1-resize-unlock.patch` | Upstream-версия на probe сама разворачивала REBAR до 64 GB — на этом стенде это ломало раскладку quirk'а ядра (карта получала 0 MB). Заменена на **log-only**: печатает `CMP BAR1: final size = N MB`, ничего не ресайзит. |
| `driver/patches/sec2-postbl-plm-ss-cfg.patch` | Перегенерирован под дерево 615.71.09 (номера строк хунков у upstream другие). Геометрия для `10de:20c2`: 64 GiB → **80 GiB** (`targetFbBytes = 0x1400000000`), ветка `cfg1/lmr` как у 8gb-профиля. Добавлен путь для **A100-зашитой карты** (`10de:20b5`): гейт `_kgspSec2PostblTimingEnabled` зависит от `cmp_a100_ss`, «A100-блок» открывает PLM `WPR_CFG` → `WPR` → `FEAT`, восстанавливает WPR2, пишет SS0/SS1, пересобирает подпись и уходит на загрузку GSP мимо CMP-шных CFG1/LMR/SM-RECONFIG/PCIe. Подробности — `../docs/a100-sm-speed-unlock.md`. В CMP-ветке параметр `cmp_lmr` может подменить `NV_PFB_PRI_MMU_LOCAL_MEMORY_RANGE` (и печатаются стоковые CFG1/LMR) — `../docs/memory-80g-cmp.md`. |
| `driver/patches/booter-verify.patch` | Для `10de:20b5` печатаются код завершения загрузчика и WPR2; при успехе — верификация PLM/SS0/SS1/CFG1/LMR. |

## Добавленные файлы

| Файл | Что это |
|---|---|
| `driver/patches/a100-ss-param.patch` | Объявляет параметры модуля в `kernel-open/nvidia/nv-pci.c`: `cmp_a100_ss` (`0` — сток, `1` — SM-скорость, `2` — контроль) и `cmp_lmr` (`-1` — профильное `LOCAL_MEMORY_RANGE` = 64 GiB, `0x28b` = 80 GiB для карт с 10 FBP). |
| `driver/patches/0011-p2p-bar1.patch`, `0013-skip-mailbox-peer-preinit.patch`, `0015-bar1p2p-readcap-override.patch` | BAR1-P2P (перенесены с ветки bayley). Собираются, но на этом железе (X99 + PLX) P2P не работает; NCCL ходит через SHM. `patches/p2p-parked/` — байт-в-байт те же три файла (копия на случай, если их снова уберут из `PATCH_ORDER`). |
| `driver/build-26.sh` | Прежняя копия билдера с жёстким `KVER="7.0.14-26-pve"`. Историческая, не используется. |

## Как получены патчи для A100

`../a100/mkpatches.py`: берёт pristine-тарбол 615.71.09, накладывает known-good версии (sec2 —
рабочая, booter-verify — upstream v0.5), делает
правки по точным якорям и снимает `diff -Naur`. Поэтому заголовки хунков всегда верные.
