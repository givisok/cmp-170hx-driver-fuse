# Патчи ядра Proxmox (сборка `-z2p`)

Рабочее ядро хоста — `7.0.14-29-z2p-pve`: сборка [pve-kernel](http://git.proxmox.com/git/pve-kernel.git)
с двумя патчами из этого каталога и `KREL_EXTRA=-z2p` в `Makefile` (суффикс нужен, чтобы пакет
сосуществовал со стоковым ядром и сортировался выше него в GRUB).

| Файл | Что делает |
|---|---|
| `9901-pci-size-bridge-window-for-child-alignment.patch` | `pbus_size_mem()` считает окно моста без учёта выравнивания детей: если размер ребёнка не кратен его выравниванию, перед следующим ребёнком остаётся промежуток, который не заложен в бюджет — окна не хватает, и картам молча не достаётся адресное пространство. |
| `9902-pci-quirk-cmp170hx-rebar-early-32g.patch` | Early-quirk (до чтения BAR'ов): через окно XVE в BAR0 включает Resizable BAR и ставит BAR1 = 32 GB для `10de:20c2`, `10de:2082`, `10de:20b5`. Иначе enumeration раскладывает окна под стоковые 64 MB, и позднему ресайзу некуда ставить BAR. |
| `pve-kernel-Makefile-KREL_EXTRA-z2p.diff` | Единственная правка `Makefile`: `KREL_EXTRA=-z2p`. |

Без этих патчей (на стоковом ядре) раздача BAR1 — лотерея загрузки.

## Замечание про 9902 и карту `10de:20b5`

На хосте файл 9902 до 2026-11-27 был повреждён: строки с хуком для `0x20b5` дописали без
префикса `+`, и GNU patch молча их выбросил (хунк «удачно» применялся без них). Здесь лежит
исправленная версия (хунк `@@ -6395,3 +6395,110 @@`, проверена `patch --dry-run` на чистом
`submodules/ubuntu-kernel`); она же положена на хост вместо повреждённой.

Функционально это ничего не меняет: для A100-карты quirk всё равно выходит сразу — BIOS оставляет
её BAR0 неназначенным (`bar0 == 0`), а по неназначенному окну XVE не читается. BAR1 этой карты
разворачивает `gen3.service` уже после загрузки драйвера (см. `host/`).

## Сборка

Полная процедура и ловушки (пути к control, lintian, конфликт headers, «Unmet dependencies») —
в [../host/HOST-README.md](../host/HOST-README.md).
Коротко:

```bash
cd /root/kernel-p2p/pve-kernel
git fetch origin && git checkout -- Makefile && git checkout origin/master
# патчи 9901/9902 лежат в patches/kernel/ (untracked, переживают checkout)
sed -i 's/^KREL_EXTRA=.*/KREL_EXTRA=-z2p/' Makefile
git submodule update --init --depth 1 submodules/ubuntu-kernel submodules/zfsonlinux
(cd submodules/zfsonlinux && git submodule update --init --depth 1)
make build-dir-fresh
mk-build-deps -ir -t "apt-get -y -o Debug::NoLocking=1" proxmox-kernel-7.0.*/debian/control
nohup make deb > build.log 2>&1          # ~50 минут
dpkg -i proxmox-kernel-<ver>-z2p-pve_*.deb
dpkg --purge proxmox-headers-<ver>-z2p-pve; dpkg --force-conflicts -i proxmox-kernel-<ver>-z2p-pve_*.deb
cmpunlocker-kernel-modules --kernel <новое-z2p-имя>     # модули под новое ядро, ДО ребута
reboot
```
