# Что живёт на хосте

Все пути — как на хосте `pve` (192.168.1.11). Скрипты предполагают, что дерево драйвера лежит
в `/root/cmpunlocker-0.5`, а инструменты из `../a100/` — в `/root/a100-work`.

| Файл в репо | Куда ставится | Что делает |
|---|---|---|
| `gen3-boot` | `/usr/local/sbin/gen3-boot` | Фиксы после загрузки драйвера: REBAR A100-карты 128G → 32G + remove/rescan; ждёт все 3 карты; Gen3-ретрейн `05:00.0`/`06:00.0`; рестарт CT 101. Лог `/var/log/gen3.log`. |
| `systemd/gen3.service` | `/etc/systemd/system/` | `After=multi-user.target nvidia-persistenced.service`, oneshot, `TimeoutStartSec=300`. |
| `gen3_retrain.py` | `/root/gen3_retrain.py` | Volatile Gen3-ретрейн одной карты (`gen3_retrain.py 0000:05:00.0`): читает фьюз-гейты, чистит `CYA_0` bit 2 (DIS_G2), ставит `LINK_CONFIG_0[19:18]=1` (Gen3; кодирование обратное: 0 = Gen4, 1 = Gen3, 2 = Gen2), `LNKCTL2` target = 8.0 GT/s на карте и upstream-мосту, дёргает retrain. Всё сбрасывается при обесточивании. |
| `modprobe.d/cmpunlocker-a100-ss.conf` | `/etc/modprobe.d/` | `options nvidia cmp_a100_ss=1` — SM-скорость A100-карты. Удалить файл + reboot = откат. |
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
   * `setpci -s 09:00.0 0xbc0.l=00000f00` (REBAR 32 GB), `remove` + `rescan`;
   * карта пробуется снова и грузится через путь `cmp_a100_ss`
     (`A100 SS unlock SS0 0x00000000->0x88888888 … normal BooterLoad status=0x0`);
   * ждёт, пока `nvidia-smi -L` не покажет 3 карты;
   * Gen3-ретрейн CMP-пары;
   * если CT 101 запущен (`onboot=1`) — его рестарт, чтобы внутри появились `/dev/nvidia*`.
4. vLLM в CT 101 поднимается сам: 2026-11-27 от reboot до готового сервера — 5.7 минуты.

## Проверка

```bash
cat /sys/module/nvidia/parameters/cmp_a100_ss                 # 1
nvidia-smi --query-gpu=index,name,memory.total,pcie.link.gen.current,pcie.link.width.current --format=csv
journalctl -b -k  | grep -E "BAR1 REBAR programmed|CMP BAR1: final size|GPU2.*(A100|BooterLoad)"
journalctl -b -u gen3.service; tail /var/log/gen3.log
cmpunlocker-kernel-modules --check
```
