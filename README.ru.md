# cmp-170hx-driver-fuse

[English](README.md) | **Русский**

> **Сначала прочтите: карты должны быть с прожжёнными фьюзами.** Всё в этом репозитории сделано для
> карт NVIDIA CMP 170HX **после прожига OTP-фьюзов** (коммерческий прожиг; сам прожиг — не часть этого
> репозитория, и он необратим: OTP пишется один раз). Именно прожиг превращает стоковую карту
> (8 GiB, 74 SM, PCIe Gen1/Gen2) в то, что описано ниже: 80 GiB HBM2e, 106–112 SM, PCIe Gen3 и полная
> SM-производительность. На непрожжённой карте результатов, опций модуля и загрузочных фиксов из этого
> текста не будет — это другая, намного более скромная конфигурация (64 GiB, 74 SM, Gen2).

Патченный драйвер NVIDIA и загрузочные фиксы для стенда из **3× NVIDIA CMP 170HX (GA100)** после
коммерческого прожига OTP-фьюзов: 80 GiB HBM2e на каждой карте, 106–112 SM, PCIe Gen3 и полная
SM-производительность — включая карту, которую прожгли/перестращили под **A100 PCIe 80GB**.

Всё, что здесь лежит, работает на хосте Proxmox прямо сейчас (2026-11-28): nvidia-open
**615.71.09** + модули из [amoghmunikote/cmpunlocker v0.5](https://github.com/amoghmunikote/cmpunlocker)
с локальными патчами и ядро `7.0.14-29-z2p-pve` собственной сборки.

## Итог

| | GPU0 — `05:00.0` | GPU1 — `06:00.0` | GPU2 — `09:00.0` |
|---|---|---|---|
| Стряп | CMP 170HX (`10de:20c2`) | CMP 170HX (`10de:20c2`) | **A100 PCIe 80GB** (`10de:20b5`) |
| UUID | `GPU-2f74a495…` | `GPU-295351c7…` | `GPU-ec614963…` |
| Память (nvidia-smi / CUDA) | 81920 MiB / 79 GiB | 81920 MiB / 79 GiB | 81920 MiB / 79 GiB |
| SM | 112 | 106 | 106 |
| PCIe | Gen3 x16 | Gen3 x16 | Gen3 x8 (LnkCap x16) |
| Макс. SM-клок / лимит мощности | 1695 MHz / 250 W | 1695 MHz / 250 W | 1410 MHz / 300 W |
| matmul 8192² fp16 | ~212 TFLOPS | ~214 TFLOPS | **~214 TFLOPS** |

GPU2 до фикса считала в **23 раза медленнее** (9.2 TFLOPS, matmul 119 мс вместо 5.1 мс), и модель
на конвейере PP=3 постоянно ждала именно этот рэнк. А на CMP-паре программа могла занять
только 63 ГиБ из 80. После двух фиксов все три карты одинаковые: ~214 TFLOPS и 79 ГиБ.

## Что здесь лежит

```
README.md                        английская версия этого файла
README.ru.md                     этот файл: что делали, как устроено, как воспроизвести
LICENSE                          GPL-2.0 (базовый проект cmpunlocker — GPL-2.0)
cmpunlocker/                     рабочее дерево драйвера (= /root/cmpunlocker-0.5 на хосте)
  driver/patches/                все патчи против open-gpu-kernel-modules 615.71.09
  driver/build.sh                сборка модулей (профиль 80gb по умолчанию)
  common/constants.yaml          профили памяти; от него зависит валидатор read-constants.py
  LOCAL-CHANGES.md               чем это дерево отличается от upstream v0.5
kernel/                          патчи для ядра Proxmox (z2p) и правка Makefile
host/                            то, что живёт на хосте: сервисы, скрипты загрузки, modprobe.d
  HOST-README.md                 эксплуатационная документация хоста (история, ловушки, обновления)
a100/                            инструменты, которыми нашли и починили SM-скорость A100-карты
bench/gpubench.py                замер SM-производительности по картам
bench/pciebench.py               замер пропускной способности PCIe по картам (на хосте не запускался)
docs/
  a100-sm-speed-unlock.md        разбор: почему карта была медленной и как это чинится
  memory-80g-cmp.md              разбор: почему на CMP-паре было 63 ГиБ и как стало 79
  rig-and-history.md             железо, топология, хронология от стока до текущего состояния
  boot-logs/                     журналы загрузок: рабочий путь, неудачные попытки, тест перестановки карт
```

## Что мы делали

Коротко; подробно — в [docs/rig-and-history.md](docs/rig-and-history.md).

1. **Исходная точка.** 3× CMP 170HX: 8 GiB, 74 SM, PCIe Gen1/Gen2. Скан фьюзов (2026-11-14) показал,
   что часть блоков выключена SKU-сегментацией, а часть — дефектна.
2. **Драйвер под непрожжённые карты.** cmpunlocker v0.5 + локальные патчи: 64 GiB, BAR1 32 GB
   (quirk ядра 9901/9902, сборка `-z2p`), Gen2-ретрейн, автопересборка модулей под новые ядра.
3. **Прожиг фьюзов.** Сначала сами дожгли память (64 → 80 GiB), потом отдали карты в сервис
   («максимум, что возможно»): получилось 112/106/106 SM, 80 GiB везде, Gen3-фьюзы на паре 0x20c2,
   и одну карту (ec61) полностью перестращили под A100 PCIe 80GB.
4. **Пересборка под новое состояние.** Форк scrameta/cmpunlocker_fused не подошёл
   (кривой патч + Booter 0x29 на прожжённых картах). Остались на v0.5: профиль 80gb, Gen3-значения
   в `pcie-gen2.patch`, загрузочный фикс BAR1 для A100-карты (BIOS не раскладывает её BAR'ы),
   Gen3-ретрейн CMP-пары.
5. **Симптом.** Модель стала сильно медленнее: A100-карта выдаёт 9.2 TFLOPS.
6. **Диагноз.** У карты задушена SM-скорость: регистры `FEATURE_OVERRIDE_SM_SPEED`
   (SS0 `0x82381c` / SS1 `0x823820`) на стоке равны 0, а у CMP-пар патч cmpunlocker пишет туда
   `0x88888888` / `0x8`. Запись закрыта PLM, открыть его можно только через SEC2-загрузчик.
7. **Фикс.** Параметр модуля `cmp_a100_ss=1`: до загрузки GSP открываем нужные PLM'ы тем же
   механизмом, что и cmpunlocker, восстанавливаем WPR2, пишем SS0/SS1 — и карта грузится штатно.
8. **Проверка.** Холодная загрузка, замер 214 TFLOPS, vLLM на трёх картах поднимается сам.
9. **Память на CMP-паре (63 → 79 ГиБ).** Драйвер берёт размер памяти из регистра
   `NV_PFB_PRI_MMU_LOCAL_MEMORY_RANGE`, а туда писали 64 ГиБ по профилю для непрожжённых карт.
   Параметр `cmp_lmr=0x28b` (80 ГиБ, как у настоящей A100) + тест памяти паттернами на весь объём.
   Подробно — в [docs/memory-80g-cmp.md](docs/memory-80g-cmp.md).

## Как это загружается

Карты ищутся по device id, поэтому их можно ставить в любые слоты.

```
BIOS (Above 4G Decoding = Enabled)
 └─ ядро 7.0.14-29-z2p-pve
     ├─ quirk 9902: BAR1 CMP-карт → 32 GB до нумерации шины       (для A100-карты не срабатывает: BIOS
     │                                                             оставляет её BAR0 неназначенным)
     ├─ modprobe nvidia  (options nvidia cmp_a100_ss=1 cmp_lmr=0x28b)
     │    ├─ CMP-карты: полный цикл cmpunlocker (PLM, SS0/SS1, CFG1/LMR=80 GiB, SM-RECONFIG, PCIe)
     │    └─ A100-карта: не проходит первый probe — BIOS не назначил её BAR'ы
     └─ gen3.service → /usr/local/sbin/gen3-boot
          1. A100-карта: REBAR 128G → 32G (setpci 0xbc0) + remove/rescan
             → probe → путь cmp_a100_ss (PLM → restore WPR2 → SS0/SS1 → GSP)
          1a. карта так и без драйвера → перечисляется её плата PLX
              (окна мостов гасятся, корневой порт удаляется, rescan — и ядро распланировывает окна
               заново; иначе 32-битное окно платы попадает внутрь диапазона MMCONFIG)
          2. ждёт все 3 карты → Gen3-ретрейн CMP-карт (/root/gen3_retrain.py)
          3. перезапуск CT 101 (vLLM), чтобы перемонтировались /dev/nvidia*
```

## Воспроизведение

Скрипты и пути рассчитаны на раскладку upstream: драйвер в `/root/cmpunlocker-0.5`, инструменты в
`/root/a100-work`. Порядок на такой же машине:

1. **Ядро.** Патчи из `kernel/` в `patches/kernel/` исходников pve-kernel, `KREL_EXTRA=-z2p`,
   сборка `.deb` — процедура и ловушки в [host/HOST-README.md](host/HOST-README.md).
2. **Модули.** Дерево `cmpunlocker/` → `/root/cmpunlocker-0.5` (юзерспейс — NVIDIA .run той же
   версии), затем
   `cp host/cmpunlocker-kernel-modules /usr/local/sbin/ && /usr/local/sbin/cmpunlocker-kernel-modules --kernel "$(uname -r)" --force`
   — соберёт модули под текущее ядро, не трогая работающий драйвер. Хук `host/kernel-postinst/`
   и `host/systemd/cmpunlocker-modules.{service,timer}` делают то же под новые ядра.
3. **Опции модуля.** `cp host/modprobe.d/cmpunlocker-a100-ss.conf /etc/modprobe.d/`:
   `cmp_a100_ss=1` (SM-скорость A100-карты) и `cmp_lmr=0x28b` (80 ГиБ на CMP-картах после прожига;
   для непрожжённых карт `cmp_lmr` не нужен — у них реально 64 ГиБ).
4. **Загрузочные фиксы.** `cp host/gen3_retrain.py /root/`, `cp host/gen3-boot /usr/local/sbin/`,
   `cp host/systemd/gen3.service /etc/systemd/system/ && systemctl enable gen3.service`.
5. `reboot`.

## Проверка после загрузки

```bash
cat /sys/module/nvidia/parameters/cmp_a100_ss            # 1
nvidia-smi --query-gpu=index,name,memory.total,pcie.link.gen.current,pcie.link.width.current --format=csv
journalctl -b -k | grep -E "GPU2.*(A100|BooterLoad)"
#  A100 WPR2 restore want=0x1ffffe00/0x00000000 read=0x1ffffe00/0x00000000
#  A100 SS unlock SS0 0x00000000->0x88888888 SS1 0x00000000->0x00000008
#  normal BooterLoad status=0x0
journalctl -b -k | grep -E "POST-WRITE|SEC2_DEBUG_HEAP"       # CMP: LMR=0x28b, publicBytes=0x13d30f0000
journalctl -b -u gen3.service                              # finished, CT101 rebooted
grep -a "link " /var/log/gen3.log                          # скорость и ширина линка каждой карты
```

Замер SM: `bench/gpubench.py`, память: `a100/memtest.py`, PCIe: `bench/pciebench.py` (torch, внутри
контейнера с CUDA; на время теста vLLM остановить).

## Эксперименты и откат

* `a100/cmp-reload.sh [параметр=значение …]` — перезагрузка модулей без ребута с любыми параметрами
  (`cmp_lmr=0x28b` и т. п.) и снимок лога загрузки всех карт; vLLM останавливает и запускает обратно сам.
* `a100/a100-ss-test.sh <0|1|2> [reset]` — то же только для A100-карты: `0` сток (задушена),
  `1` рабочий режим, `2` контроль без записи SS.
* `a100/memtest.py <устройство>` — тест всей памяти паттернами (в контейнере с CUDA).
* Патчи `sec2-postbl-plm-ss-cfg.patch`, `booter-verify.patch`, `a100-ss-param.patch` не править
  руками: `a100/mkpatches.py` перегенерирует их из pristine-тарбола, `a100/deploy.py` раскладывает
  и валидирует (`--revert` — назад).
* Откатить только SM-разблокировку: удалить `/etc/modprobe.d/cmpunlocker-a100-ss.conf`, `reboot` —
  A100-карта снова грузится стоком (медленная, но рабочая).

## Что осталось

* A100-карта работает на **x8**. Это дефект самой карты — AC-конденсаторы на лейнах edge-разъёма;
  работают только лейны 0–7. Проверено перестановкой карт (2026-11-27/28): в слоте, где CMP всегда
  даёт x16, эта карта даёт x4, а CMP в её слоте даёт x16. Лечится только пайкой.
* P2P между картами не работает на этом железе (X99 + PLX, NCCL ходит через SHM) — история в
  [host/HOST-README.md](host/HOST-README.md).

## Лицензия и благодарности

GPL-2.0 (см. `LICENSE`), как у базового проекта.

### Что откуда взято

| Источник | Что взято | Где лежит |
|---|---|---|
| [amoghmunikote/cmpunlocker](https://github.com/amoghmunikote/cmpunlocker) **v0.5** (тег `v0.5`, коммит `17535a0`) | Всё дерево драйвера: открытие PLM через SEC2-загрузчик, запись SS0/SS1 (SM-скорость), геометрия памяти, Gen2-ретрейн, ECC, BAR1, JTAG, инструменты VFIO-пасстру, установщик. Всё, что не перечислено в [cmpunlocker/LOCAL-CHANGES.md](cmpunlocker/LOCAL-CHANGES.md), — байт-в-байт как в upstream. | `cmpunlocker/` |
| [bayley/cmpunlocker](https://github.com/bayley/cmpunlocker) | Патчи BAR1-P2P `0011`, `0013`, `0015` — как есть, байт-в-байт. Собираются, но на этом железе P2P не работает. | `cmpunlocker/driver/patches/` (копии в `p2p-parked/`) |
| [scrameta/cmpunlocker_fused](https://github.com/scrameta/cmpunlocker_fused) | Идея volatile-ретрейна и нативного P2P; `host/gen3_retrain.py` повторяет регистровую последовательность его патча ретрейна при probe со значениями Gen3. Сам форк на этих картах не заработал (кривой патч, Booter 0x29). | `host/gen3_retrain.py` |
| [matstatman/gpcprobe](https://github.com/matstatman/gpcprobe) | Эталон сканирования фьюзов: регистры `OPT_*`, по которым видно, какие блоки выключены SKU-сегментацией, а какие дефектны. | использовались при скане фьюзов 2026-11-14 |
| [sayhellotojungle/cmp170hx-ffcalc](https://github.com/sayhellotojungle/cmp170hx-ffcalc) | Сканирование фьюзов и планирование прожига — те же регистры `OPT_*`. | то же; данные скана и планы прожига — в [givisok/qwen38-flash-next-170hx-recipe](https://github.com/givisok/qwen38-flash-next-170hx-recipe), `runs/ffcalc-20261114` |
| [NVIDIA/open-gpu-kernel-modules](https://github.com/NVIDIA/open-gpu-kernel-modules) 615.71.09 | Драйвер, на который накладываются все патчи; юзерспейс — NVIDIA .run той же версии. | скачивается при сборке `cmpunlocker/driver/build.sh` |
| [Proxmox pve-kernel](http://git.proxmox.com/git/pve-kernel.git) | База ядра (`7.0.14`). | собирается с двумя патчами из `kernel/` |

### Написано на этом стенде

* Патчи ядра `9901`/`9902` (BAR1 в 32 GB до нумерации шины), загрузочный скрипт `host/gen3-boot`,
  билдер модулей `host/cmpunlocker-kernel-modules` с его хуками.
* Локальные правки дерева драйвера — список в
  [cmpunlocker/LOCAL-CHANGES.md](cmpunlocker/LOCAL-CHANGES.md). В том числе разблокировка
  SM-скорости A100-карты (`cmp_a100_ss`): она расширяет upstream-патч `sec2-postbl-plm-ss-cfg.patch`
  тем же механизмом SEC2; значения Gen3; геометрия 80 GiB и `cmp_lmr`.
* Инструменты в `a100/` и замеры в `bench/`.

### Собственные благодарности upstream

[cmpunlocker/CREDITS.md](cmpunlocker/CREDITS.md): [asm64-hooligan](https://github.com/asm64-hooligan/) — полный размер
BAR1 (64 GB); [lesj0610](https://github.com/lesj0610/) — профилирование GPU. История самой разблокировки — видео по
ссылке в [cmpunlocker/docs/HISTORY.md](cmpunlocker/docs/HISTORY.md).
