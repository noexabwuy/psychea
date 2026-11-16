# using psychea

what the program does, option by option, and what to do when something goes wrong. the short version is in the [README](../README.md).

## windows driver and first run

on Windows the PSP's interfaces 0, 1 (audio) and 3 need the WinUSB driver, once per USB port: `uv run psychea --install-driver` in a
terminal opened **as Administrator**. the PSP can stay unplugged: the command registers WinUSB (wdi-simple from libwdi, shipped in
`psychea/binaries`) for the PSP camera's hardware IDs, and Windows binds it the next time the PSP shows up as the camera. fallback:
Zadig -> Options -> List all devices -> each PSP interface (0, 1 and 3) -> WinUSB -> Replace driver. on Linux no driver is needed.
in camera mode the PSP shows up on USB for only a short moment, which is why the installer does not wait for it.

the order matters: start the program, get the game (or the XMB camera app) to its camera screen, **then** plug in the cable. the PSP
checks the connection for only a fraction of a second after the cable is plugged in, so the camera screen must already be open;
psychea keeps retrying the handshake for 2 seconds after the PSP appears. when the session ends, unplug the cable to start again; the
program keeps running and waits for the next connection.

## command line

`uv run psychea --help` prints exactly this text:

```
Usage: uv run psychea [options]

Picture
  --webcam N         PC camera to use (default 0; numbers from --list)
  --camres WxH       capture size, e.g. 1280x720: sharper after scaling down (default 640x480)
  --mirror           flip the picture left-right (if the webcam shows a mirror image)
  --testpattern      colour bars instead of a camera (checks the PSP link without a webcam)

Sound
  --mic pc|tone|off  PC microphone (default; needs sounddevice), 1 kHz test tone, or no audio
  --micdev N         microphone to use (numbers from --list)
  --micgain G        volume factor, e.g. 0.4 (default 1.0)

Diagnostics
  --list             show the cameras and microphones of this PC, then exit
  --verbose          also show protocol detail on the console (the log file always has it)
  --logfile PATH     where to write the detailed log (default psychea.log; send it if something goes wrong)

Windows
  --install-driver   once, as Administrator: register WinUSB for the PSP camera (the PSP can stay unplugged)

Options with a value also accept --name=value.
Run it, get the game to the camera screen, THEN plug the cable.
```

notes on the options:

- the frame rate is the one the game asks for, at most 30; there is no `--fps`.
- `--mic pc` falls back to a 1 kHz test tone (with a warning) when the PC microphone cannot be opened.
- `--camres 1280x720` (160x120 to 4096x4096) captures at that size (MJPG) and scales down with area averaging, which is sharper than a 640x480 capture cropped to 16:9. it costs some PC CPU for decoding. a camera that cannot do the size keeps its own and psychea logs a warning (`camres_ignored`). photos are cut from this same frame.
- `--mirror` flips the picture left-right on top of what the game asks for (use it when the webcam driver already mirrors).

## which camera-app settings are honoured

the PSP sends its picture settings to the PC; psychea applies them to the webcam picture itself (the PSP never touches the
JPEG). each change appears as `Game set ...` in the log.

| setting in the camera app | what psychea does |
|---|---|
| zoom (1.0x..8.0x) | digital centre crop and enlarge |
| image effect | negative, black and white, sepia, and blue / red / green tints |
| white balance | daylight and auto: picture unchanged; fluorescent and incandescent: colour gains from a black-body estimate (not compared with the real camera) |
| exposure (EV) | brightness curve |
| contrast, brightness, saturation, sharpness | applied to the picture; the neutral values leave it unchanged |
| mirror / flip | applied to the picture (the app sends it when the stream starts) |
| photo sizes | the photo is cut to the requested shape from the newest webcam frame (640x480 unless `--camres`) and resized; sizes above the webcam frame are therefore enlarged, not sharper |
| video sizes | the size and frame rate in the game's start request (the picture is cropped to its aspect ratio) |

which settings arrive depends on the program (the XMB camera app and games send different ones: [protocol.md](protocol.md) section 6).

not honoured: anti-flicker (only named in the log), photo quality (fine / standard) and the self-timer value.
saturation: games start with the value 125, which arrives as 2, and psychea reads only the setter, not the start value.

## microphone

