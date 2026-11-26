# cmpunlocker-kernel-modules — GPU-модули переживают обновление ядра

Узел: `pve` (192.168.1.11), Proxmox VE 9.2.24, 3× **NVIDIA CMP 170HX** (GA100, `10de:20c2`).

## 1. Зачем это нужно

Карточки разблокированы (64 GiB VRAM вместо 8 GiB, PCIe Gen2) кастомной сборкой
модулей **cmpunlocker** из `/root/cmpunlocker-0.5` — это пропатченный
`open-gpu-kernel-modules 615.71.09`. Модули скомпилированы под конкретное ядро:
у них жёсткий `vermagic`.

Следствие: `apt dist-upgrade` ставит `proxmox-kernel-7.0.<новая-ревизия>`, а
`/lib/modules/<новая>/updates/cmpunlocker/` у неё пустой (или его нет). При
загрузке на такое ядро:

- `nvidia` не привязывается к PCI-устройствам → нет `/dev/nvidia*`;
- `nvidia-smi` не работает → пустая панель GPU в Summary;
- CT `101 qwen38fn` с `lxc.mount.entry: /dev/nvidiaN ...` стартует **без** карт
  (опция `optional`), и инференс падает внутри контейнера, а не на хосте.

Сам установщик cmpunlocker об этом пишет: *«You will need to re-run this script
after each kernel upgrade»* — но руками это забывается. Скрипт из этого
каталога делает это автоматически.

**Вторая, более важная половина — патченое ядро.** Рабочее ядро здесь не сток,
а `7.0.14-26-z2p-pve` (локальная сборка из `/root/kernel-p2p/pve-kernel`,
`KREL_EXTRA=-z2p`) с двумя патчами из `patches/kernel/`:

- `9901` — расчёт PCI-окон мостов без выравнивания детей → картам молча не
  хватало адресного пространства;
- `9902` — early-quirk: REBAR на 32 GB **до** нумерации шины, по одной карте.

Поэтому данный хук решает только задачу «модули под новое ядро». Он **не**
делает сток-ядро пригодной целью загрузки — `--check` честно показывает
разницу в колонке `BAR1`, а полная процедура после апдейта Proxmox описана в
`/root/CMP170HX-README.md`. Дополнительная ловушка: карта, получившая BAR1 =
0 MB, не оживает ни `rmmod`-циклами, ни unbind/rescan, ни перезагрузкой —
нужен обесточивание на ~15 s (кнопка питания не помогает, 5VSB живёт).

Нюанс про сам билдер: `driver/patches/bar1-resize-unlock.patch` сейчас — это
**LOG-ONLY v3** (байт-в-байт `/root/bar1-v3-logonly.patch`, проверено `diff -q`),
то есть только логирует размер BAR1, а не ресайзит его. Хук вызывает именно
`driver/build.sh`, значит подхватывает это состояние корректно. Ресайз-вариант
возвращать нельзя: он ломает раскладку quirk'а (карта получает 0 MB).

## 2. Что установлено

| Путь | Что это |
|---|---|
| `/usr/local/sbin/cmpunlocker-kernel-modules` | движок: поиск ядер без модулей → сборка → установка → верификация |
| `/etc/kernel/postinst.d/99-cmpunlocker-modules` | триггер: его дёргает пакет ядра при установке (рядом штатные `initramfs-tools`, `zz-proxmox-boot`, `zz-update-grub`) |
| `/etc/systemd/system/cmpunlocker-modules.service` | unit для фоновой сборки (`Type=oneshot`, `Nice=10`) |
| `/etc/systemd/system/cmpunlocker-modules.timer` | страховка: `OnBootSec=10min`, `OnUnitActiveSec=6h`, `Persistent=true` |
| `/var/log/cmpunlocker-kernel-modules.log` | общий лог (и служебные строки, и вывод `make`) |
| `/run/lock/cmpunlocker-modules.lock` | `flock` — параллельные запуски (хук + таймер) не дерутся |
| `/usr/share/doc/cmpunlocker-kernel-modules/README.md` | этот файл |
| `/var/lib/cmpunlocker-hook/` | scratch для сборки `cmp_no_bus_reset.ko` (по ядру на подкаталог) |

Локальные копии исходников: `/home/givis/pve-cmpunlocker-hook/` (на рабочей машине).

## 3. Как это работает

Триггеры:

