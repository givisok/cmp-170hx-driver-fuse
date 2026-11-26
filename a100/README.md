# Инструменты A100 SM-speed (на хосте — `/root/a100-work/`)

| Файл | Что делает |
|---|---|
| `mkpatches.py` | Перегенерирует `sec2-postbl-plm-ss-cfg.patch`, `booter-verify.patch`, `a100-ss-param.patch` из pristine-тарбола open-gpu-kernel-modules: применяет к свежему дереву known-good версии, накладывает правки (каждый якорь обязан совпасть ровно один раз), делает `diff -Naur`. В конце применяет весь PATCH_ORDER к свежему дереву — любые FAILED/fuzz видны сразу. Результат кладёт в `/root/a100-work/out/`. |
| `deploy.py` | Бэкапит (`/root/cmpunlocker-0.5-backups/pre-a100-<date>`) и раскладывает сгенерированное в `/root/cmpunlocker-0.5`, правит `PATCH_ORDER` и `unlocks` в `constants.yaml`, прогоняет `read-constants.py`. `--revert` возвращает прежнее состояние. |
| `a100-ss-test.sh <0\|1\|2> [reset]` | Эксперимент без ребута: гасит nvidia-модули, грузит `nvidia` с `cmp_a100_ss=<mode>`, снимает журнал загрузки A100-карты, возвращает Gen3 на CMP-паре. CT 101 должен быть остановлен. `reset` перед загрузкой возвращает WPR2/SS0/SS1 (срабатывает только пока PLM открыты). Лог `/var/log/a100-ss-test.log`. |
| `a100_regs.py [state\|reset]` | Читает через BAR0 A100-карты (`09:00.0`): WPR2_LO/HI, PLM WPR_CFG/WPR/FEAT, SS0, SS1. `reset` пишет сток: `WPR2_HI=0`, `SS0=0`, `SS1=0`. |
| `gpu_regs.py [BDF…]` | То же для всех трёх карт (+ PLM FEAT2/OPT/RECONFIG, OPT_GPC_DISABLE). Только адреса ниже 16 MiB — прямой PRI-апертуры BAR0. |

## Порядок работы с патчами

1. Поправить текст правок в `mkpatches.py` (словари `*_OLD`/`*_NEW`).
2. `python3 mkpatches.py` — смотрим, что стёк применился без FAILED и без новых fuzz.
3. `diff /root/cmpunlocker-0.5/driver/patches/<имя> /root/a100-work/out/<имя>` — глазами.
4. `python3 deploy.py`, затем `cmpunlocker-kernel-modules --kernel "$(uname -r)" --force`.
5. Проверить без ребута: `a100-ss-test.sh 2` (контроль), затем `a100-ss-test.sh 1`.

Хунки в патчах руками не править: счётчики в заголовках хунков легко сломать, а GNU patch при
этом молча выбрасывает последующие хунки (так на этом стенде уже терялся символ
`cmp170hx_want_p2p` из форка scrameta).

## Что важно знать

* Booter-запуск с payload всегда заканчивается `Booter failed with non-zero error code: 0x31` —
  это ожидаемый код, регистр при этом записан.
* `0x5` на настоящей загрузке = WPR2 остался «поднятым» (`WPR2_HI != 0`): restore не сработал.
* Falcon-таймаут (`kflcnWaitForHalt … status 0x65`) после записи CFG1/LMR + SM-RECONFIG на
  `0x20b5` — не повторять: полный CMP-цикл для этой карты не нужен.
* Значения регистров — только volatile; обесточивание возвращает всё к стоку.