when the game starts the microphone (`Game started the microphone: 22050 Hz ...`) psychea sends the PC microphone as 16-bit
mono at the rate the game asks for (11025, 22050, 44100 or 48000 Hz; 11025 and 22050 were seen). the signal passes a 100 Hz
high-pass and a 10 kHz low-pass (like the original microphone's band), the gain `--micgain`, and a soft limiter that bends
smoothly to full scale instead of clipping. the details are in [docs/protocol.md](protocol.md) section 10.

`--mic tone` sends a 1 kHz tone, `--mic off` sends nothing. if the sound is too loud or distorted, lower `--micgain` (for
example 0.3); if it is too quiet, raise it. the delay is about 27-42 ms.

on Windows `--list` shows the same microphone once per audio API (MME, DirectSound, WASAPI, WDM-KS). the default is MME;
a WASAPI entry (`--micdev N`) works too.

audio write failures when the PSP is unplugged are normal (debug level only); other audio failures: see troubleshooting.

## logs

- the **console** shows short plain-language lines; `--verbose` adds protocol detail.
- **`psychea.log`** (in the folder you run it from, rotated at 5 MB, 3 older files kept) always has everything. one event per
  line: `date time LEVEL component text | event=name key=value ...`. search with `grep event=video_start psychea.log`.
  send this file when something goes wrong.
- levels: choices of the user and requests of the game are INFO; a problem that does not stop the program is WARNING (written as WARN in the file)
  (and says what was done about it); a lost operation is ERROR and says what to try; protocol traffic (`psp_message`) is DEBUG
  and appears only in the file (or on the console with `--verbose`).
- events: every line has `event=name`; failures end in `_failed` (also `session_crashed`, `video_gave_up`, `mic_gave_up`). the ones to look for: `start` (the first line of a
  run: version, Python, OS, arguments), `device_seen`, `handshake_ok` / `handshake_failed`, `camera_open` / `camera_failed`,
  `video_start` / `video_stop`, `setting` (what the game changed), `still` (a photo), `stats_video` and `stats_mic` (every 10 s:
  fps, KB per frame, picture delay, audio failures), `unplugged` and `session_end`.

## troubleshooting

| symptom | cause and what to do |
|---|---|
| first plug-in fails with `error 433` ("device gone") in `psychea.log`, re-plugging works | seen on a first plug-in with no driver yet. the PSP gives up about 90 ms after it appears. re-plug the cable. |
| `Could not connect to the PSP` / `handshake_failed` | the game or XMB app was not on its camera screen when the cable was plugged in. unplug, open the camera screen, plug again. on Windows check that the driver is installed. |
| `Could not switch the audio interface off: ... error 170` | Windows refuses to change the audio interface while an audio buffer is registered. the program closes the buffer first, so this should not appear in a normal stop; if it does, send the log. |
| the PC microphone is the wrong device, or you hear a steady 1 kHz tone | run `uv run psychea --list`, then `--micdev N`. the tone means the microphone could not be opened (see the warning in the log). |
| microphone too loud or crackling | lower `--micgain` (for example 0.3). |
| `Error opening RawInputStream: Invalid sample rate [PaErrorCode -9997]`, then a steady 1 kHz tone | the chosen microphone entry refuses the sample rate the game asked for. try another entry of the same microphone in `--list` (MME works), or `--mic off`. |
| `An audio transfer failed (status ...)` / `Audio stopped: 100 transfers failed in a row` while the PSP is still connected | the audio stream broke. re-plug the cable. a single failure restarts the stream by itself; send `psychea.log`, ideally recorded with `--verbose`. |
| `Could not start the microphone stream` | run `--install-driver` again (the audio interface 1 needs WinUSB too), or use `--mic off`. |
| `the WinUSB driver is not installed` (WARNING right after the cable is plugged in) | the driver is not registered yet. run `uv run psychea --install-driver` once in a terminal opened as Administrator, then re-plug the cable. |
| the XMB camera app says to connect a camera | the PSP needs ARK-4 with `psychea`; see [docs/protocol.md](protocol.md). |

## known limitations

the status table is in the [README](../README.md). besides it:

- tested only on a PSP Street (E1000); other games may ask for something not seen yet.
- the colours of the fluorescent and incandescent white balance, and the neutral point of saturation, were not compared with the real camera.
- photos above 640x480 are enlarged, not sharper (the webcam frame is the limit).

the full list of what was and was not tried on hardware is in [docs/protocol.md](protocol.md) section 11.
