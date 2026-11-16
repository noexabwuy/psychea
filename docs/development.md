# development

## tests and checks

    uv run pytest                  # tests, no hardware needed
    uv run ruff check .            # lint (max complexity 10)
    uvx --with numpy mypy          # types (config in pyproject.toml; numpy is needed so that the import resolves)
    gcc -Wall -o /tmp/test_cfg plugin/test_cfg.c && /tmp/test_cfg        # the plugin's ini parser, clock and photo patch (prints "cfg ok")
    gcc -Wall -o /tmp/test_slots plugin/test_slots.c && /tmp/test_slots  # the plugin's driver table (prints "slots ok")

layout (one decision per file):

- `psychea/protocol.py`  constants, frame framing, message parsing - pure functions
- `psychea/session.py`   one connection: answers the PSP's messages, streams video and audio (backend independent)
- `psychea/transport.py` backend interface, USB error types, the handshake
- `psychea/winusb.py`    Windows backend (winusb.dll via ctypes), hardware verified: handshake, video, microphone
- `psychea/libusb_backend.py` Linux backend (unverified) and "is the PSP plugged in?" for every OS
- `psychea/sources.py`   webcam, test pattern, PC microphone, tone, picture adjustments
- `psychea/audio_packets.py` the 1 ms microphone packets, shared by both backends
- `psychea/stats.py`     the 10-second video statistics line
- `psychea/settings.py`  the run-time settings (from the command line)
- `psychea/devices.py`   `--list`
- `psychea/driver.py`    `--install-driver`
- `psychea/logs.py`      logging rules and formatters
- `psychea/cli.py`       arguments and the connect - run - wait-for-unplug loop
- `psychea/__main__.py` starts `cli.main` for `python -m psychea`
- `plugin/`             the PSP kernel plugin `psychea.prx` (C, PSP SDK): `main.c` plus the headers it includes, `psychea.ini.sample`; see `plugin/README.md`
- `LICENSE`            GPL-3.0 text
- `assets/`             banner (`banner.png` for dark pages, `banner-light.png` for light ones), `logo.png`
- `scripts/bench.py`    micro-benchmarks of the hot paths (`uv run python scripts/bench.py`)
- `scripts/size_probe.py` sends frames of exactly growing sizes to find the PSP's size limit (research)

documentation: [docs/protocol.md](protocol.md) is the protocol (what the PSP sends, what we answer, what is verified),
[docs/glossary.md](glossary.md) explains the terms, [plugin/README.md](../plugin/README.md) builds and installs the PSP plugin.

## third-party projects

installed from PyPI by `uv` (versions in `pyproject.toml`):

| project | used for |
|---|---|
| [libusb1](https://github.com/vpelletier/python-libusb1) (a wrapper for [libusb](https://libusb.info/)) | "is the PSP plugged in?" on every OS, all transfers on Linux |
| [opencv-python-headless](https://pypi.org/project/opencv-python-headless/) | webcam capture, resizing, JPEG encoding |
| [sounddevice](https://github.com/spatialaudio/python-sounddevice) (on [PortAudio](https://portaudio.com/)) | the PC microphone |
| [cv2-enumerate-cameras](https://pypi.org/project/cv2-enumerate-cameras/) | the camera names for `--list` |

shipped in this repository: `psychea/binaries/wdi-32bit.exe` and `wdi-64bit.exe`, the unmodified "wdi-simple" built from
[libwdi](https://github.com/pbatard/libwdi) (LGPL-3.0, the engine of [Zadig](https://zadig.akeo.ie/)), v1.3.1 as shipped in
[capablerobot-usbregister](https://pypi.org/project/capablerobot-usbregister/) 1.3.1.2 (MIT). they are used only by
`--install-driver` (details: `psychea/binaries/README.txt`).

on the PSP: [ARK-4](https://github.com/PSP-Archive/ARK-4) custom firmware. to build `psychea.prx`: the
[pspdev](https://github.com/pspdev/pspdev) toolchain.

development tools: [uv](https://docs.astral.sh/uv/), [ruff](https://docs.astral.sh/ruff/), and pytest, coverage, hypothesis,
mutmut and radon (see `pyproject.toml`); mypy is run with `uvx`.
