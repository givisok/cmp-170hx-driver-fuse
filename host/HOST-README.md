# CMP 170HX × 3 — эксплуатационная документация (Proxmox pve, 192.168.1.11)

Обновлено: 2026-11-16. Поддерживается вместе с агентом ZCode (память: proxmox-cmp170hx-cmpunlocker).

## Текущее рабочее состояние

| Компонент | Значение |
|---|---|
| PVE | pve-manager **9.2.28** (обновлён 2026-11-16 вместе с ядром, без инцидентов) |
| Ядро (default) | **7.0.14-29-z2p-pve** — собственная сборка, грузится автоматически (суффикс `-z2p` сортируется выше стока); fallback = 7.0.14-28-z2p-pve (GRUB Advanced, модули на месте) |
| Драйвер | nvidia-open **615.71.09** userspace + модули cmpunlocker v0.5 (патчи под наше железо) |
| Карты | 3× CMP 170HX (10de:20c2, шины 05/06/09, за PLX PEX8747) = **3× 65536 MiB**, PCIe Gen2 |
| BAR1 | 3× **32GB статично** — выдаётся quirk'ом ядра ДО нумерации шины (лотереи раскладки нет) |
| CUDA | контексты и аллокации проверены (persistence mode ОБЯЗАТЕЛЕН — сервис nvidia-persistenced) |
| Сервисы | nvidia-persistenced.service, cmpunlocker-passthrough.service, gen2.service, cmpunlocker-modules.timer (6ч) |
| vLLM | CT **101** (qwen38fn, onboot=1) + CT 102 (hermes). После ребута хоста vLLM поднимается сам ~3-5 мин |

Проверка за 10 секунд:

```bash
nvidia-smi --query-gpu=index,pci.bus_id,memory.total,pcie.link.gen.current --format=csv
dmesg | grep "BAR1 REBAR"   # должно быть ×3: "programmed to 32GB before enumeration"
dmesg | grep "final size"   # ×3: 32768 MB
```

## Как всё устроено

1. **Ядро z2p** содержит два наших патча (в /root/kernel-p2p/pve-kernel/patches/kernel/):
   - `9901` — фикс ядра: расчёт PCI-окон мостов не учитывал выравнивание детей → картам молча не хватало адресного пространства;
   - `9902` — early-quirk: каждой CMP 170HX разворачивается REBAR на 32GB до нумерации шины.
   Без них (на стоковых ядрах) раздача BAR1 — лотерея загрузки: может быть 3/3, 2/3 или 1/3 карт.
2. **Драйверный bar1-патч — LOG-ONLY v3** (`/root/bar1-v3-logonly.patch`): НЕ ресайзит BAR (ресайз ломал quirk-раскладку, карта получала 0 МБ и умирала). Работает ТОЛЬКО в паре с z2p-ядром.
3. **Авто-обновление модулей**: хук `/etc/kernel/postinst.d/99-cmpunlocker-modules` + таймер (6ч) + скрипт `/usr/local/sbin/cmpunlocker-kernel-modules` — сами пересобирают NVIDIA-модули под новые ядра. Проверка: `cmpunlocker-kernel-modules --check`.
4. **Persistence**: без него CUDA мертва (cuCtxCreate=OOM) — сервис nvidia-persistenced держит карты.

## ⚠️ При обновлении Proxmox (новое ядро 7.0.15+)

Новое стоковое ядро станет дефолтом и ВЕРНЁТ лотерею BAR1. Нужно пересобрать z2p:

```bash
cd /root/kernel-p2p/pve-kernel
git fetch origin; git checkout origin/master        # свежие исходники
# патчи 9901/9902 лежат в patches/kernel/ (untracked, переживают checkout) — проверить ls
sed -i 's/^KREL_EXTRA=.*/KREL_EXTRA=-z2p/' Makefile
git submodule update --init --depth 1 submodules/ubuntu-kernel submodules/zfsonlinux
(cd submodules/zfsonlinux && git submodule update --init --depth 1)
make build-dir-fresh
mk-build-deps -ir -t "apt-get -y -o Debug::NoLocking=1" proxmox-kernel-7.0.*/debian/control 2>/dev/null || \
  mk-build-deps -ir -t "apt-get -y -o Debug::NoLocking=1" proxmox-kernel-$(make print-KERNEL_VER 2>/dev/null)/debian/control
nohup make deb > build.log 2>&1 &        # ~50 минут
# затем:
dpkg -i proxmox-kernel-<ver>-z2p-pve_*.deb
dpkg --purge proxmox-headers-<ver>-z2p-pve 2>/dev/null; dpkg --force-conflicts -i proxmox-headers-<ver>-z2p-pve_*.deb
dpkg --configure -a                        # если симлинк build остался .dpkg-new
reboot                                     # z2p снова дефолт
# после загрузки: cd /root/cmpunlocker-0.5 && ./install.sh   (модули под новое имя ядра)
```

