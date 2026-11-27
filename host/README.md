# Что живёт на хосте

Все пути — как на хосте `pve` (192.168.1.11). Скрипты предполагают, что дерево драйвера лежит
в `/root/cmpunlocker-0.5`, а инструменты из `../a100/` — в `/root/a100-work`.

| Файл в репо | Куда ставится | Что делает |
|---|---|---|
| `gen3-boot` | `/usr/local/sbin/gen3-boot` | Фиксы после загрузки драйвера. Карты ищутся по device id (`10de:20b5` — A100-карта, `10de:20c2` — CMP), адреса слотов не важны. Шаги: (1) REBAR A100-карты 128G → 32G + remove/rescan; (2) если после этого наша карта осталась без драйвера — перечислить плату PLX под ней (окна её мостов гасятся в hardware, корневой порт удаляется, `rescan`; ядро распланировывает окна заново); (3) дождаться 3 карт, Gen3-ретрейн CMP-карт (и A100-карты, если та сама не дошла до Gen3); трогаются только карты с драйвером; (4) рестарт CT 101. В лог `/var/log/gen3.log` пишется и скорость/ширина линка каждой карты. Откат: `/root/backup-idbased-20261127/` (адресная версия под раскладку до 2026-11-27). |
| `systemd/gen3.service` | `/etc/systemd/system/` | `After=multi-user.target nvidia-persistenced.service`, oneshot, `TimeoutStartSec=300`. |
| `gen3_retrain.py` | `/root/gen3_retrain.py` | Volatile Gen3-ретрейн одной карты (`gen3_retrain.py 0000:05:00.0`): читает фьюз-гейты, чистит `CYA_0` bit 2 (DIS_G2), ставит `LINK_CONFIG_0[19:18]=1` (Gen3; кодирование обратное: 0 = Gen4, 1 = Gen3, 2 = Gen2), `LNKCTL2` target = 8.0 GT/s на карте и upstream-мосту, дёргает retrain. Всё сбрасывается при обесточивании. |
| `modprobe.d/cmpunlocker-a100-ss.conf` | `/etc/modprobe.d/` | `options nvidia cmp_a100_ss=1 cmp_lmr=0x28b`: SM-скорость A100-карты и 80 ГиБ на CMP-паре (`NV_PFB_PRI_MMU_LOCAL_MEMORY_RANGE`). Удалить файл + reboot = откат обеих опций. |
| `modprobe.d/cmp-pcie-gen2.conf` | `/etc/modprobe.d/` | `NVreg_RegistryDwords="RmForceEnableGen2=1;RMPcieLinkSpeed=0x5"` — остался от эпохи Gen2, лежит как есть; Gen3 сейчас делается патчами драйвера и `gen3_retrain.py`. |
| `modprobe.d/cmpunlocker-vfio.conf` | `/etc/modprobe.d/` | `vfio-pci disable_idle_d3=1` — от экспериментов с passthrough. |
| `cmpunlocker-kernel-modules` | `/usr/local/sbin/` | Пересобирает NVIDIA-модули cmpunlocker под ядра, где их нет (`--missing` по умолчанию, `--kernel X --force`, `--check`). Никогда не выгружает работающий драйвер и не ребутает. |
| `systemd/cmpunlocker-modules.{service,timer}` | `/etc/systemd/system/` | Запускают билдер через 10 минут после загрузки и далее каждые 6 часов. |
| `kernel-postinst/99-cmpunlocker-modules` | `/etc/kernel/postinst.d/` | При установке нового ядра (`*-pve`) hand-off'ит сборку модулей в `systemd-run`, чтобы dpkg не блокировался. |
| `cmpunlocker-kernel-modules.md` | `/usr/share/doc/cmpunlocker-kernel-modules/README.md` | Документация к билдеру (писалась в эпоху 64 GiB/Gen2 — часть цифр устарела, механика та же). |
| `systemd/gen2.service` | — (отключён) | Прежний ранний Gen2-ретрейн (`/usr/local/sbin/gen2-hammer`). Заменён `gen3.service`; оставлен для истории. |
| `HOST-README.md` | `/root/CMP170HX-README.md` | Эксплуатационная документация хоста: обновление Proxmox, ловушки, P2P, текущее состояние. |

## Как проходит загрузка

1. Ядро `-z2p`: quirk 9902 разворачивает BAR1 CMP-пары до 32 GB.
2. `modprobe nvidia` с `cmp_a100_ss=1`: CMP-пара проходит полный цикл cmpunlocker;
   A100-карта не проходит первый probe (BIOS не назначил её BAR'ы).
3. `gen3.service` (после `multi-user.target`):
   * `setpci -s <A100-карта> 0xbc0.l=00000f00` (REBAR 32 GB), `remove` + `rescan`;
   * карта пробуется снова и грузится через путь `cmp_a100_ss`
     (`A100 SS unlock SS0 0x00000000->0x88888888 … normal BooterLoad status=0x0`);
   * CMP-карты к этому моменту уже грузятся с `LMR=0x28b` (80 ГиБ, `publicBytes=0x13d30f0000`);
   * если какая-то из наших карт так и без драйвера (так бывает, когда A100-карта делит плату PLX
     с CMP: ядро кладёт 32-битное окно платы внутрь MMCONFIG, см. docs/rig-and-history.md) —
     платы перечисляются: окна мостов выключаются, корневой порт удаляется, `rescan`;
   * ждёт, пока `nvidia-smi -L` не покажет 3 карты;
   * Gen3-ретрейн CMP-карт;
   * если CT 101 запущен (`onboot=1`) — его рестарт, чтобы внутри появились `/dev/nvidia*`.
4. vLLM в CT 101 поднимается сам: 2026-11-27 от reboot до готового сервера — 5.7 минуты (раскладка
   до обеда), 5 мин 14 с (раскладка после перестановки, с перечислением платы).

## Замер PCIe

`../bench/pciebench.py` (в CT 101 — `/root/pciebench.py`): pinned H2D/D2H по каждой карте и копии
карта→карта, плюс строка линка из `nvidia-smi`. Запускать при остановленном `qwen-intel`, как
`gpubench.py`. На хосте пока не запускался.

## Проверка

```bash
cat /sys/module/nvidia/parameters/cmp_a100_ss                 # 1
nvidia-smi --query-gpu=index,name,memory.total,pcie.link.gen.current,pcie.link.width.current --format=csv
journalctl -b -k  | grep -E "BAR1 REBAR programmed|CMP BAR1: final size|GPU2.*(A100|BooterLoad)|POST-WRITE|SEC2_DEBUG_HEAP"
journalctl -b -u gen3.service; tail /var/log/gen3.log
cmpunlocker-kernel-modules --check
```
