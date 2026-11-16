# psychea plugin (PSP kernel plugin, [ARK-4](https://github.com/PSP-Archive/ARK-4) CFW 6.61)

makes the PSP accept the PC as its Go!Cam: patches the accessory power and auth checks (so the Camera item of
the XMB works) and logs the USB traffic. pairs with the PC tool in `../psychea/`.
license: GPL-3.0-or-later like the rest of the repository (`../LICENSE`); the plugin is built with the GPL header `systemctrl.h`.

## files
- `main.c`: includes, module info, `module_start`/`module_stop`. the rest is split by topic and included in this order:
  `defs.h` (constants), `util.h`, `log.h` (RAM log, clock), `bus_state.h` (hook table, driver and request tables),
  `bus_hooks.h` (bus and driver wrappers), `camapi.h` (sceUsbCam hooks), `patch.h` (import stub patches), `dump.h`,
  `modules.h` (module-start handler), `writer.h` (writer thread, log file, ini loading).
- pure code, tested on the PC: `safe.h` (address checks, clock), `cfg.h` (ini parser), `stillbig.h` (1280x960 photo patch), `slots.h` (table rows).
- `test_cfg.c`, `test_slots.c`: host tests of `cfg.h` and `slots.h`. `psychea.ini.sample`: the settings file with every key explained.

## build
needs the PSP SDK ([pspdev](https://github.com/pspdev/pspdev)) on PATH. output: `psychea.prx` (ignored by git).

    export PSPDEV=/path/to/pspdev; export PATH=$PSPDEV/bin:$PATH
    rm -f main.o psychea.elf psychea.prx; make

## test (on the PC, no PSP)

    gcc -Wall -o /tmp/test_cfg test_cfg.c && /tmp/test_cfg         # prints "cfg ok"
    gcc -Wall -o /tmp/test_slots test_slots.c && /tmp/test_slots   # prints "slots ok"

## install
1. copy `psychea.prx` to `ms0:/seplugins/` and `psychea.ini.sample` to `ms0:/seplugins/psychea.ini` (set `short_log=1` for everyday use).
2. in `ms0:/PSP/SAVEDATA/ARK_01234/PLUGINS.TXT` add the lines

       game, ms0:/seplugins/psychea.prx, on
       vsh, ms0:/seplugins/psychea.prx, on

3. power the PSP off and on. the log is `ms0:/psychea.log`;
   its first line is `psychea v0.1 start: ...`.

## settings

`ms0:/seplugins/psychea.ini` has one `key=0` or `key=1` per line (`#` starts a comment); a missing key keeps its default, so
the file is optional. the plugin writes `ms0:/psychea.log` (the log is appended from boot to boot; once it is over 8 MB, the next boot moves it to `psychea.old`).

keys and defaults, each explained: `psychea.ini.sample`. the research keys (`log_camera_calls`, `photos_1280x960`, `dump_modules`) stay 0 for daily use.

everyday use: `xmb_camera=1`, `accessory_power=1`, `short_log=1`, everything else 0.