Быстрая проверка перед сборкой: `grep ^KVER= /root/cmpunlocker-0.5/driver/build.sh` — должно быть `KVER="$(uname -r)"` (без хардкодов!).

### Ловушки, подтверждённые при обновлениях 2026-11-06 (28-z2p) и 2026-11-16 (29-z2p)

1. **Грязный Makefile**: после прошлой сборки `KREL_EXTRA=-z2p` висит в Makefile → `git checkout -- Makefile` ПЕРЕД `git checkout origin/master`, иначе checkout откажется.
2. **mk-build-deps**: путь к control — `proxmox-kernel-7.0.14/debian/control` (каталог БЕЗ патчлевела версии; `proxmox-kernel-7.0.14.29/` не существует). Работает glob `proxmox-kernel-7.0.*/debian/control`.
3. **lintian хвостит**: после записи нужных deb (kernel+headers готовы!) lintian проверяет 126-мегабайтный пакет ~20 мин (процесс R, не вис) — можно не ждать, пакеты уже на диске.
4. **Хук модулей НЕ срабатывает при `dpkg -i` ядра z2p** (headers ставятся позже с --force-conflicts) → модули MISSING. Перед ребутом ОБЯЗАТЕЛЬНО: `cmpunlocker-kernel-modules --kernel <новое-z2p-имя>` (~70 c, «ready for the next boot») и проверить `find /lib/modules/<имя>/updates -name nvidia.ko`.
5. **⚠️ apt-get check = "Unmet dependencies" ПОСЛЕ установки z2p-headers с --force-conflicts** — это конфликт метапакета `proxmox-kernel-7.0-build-deps` с z2p-headers. **НИКОГДА не запускать слепо `apt --fix-broken install`** — он предложит REMOVE proxmox-headers-…-z2p (поломает будущие пересборки модулей). Лечение: `apt-get remove proxmox-kernel-7.0-build-deps` (метапакет, mk-build-deps пересоздаст при следующей сборке) → `apt-get check` чистый.
6. **Сток-headers нового ядра из репо НЕ ставятся** (unmet с z2p-headers) → сток-ядро остаётся без NVIDIA-модулей (это лотерейное ядро, так и надо). Поэтому **НЕ сносить прошлый z2p-пакет** — он рабочий fallback в GRUB Advanced (28-z2p с модулями).
7. **install.sh после загрузки НЕ нужен**, если модули собрал cmpunlocker-kernel-modules под новое ядро (юзерспейс не менялся). Он нужен только при смене версии драйвера — и тогда проверить cmp-pcie-gen2.conf (перезаписывает).

Хронология обновлений: 11-06 → pve 9.2.26 + 28-z2p; 11-16 → pve 9.2.28 + 29-z2p (даунтайм vLLM ~4 мин, карты/BAR1/persistence — без замечаний).

## Известные ловушки

- **Зависшая карта** (после неудачного GSP-старта: `GPU failed probe-time init` / BAR1 0M) не поднимается ни rmmod-циклами, ни unbind/rescan. Лечит только обесточивание: кнопка питания НЕ помогает (дежурные 5VSB живы) — выдернуть из розетки на ~15 сек.
- **nvidia-smi на мёртвых картах виснет** и ломает shutdown — не запускать, диагностика по dmesg.
- **install.sh перезаписывает** /etc/modprobe.d/cmp-pcie-gen2.conf — после каждого install проверять содержимое (должна быть строка `RmForceEnableGen2=1;RMPcieLinkSpeed=0x1`).
- Прерванный `dpkg -i` headers оставляет пакет «in a mess» и симлинк build.dpkg-new → `dpkg --purge` + повторная установка + `dpkg --configure -a`.
- Python-heredoc на хосте — только с кавычками `<<'PYEOF'`, иначе `\n` в C-строках превращается в перевод строки.
- SSH-BatchMode скрипты: `pkill -f <строка>` убивает собственную ssh-сессию, если строка есть в команде.
- GRUB: править `GRUB_CMDLINE_LINUX_DEFAULT` только заменой всей строки (порядок слов меняется). Сейчас там: `quiet pci=realloc` (realloc нужен для раскладки BAR).