1. **Пакет ядра** → `/etc/kernel/postinst.d/99-cmpunlocker-modules <kver> <zimage>`.
   Хук не собирает сам (иначе встал бы на 1–2 минуты посреди `dpkg`) и не ждёт
   apt-лока: он отдаёт работу `systemd-run` во временный unit
   `cmpunlocker-modules-<kver>.service` и сразу выходит с `0`. Не `*-pve` ядро —
   игнор.
2. **Таймер** раз в 6 часов и через 10 минут после каждого старта системы —
   лечит случаи, когда сборка не успела (оборвалась, ребутнулся раньше, не было
   headers, был занят dpkg).

Что происходит на каждый таргет-ядра `<kver>`:

```
needs_build?  есть ли /lib/modules/<kver>/updates/cmpunlocker/*.ko
              и совпадает ли их vermagic с <kver>          -> нет: «already in place»
ensure_headers? /lib/modules/<kver>/build существует
              нет: apt-cache policy proxmox-headers-<kver>  (фолбэк linux-headers-<kver>)
                   занят dpkg-lock -> выход, попробует таймер
build_nvidia  генерирует .generated-by-hook-<kver>.sh из /root/cmpunlocker-0.5/driver/build.sh,
              меняя ровно две вещи:
                • KVER="<kver>"            (в оригинале захардкожено несуществующее 7.0.14-25-p2p-pve)
                • режет хвост, начиная с info "Attempting to unload NVIDIA modules..."
                  (тот хвост останавливает nvidia-persistenced и делает modprobe -r —
                   на живом узле с CUDA-контекстами это недопустимо)
              затем: распаковка tarball -> 11 unlock-патчей -> профиль 8gb (CFG1/LMR/FB)
                    -> make -j$(nproc) modules SYSSRC=/lib/modules/<kver>/build
                    -> install в updates/cmpunlocker/ -> depmod -a -> update-initramfs -u
              CMPUNLOCKER_GPU_INVENTORY передаётся из уже собранного ядра,
              чтобы gpu_inventory не остался пустым
build_no_bus_reset  компирует driver/passthrough/cmp_no_bus_reset.c в свой scratch-каталог
                    (исходники -25 не затирает) и ставит .ko рядом
verify              vermagic == <kver> у 6 модулей, наличие driver_version/card_profile/
                    unlock_geometry/gpu_inventory, и modinfo -k <kver> nvidia -n
                    обязан указать путь .../updates/cmpunlocker/nvidia.ko
```

После этого **ничего не перезагружается**: новый набор лежит на диске и
подхватится при следующей загрузке на `<kver>`. Модули цепляются по generic
PCI-алиасу из `nvidia.ko` — `pci:v000010DEd*sv*sd*bc03sc02i00*` (CMP 170HX —
class `0302`), поэтому отдельного `modules-load.d` не нужно; `depmod` его и
регистрирует.

## 4. Проверка состояния

```bash
/usr/local/sbin/cmpunlocker-kernel-modules --check
```

```
KERNEL                   MODULES    HEADERS  BAR1            NOTE
7.0.2-6-pve              OK         yes      stock/lottery
7.0.14-25-pve            OK         yes      stock/lottery
7.0.14-26-pve            OK         yes      stock/lottery
7.0.14-26-z2p-pve        OK         yes      z2p quirk
driver (userspace): 615.71.09
running kernel: 7.0.14-26-z2p-pve
  BAR1 REBAR lines in dmesg: 3 (want one per card), final size lines: 3
note: rc counts module problems only; "stock/lottery" = usable as fallback, not as target
```

`MODULES` — `OK`/`MISSING`, `BAR1` — есть ли у ядра quirk (`*-z2p-pve`) или это
сток-лотерея, `NOTE` — чем именно плохо (какой модуль, `VERMAGIC`, `RESOLVER`).
**Код возврата = число ядер с проблемами по модулям**; `stock/lottery` в rc не
считается (иначе таймер вечно показывал бы `failed` из-за fallback-ядер).
`--check` read-only и не ждёт lock.

Ручная проверка «что реально загрузится на это ядро» без сборки:

```bash
modinfo -k 7.0.14-26-pve nvidia -n          # путь, который возьмёт modprobe
modinfo -F vermagic /lib/modules/7.0.14-26-pve/updates/cmpunlocker/nvidia.ko
ls -l /lib/modules/7.0.14-26-pve/updates/cmpunlocker/
```

После загрузки на новое ядро:

