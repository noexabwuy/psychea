<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/banner.png">
    <img src="assets/banner-light.png" alt="psychea" width="520">
  </picture>
</p>

# psychea - a USB camera and microphone for the PSP, made from your PC

![license: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)
![python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![Windows: verified](https://img.shields.io/badge/Windows-verified-brightgreen)
![Linux: tests only](https://img.shields.io/badge/Linux-tests%20only-yellow)
![PSP: Street (E1000), ARK-4 6.61](https://img.shields.io/badge/PSP-Street%20(E1000)%20%C2%B7%20ARK--4%206.61-lightgrey)

`psychea` lets a PC act as the Sony PSP "Go!Cam" (USB 054C:0282): the PC's webcam becomes the picture, the PC's
microphone becomes the sound. a Python program on the PC plus a small kernel plugin on the PSP.

| works with | |
|---|---|
| EyePet, Go!Edit (JP), Invizimals: The Lost Tribes: video and microphone | ✅ verified |
| XMB camera app: live video, photos, size / effect / zoom / contrast / exposure / mirror | ✅ verified |
| 1280x960 photos (`photos_1280x960`, the picture is enlarged from the webcam frame) | ✅ verified |
| PSP Street (E1000) with ARK-4 6.61 | ✅ the model all of this was tested on |
| other PSP models | ❔ not tried |
| first plug-in on a clean Windows after `--install-driver` | ❔ unconfirmed |
| 44100 / 48000 Hz sound, `xmb_camera=0` | ❔ never tried |

✅ seen on a real PSP, 🧪 covered by tests only, ❔ not tried. the full list: [docs/protocol.md](docs/protocol.md) section 11.

## what you need

- a PSP with **ARK-4** and the `psychea` plugin ([plugin/README.md](plugin/README.md)), a USB cable. not the PSP's "USB Connection" mode.
- a webcam, optionally a microphone, on a PC with Windows (✅) or Linux (🧪).
- [uv](https://docs.astral.sh/uv/) (`winget install astral-sh.uv`): it installs Python and the dependencies by itself.

## quick start

1. Windows, once, in a terminal **as Administrator**: `uv run psychea --install-driver` (registers WinUSB; the PSP can stay unplugged; fallback: Zadig, WinUSB for interfaces 0, 1 and 3). Linux needs no driver.
2. `uv run psychea`
3. on the PSP open the game's (or the XMB's) **camera screen**.
4. **then** plug in the USB cable. unplug it to start again; Ctrl+C quits.

`uv run psychea --list` shows the cameras and microphones; the main options:

| option | |
|---|---|
| `--webcam N`, `--camres WxH` | which camera, and the capture size (`1280x720` is sharper after scaling down) |
| `--mic pc\|tone\|off`, `--micdev N`, `--micgain G` | which microphone and how loud |
| `--mirror` | flip the picture left-right |
| `--testpattern` | colour bars instead of a camera |
| `--verbose`, `--logfile PATH` | more detail on the console; where the log goes (default `psychea.log`) |

all options, the camera settings that are honoured, the microphone, logs and troubleshooting: [docs/usage.md](docs/usage.md).

## the PSP plugin

`psychea.prx` makes the PSP accept the PC as a camera (games and the XMB) and logs the USB traffic. build, install and the
`psychea.ini` keys: [plugin/README.md](plugin/README.md). for everyday use set `short_log=1` and leave the research keys at 0.

## more

[docs/usage.md](docs/usage.md) usage and troubleshooting · [docs/protocol.md](docs/protocol.md) the protocol and what is verified ·
[docs/glossary.md](docs/glossary.md) terms · [docs/development.md](docs/development.md) layout, tests, third-party projects

## license

GPL-3.0-or-later ([LICENSE](LICENSE)): the plugin is built with the GPL header `systemctrl.h` (PRO CFW / ARK-4), so one license
covers the whole repository. `psychea/binaries/` holds unmodified third-party programs under their own license (LGPL-3.0).