## P2P между картами — статус: НЕ РАБОТАЕТ на этом железе

Проверены все пути (2026-10-22..23, 5 комбинаций — все вешали холодную загрузку): драйверы 615.71.09/610.57.04/610.43.02 + патчи bayley BAR1-P2P (0011/0013/0015) + regkey ForceP2P/без + IOMMU on/off. Mailbox-путь (ветка amogh P2P + trap31 PLM 0x0012277c) открывает флаги canAccessPeer=YES, но убивает CUDA-контексты — партия карт не принимает (подтверждено автором репо в коммите 14b38f7). Работает у других на серверных платах (gist ropuls, satspace-cpu/cmp170hx-linux-p2p, bayley issue #1) — на X99+PLX+3 карты нет.

Заделы на диске: p2p-ядро 7.0.14-25-p2p-pve (GRUB Advanced), ветка /root/cmpunlocker-p2p, .run'ы 610.43.02/610.57.04/615.71.09 в /root, тесты /root/p2p_probe.py и /root/p2p_check.py, патчи в driver/patches/p2p-parked/. Возобновлять только после апдейтов авторов (Discord cmpunlocker) + serial console лог зависания.

## Ключевые каталоги

- `/root/cmpunlocker-0.5/` — рабочее дерево cmpunlocker (запускать `./install.sh`; патчи в driver/patches/)
- `/root/kernel-p2p/pve-kernel/` — исходники ядра Proxmox + наши патчи + собранные deb
- `/root/NVIDIA-Linux-x86_64-615.71.09.run` (+ 610.57.04, 610.43.02) — юзерспейс-драйверы
- `/usr/local/sbin/cmpunlocker-kernel-modules` — хелпер синхронизации модулей с ядрами
- BIOS: **Above 4G Decoding = Enabled** (без него 3 карты не поднимаются вообще!)

---

## 2026-11-27: после фьюз-прожига (80GB/SM/Gen3-фьюзы) — текущее состояние

Картины: 2× CMP 170HX (05/06, 0x20c2) + ec61 перепрошита ПОЛНОСТЬЮ под A100 PCIe 80GB (09:00.0, 0x20b5, LnkCap Gen4).

**Работает:** 3× 81920 MiB (nvidia-smi); Gen3 x16 на 05/06 (ретрейн gen3.service после инициализации драйвера); A100-карта Gen3 x8; vLLM CT101 на 3 картах; SM 112/106/106 (прожиг открыл SM!).

**Загрузка теперь:** ядро z2p то же имя (пересобрано) → драйвер = /root/cmpunlocker-0.5 (80gb-профиль по умолчанию в build.sh + constants.yaml, Gen3-значения в pcie-gen2.patch, путь SS для 0x20B5 за параметром cmp_a100_ss, включён в modprobe.d — см. раздел ниже). gen3.service (был gen2.service): (1) REBAR A100-карты 128G→32G setpci 0xbc0 + remove/rescan, (2) Gen3-ретрейн 05/06 (/root/gen3_retrain.py), (3) reboot CT101 (перемонтирует /dev/nvidia* после rescan). Откат: revert патчей → builder --force → reboot.

**Известные хвосты:**
1. ~~GPU2 (A100-зашитая) считает на ~9 TFLOPS вместо ~214~~ — **РЕШЕНО** в ночь на 2026-11-27: `cmp_a100_ss=1`, теперь ~214 TFLOPS. См. следующий раздел.
2. ~~CUDA видит 63GiB на CMP-паре~~ — **РЕШЕНО** 2026-11-27: `cmp_lmr=0x28b`, теперь 79 ГиБ на всех трёх. См. раздел ниже.
3. A100-карта x8 (не x16): слот/конденсаторы лейнов 8-15; рядом GT520 жрёт x16-порт второго PLX.
4. P2P матрица GNS (NCCL и раньше ходил через SHM — не регрессия).
5. persistence на A100-карте не ставится (Unknown Error, artifact rescan-пути).

## 2026-11-27 (ночь): A100-карта — полная SM-производительность РАБОТАЕТ

**Результат:** A100-зашитая карта (09:00.0, 0x20b5) даёт ~214 TFLOPS на 8192² fp16 matmul
(было 9.2), на уровне CMP-пары. Замер после холодной загрузки в рабочей конфигурации:
212.1 / 213.7 / 214.0 TFLOPS (05 / 06 / 09).

**Что было на самом деле** (хост не «крашился» — карта просто не проходила probe,
ядро доживало до конца загрузки):
- minimal-попытка, Booter **0x5**: restore регистров WPR2 (0x1fa824/28) — обычная запись,
  а они закрыты PLM WPR_CFG (0x1fa7cc) / WPR (0x1fa7c4). В minimal-пути открывался только
  FEAT → запись молча отбрасывалась, WPR2 оставался «поднятым», настоящий booter выходил
  за 5 мс с 0x5. Рабочее состояние WPR2 — `HI == 0` (down); `kgspIsWpr2Up_TU102` смотрит
  именно на HI.
- полный CMP-цикл, **falcon halt timeout (0x65)**: зависал на первом же PCIe-запуске
  booter'а после записи CFG1/LMR + SM-RECONFIG. У A100 стоковое `LMR=0x28b`, а цикл пишет
  `0x20b` (профиль CMP) — вероятный виновник. Сама запись SS0/SS1 безвредна (проверено
  отдельно). CFG1/LMR/SM-RECONFIG на этой карте не трогать.

**Как устроено.** Module parameter `cmp_a100_ss` (kernel-open/nvidia/nv-pci.c, патч
`a100-ss-param.patch`, читается из kernel_gsp.c):
- `0` — карта грузится стоком, SM задушен (~9 TFLOPS). Default, если нет записи в
  modprobe.d: загрузка не может «зависнуть» из-за этого пути.
- `1` — рабочий режим: открыть WPR_CFG → WPR → FEAT через SEC2 post-bootloader payload
  (по одному запуску booter'а на PLM, restore WPR2 перед каждым), restore WPR2, записать
  `SS0=0x88888888` / `SS1=0x8` (0x82381c / 0x823820), rebuild stock signature, populate
  WPR meta, обычная загрузка GSP. CMP-шные CFG1/LMR, SM-RECONFIG и PCIe-секция
  пропускаются.
- `2` — контрольный прогон: то же самое без записи SS0/SS1.
- Включено: `/etc/modprobe.d/cmpunlocker-a100-ss.conf` (`options nvidia cmp_a100_ss=1`).
  Откат — удалить файл, reboot.

**Файлы:**
- `/root/a100-work/mkpatches.py` — перегенерирует `sec2-postbl-plm-ss-cfg.patch`,
  `booter-verify.patch`, `a100-ss-param.patch` из pristine-тарбола (правки с exact-match
  якорями, `diff -Naur`, в конце прогон всего PATCH_ORDER на свежем дереве). Хунки руками
  не править.
- `/root/a100-work/deploy.py` — бэкап + deploy + read-constants; `--revert` возвращает
  pre-A100 набор.
- Бэкапы: `/root/cmpunlocker-0.5-backups/pre-a100-20261127` (патчи, build.sh,
  constants.yaml), `modules-pre-a100-20261127`.
- `/root/a100-work/a100-ss-test.sh <0|1|2> [reset]` — эксперимент без ребута:
  перегружает модули, снимает лог, лог `/var/log/a100-ss-test.log`. CT101 должен быть
  остановлен. `reset` возвращает WPR2/SS0/SS1 (срабатывает только пока PLM открыты).
- `/root/a100-work/gpu_regs.py [BDF]` — WPR2 / PLM / SS0 / SS1 через BAR0 у всех трёх карт.

**Проверка после загрузки:**
```
cat /sys/module/nvidia/parameters/cmp_a100_ss            # 1
journalctl -b -k | grep -E "GPU2.*(A100|BooterLoad)"
#  A100 WPR2 restore want=0x1ffffe00/0x00000000 read=0x1ffffe00/0x00000000
#  A100 SS unlock SS0 0x00000000->0x88888888 SS1 0x00000000->0x00000008
#  normal BooterLoad status=0x0
```
Стоковое SS0 у этой карты — `0x00000000` (а не 0x02042073, как читалось раньше). Если у A100
в логе `Booter failed ... 0x5` — WPR2 остался «поднятым»: убрать опцию, reboot (reset
ставит HI=0).

**Замер:** `/root/gpubench.py` →
`pct exec 101 -- docker run --rm --runtime=nvidia --entrypoint python3 -v /root/gpubench.py:/gpubench.py:ro vllm-sm80:q38fn /gpubench.py`
(контейнер qwen-intel на время замера остановить).

**Без изменений:** A100 x8, P2P GNS (63 GiB на CMP-паре решены — см. следующий раздел).

**Загрузка проверена целиком (2026-11-27 01:30):** reboot → gen3.service (REBAR-фикс A100, ожидание
всех 3 карт, Gen3-ретрейн, рестарт CT 101) → vLLM готов без рук за 5.7 минуты (01:30:31 →
01:36:16), запрос проходит. CT 101 снова `onboot: 1` (отключали только на время замеров).

Всё, что относится к драйверу и загрузке, лежит в репозитории
https://github.com/givisok/cmp-170hx-driver-fuse (дерево cmpunlocker + отличия от upstream,
патчи ядра 9901/9902, host/, a100/, docs/). Файл 9902 на хосте поправлен: хук для 0x20b5 был
дописан без префикса `+` и молча отбрасывался (на работу не влияло — для этой карты quirk всё
равно выходит сразу, BIOS не назначает ей BAR0).

## 2026-11-27 (ночь, продолжение): CMP-пара — 79 ГиБ доступной памяти

**Результат:** CUDA/torch видит **79.3 ГиБ** на каждой из трёх карт (на CMP-паре было 63.4).
Тест памяти паттернами (`/root/a100-work/memtest.py`, 74 ГиБ на карту) — PASS на всех трёх.

**Причина.** Драйвер на GA100 берёт размер памяти из регистра `NV_PFB_PRI_MMU_LOCAL_MEMORY_RANGE`
(0x00100ce0, в патчах называется «LMR»): `LOWER_MAG` биты [9:4], `LOWER_SCALE` биты [3:0],
`fbSize = mag << (scale + 20)`. Профиль 8gb пишет `0x20b` = 32 × 2 ГиБ = 64 ГиБ — это для
непрожжённой карты (8 FBP × 8 ГиБ). После прожига у карт 10 FBP, но в регистр всё равно
писали 64. Профиль «80gb» менял только цифру для `nvidia-smi`. Правка late-pma тут ничего
не делает (только печатает «left reserved (backs WPR2)»).

**Фикс:** параметр модуля `cmp_lmr` (default `-1` = как раньше). `0x28b` = mag 40 = 80 ГиБ,
родное значение A100 80GB. Включено в `/etc/modprobe.d/cmpunlocker-a100-ss.conf`:

    options nvidia cmp_a100_ss=1 cmp_lmr=0x28b

Стоковые значения на холодной загрузке (mining-конфиг): `CFG1=0x02449000 LMR=0x288` (10 ГиБ),
патч пишет `CFG1=0x02779000 LMR=0x28b`. Откат: убрать `cmp_lmr` из строки options, reboot.

**Проверка в логе загрузки:** `journalctl -b -k | grep -E "POST-WRITE|SEC2_DEBUG_HEAP|WPR meta updated"`
→ `LMR=0x0000028b`, `fbSize=0x1400000000 wprStart=0x13f7800000`, `publicBytes=0x13d30f0000`.

**Инструменты** (`/root/a100-work/`): `cmp-reload.sh [параметр=значение…]` — перезагрузка модулей
с любыми параметрами, сама останавливает и запускает обратно vLLM (флаг `--keep-stopped` — оставить
остановленным для тестов; помнить: после явного `docker stop` контейнер не поднимется и после ребута,
пока не сделать `pct exec 101 -- docker start qwen-intel`). `memtest.py` — тест памяти, запускать в
контейнере с CUDA. `gpu_regs.py` теперь показывает и LMR.

**Без изменений:** A100 x8, P2P GNS.