```bash
uname -r
nvidia-smi --query-gpu=index,name,memory.total --format=csv   # 3× 65536 MiB
dmesg | grep -E "SEC2_DEBUG|cmp_no_bus_reset"                 # unlock сработал
systemctl is-active nvidia-persistenced cmpunlocker-passthrough gen2
```

## 5. Руками

```bash
cmpunlocker-kernel-modules --kernel 7.0.14-27-pve --install-headers   # конкретное ядро
cmpunlocker-kernel-modules --force --kernel 7.0.14-26-pve             # пересобрать поверх
cmpunlocker-kernel-modules --kernel 7.0.14-26-pve --dry-run           # что сделает
cmpunlocker-kernel-modules                                            # = --missing --install-headers
systemctl start cmpunlocker-modules.service                           # то же самое через systemd
journalctl -u cmpunlocker-modules.service -n 50                       # вывод последнего прогона
```

Сборка одного ядра: ~75 s на 36 ядрах, пик ~3.4 GB RAM, в логе — шум от
`objtool: 'naked' return ...` (норма для этих исходников).

## 6. Если не собралось

Сначала — `grep -E "^20[0-9-]{8} " /var/log/cmpunlocker-kernel-modules.log | tail -20`
(служебные строки помечены датой, вывод `make` между ними).

| Симптом в логе | Причина / что делать |
|---|---|
| `no headers package found for <kver>` | пакета `proxmox-headers-<kver>` ещё нет в репе → `apt-get update`, потом `--kernel <kver> --install-headers` |
| `dpkg is busy, will retry on the next run` | хук поймал момент установки; следующий запуск таймера закроет вопрос |
| `userspace driver X is not in .../VERSION - refusing to guess` | обновили драйвер NVIDIA на версию, которой нет в `/root/cmpunlocker-0.5/driver/VERSION` → нужен новый cmpunlocker/патчи; скрипт намеренно не угадывает версию |
| `cannot determine the installed NVIDIA userspace version` | нет ни `/proc/driver/nvidia/version`, ни `nvidia-smi`, ни референсного `driver_version` → загрузи любое собранное ядро или поправь `CMPUNLOCKER_REPO` |
| `BUILD FAILED` / `VERIFY FAILED` | смотри полный `make` в логе; типовые причины — новый я-API, на который не лёг unlock-патч (`patch` даёт `.rej` в дереве), или отсутствующий `gcc`/`patch`/`python3` |
| `another run holds /run/lock/... - nothing to do` | уже идёт сборка — это нормально, не трать время |

Аварийный откат: `systemctl disable --now cmpunlocker-modules.timer` +
`rm /etc/kernel/postinst.d/99-cmpunlocker-modules` — ядро тогда грузится без
GPU, но в GRUB всегда есть `7.0.14-25-pve` с рабочими модулями.

## 7. Границы (важно)

- **Модули ≠ готовое ядро.** Хук не собирает и не патчит ядро: после
  `dist-upgrade` со стоковым ядром он сделает его загрузкопригодным
  «технически», но BAR1 останется лотереей. Порядок: сначала пересобрать
  `-z2p` (рецепт в `/root/CMP170HX-README.md`), потом уже модули.
- Для **локально собранных** ядер (`*-z2p-pve`) пакета headers в apt нет —
  `apt-cache policy` даёт «no headers package found», и хук корректно
  отказывается до момента, когда `proxmox-headers-<ver>-z2p-pve.deb`
  поставлен руками (`dpkg --force-conflicts -i`, затем `dpkg --configure -a`, если
  симлинк `build` остался `.dpkg-new`).
- **Не ребутит и не трогает загруженный драйвер.** Проверка «ядро готово» =
  файлы + `depmod`, а не живой `nvidia-smi`.
- Модули **не подписаны** — сейчас `SecureBoot disabled` (Platform in Setup Mode),
  поэтому ок. Если включишь Secure Boot, потребуются свои signing keys через
  `mokutil`/`kmodsign`, иначе `modprobe nvidia` получит `Key was rejected`.
- Скрипт считает, что исходники лежат в `/root/cmpunlocker-0.5` и юзерспейс
  `615.71.09` установлен (`.run`). Перенос/обновление драйвера — правь
  `REPO_DRIVER` (или через `CMPUNLOCKER_REPO=...`) и список `driver/VERSION`.
- Конкурентные сборки одного ядра исключены lock'ом, но `.build/`-дерево
  источников **общее**: два разных ядра собираются строго последовательно.
- `update-initramfs` для нового ядра вызывается из билдера; в initramfs модули
  nvidia не нужны (загрузка идёт после монтирования root), так что это
  косметика.

## 8. Связанное: панель GPU в Summary

Панель — отдельный самописный механизм, `/usr/local/sbin/pve-gpu-panel`
(API `GET /nodes/<node>/gpu` через `PVE::API2::Gpu` + инжект `PVE.node.GpuView`
в `pvemanagerlib.js`; после апдейта `pve-manager` его накатывает обратно apt-хук
`/etc/apt/apt.conf.d/99-pve-gpu-panel`).

Симптом «карточки пропали из Summary» при живом `nvidia-smi` почти всегда
означает устаревший индекс API в `pveproxy`: запрос `/api2/json/nodes/pve/gpu`
возвращает `200` и ~94 байта `{"message":"Method 'GET /nodes/pve/gpu' not
implemented"}`, а JS при ошибке прячет панель (`setVisible(false)`). Лечится
`systemctl restart pveproxy` (одного `reload`/HUP недостаточно, и `pvesh` этого
не покажет — он грузит API в своём процессе). Проверять надо HTTP-путём, т.к.
`pvesh get /nodes/pve/gpu` может работать, пока веб-морда ничего не получает.

## 9. Что реально проверялось

- `27.10.2026 20:43` — исходный баг: после `dist-upgrade` `pve-manager 9.2.21→9.2.24`
  панель опустела, `/gpu` отдавал `not implemented` (94 байта); починено
  `systemctl restart pveproxy`.
- `27.10.2026 20:54` — сервис собрал `7.0.2-6-pve` с нуля за 75 s (проверка конвейера
  на неиспользуемом ядре).
- `27.10.2026 20:56` — симуляция вызова от пакета ядра:
  `99-cmpunlocker-modules 7.0.2-6-pve /boot/vmlinuz-7.0.2-6-pve` → временный unit
  отработал и удалился; не-`pve` ядро проигнорировано.
- `27.10.2026 ~21:00` — **настоящий ребут на `7.0.14-26-pve`** (модули, собранные
  вручную по той же рецептуре). Итог через 135 s после `reboot`:
  `nvidia-smi` → 3× `CMP 170HX`, `memory.total = 65536 MiB`, `driver 615.71.09`,
  `pcie.link.gen.current = 2`; `srcversion` загруженного `nvidia` совпал с
  установленным под `-26`; в `dmesg` есть `SEC2_DEBUG ... CMP 170HX` и
  `cmp_no_bus_reset: NO_BUS_RESET`; `pct exec 101 -- nvidia-smi` видит все 3 карты
  по 65536 MiB; `GET /api2/json/nodes/pve/gpu` по API-токену вернул полный
  список; `systemctl --failed` пуст.
- Не проверено в бою: сборка, запущенная реальным `apt` при установке нового ядра
  (первый же `dist-upgrade` с `7.0.14-27-pve` это покажет — смотри
  `journalctl -u 'cmpunlocker-modules-*'`).

### Реальный бой: твоя сборка `7.0.14-26-z2p-pve`

Хук сработал сам при установке локального ядра и повёл себя правильно:

```
22:09:32 === run: kernels '7.0.14-26-z2p-pve' driver=615.71.09 ===
22:09:32   no headers package found for 7.0.14-26-z2p-pve
22:09:32 7.0.14-26-z2p-pve: NOT READY
```

Модулей не было, headers-пакета в apt тоже нет (ядро локальное) — хук не стал
ничего выдумывать и отложил работу; ты доставил headers `.deb` и собрал модули
сам (mtime 22:16–22:17), после чего `--check` показывает `OK / z2p quirk`.

Попутно этот случай вскрыл баг в определении версии юзерспейс-драйвера: в момент,
когда драйвер не отвечает, фолбэк на `nvidia-smi` возвращает текст ошибки, и он
принимался за версию:

```
22:13:00 userspace driver NVIDIA-SMIhasfailedbecause... is not in driver/VERSION
```

Исправлено (валидация формата `^[0-9]+\.[0-9]+\.[0-9]+$` + фолбэк на
`driver_version` уже собранных ядер), регресс-тест: в mount namespace с закрытым
`/proc/driver` и заглушкой `nvidia-smi` версия по-прежнему `615.71.09`, `rc=0`.

Замечание на будущее: `pct stop 101 102 --timeout 180` не сработал — `pct stop`
не знает такой опции и вернул `400 unable to parse option`, так что контейнеры
ушли не штатно, а вместе с ребутом хоста. Правильно — `pct stop` без `--timeout`.
