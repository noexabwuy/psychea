# glossary

short explanations of the terms used in the docs and in [protocol.md](protocol.md). entries are alphabetical.

**accessory** - the PSP's name for a device that plugs into its USB connector and is the USB *host* (camera, GPS). the PSP is the USB device. handled by the PSP module `sceUSB_Acc_Driver`; the "accessory handshake" is the first control exchange (protocol.md, section 2).

**accessory info** - the 8 bytes `4C 05 5B 02 01 10 01 00` that the host writes during the handshake (VID 054C, PID 025B, version 0x1001, subtype 1).

**ALC** - automatic level control. a field of the microphone start message (`START_MIC`, bytes 0-1). psychea only logs it.

**ARK-4** ([project](https://github.com/PSP-Archive/ARK-4)) - the custom firmware the PSP must run. needed for the `psychea` plugin and the PSP-side patches.

**bulk transfer** - USB transfer type with guaranteed delivery and no fixed timing. EP3 of IF3 (alt 0) is bulk; the PSP uses it only for photos in the older "COM" mode, which psychea does not use.

**camera type** - the u16 the host sends in reply to the type query (id 3, cmd 9). the PSP only looks at whether it is 0, below 0x500 or at least 0x500. psychea sends 0x0500.

**cmd** - the third byte of an EP1 message: the command number (for example 0x2B HELLO, 0x01 START_VIDEO). together with **id** it names the message.

**COM mode / trailer mode** - the two ways of framing a JPEG frame on EP4. COM mode inserts a COM segment with the length after the start of the JPEG and needs full 896-byte packets; trailer mode sends the plain JPEG and ends the frame with a 16-byte trailer. chosen by the camera-type reply (< 0x500 or >= 0x500). psychea uses trailer mode.

**EP0 .. EP4** - USB endpoints (protocol.md, section 1). EP0 is the control endpoint (the handshake and the replies); EP1 carries the PSP's messages, EP2 the microphone samples, EP4 the video frames and photos; EP3 is not used.

**EV (exposure value)** - the exposure setting of the camera app. code 0..16: 0 = +2.0 EV, 8 = 0.0, 16 = -2.0, steps of 1/4 EV; the XMB app's neutral code is 10, EyePet sends 8.

**extended framing (START_MIC_EX)** - a second form of the microphone start message (cmd 3, 15 bytes) in which every audio packet starts with a 2-byte header. implemented, not confirmed on hardware.

**G4C, G50** - names (protocol.md, section 2) for two flags of the PSP's USB bus driver: G4C = "accessory power on" (patched to 1 by `psychea`), G50 = "accessory authenticated" (set by the handshake, read by `sceUsbAccGetAuthStat`).

**gain / noise (microphone)** - fields of the microphone start message (bytes 2-3 and 4-5) from the game's `SetupMic` call. psychea only logs them; the volume is `--micgain`.

**handshake** - see Accessory (protocol.md, section 2).

**HELLO** - message id 2, cmd 0x2B, sent when the PSP attaches and any time the game asks the lens/mirror state. must be answered with 4 bytes `flip, mirror, lens, 0`.

**id** - the first two bytes of an EP1 message: the accessory type (1 = microphone, 2 = camera, 3 = queries: type, lens).

**IF0 .. IF3** - the four USB interfaces of the PSP in camera mode: 0 accessory (EP1), 1 audio control, 2 audio streaming (EP2), 3 video and photos (EP3 / EP4).

**isochronous (iso)** - USB transfer type with a fixed bandwidth per millisecond and no retries. video (EP4) and audio (EP2) use it; a lost packet is simply lost.

**JPEG budget** - the largest JPEG psychea may send per frame: the smallest of the game's requested maximum (minus 1500 bytes), the PSP's measured limit and what can be sent in 80 % of the frame period (protocol.md, section 5).

**lens direction** - which way the camera lens faces (1 = normal). reported in the HELLO reply; mirrored/flipped pictures are applied by psychea, not by the PSP.

**PSP-300 / PSP-450x** - the two retail cameras (Go!Cam). psychea replaces them.

**psychea** - the project. it has two halves with the same name: the PC program (`uv run psychea`) that plays the camera and microphone, and the kernel plugin `psychea.prx` that runs on the PSP (ARK-4; `plugin/README.md`). games and the XMB camera app need it.

**psychea.ini** - `ms0:/seplugins/psychea.ini`, `key=0|1` per line, missing keys keep their defaults. the keys and their defaults are in `plugin/psychea.ini.sample`.

**retry window (`ATTEMPT_WINDOW`)** - the 2 seconds after the PSP appears on USB during which the PC repeats the handshake every 20 ms, because the PSP checks the connection only for a moment after the cable is plugged in (protocol.md, section 2).

**setter** - a message that changes one camera setting (zoom, contrast, effect, ...). the PSP does not wait for an answer.

**soft limiter** - the last step of psychea's microphone chain: samples above 70 % of full scale are bent smoothly towards full scale instead of being clipped.

**StartVideo** - message id 2, cmd 0x01 with a 32-byte configuration (size, frame rate, white balance, effect, zoom, largest JPEG, ...). the host then selects IF3 alt 1 and streams.

**still (photo)** - a single picture taken by the XMB camera app. the PSP sends START_STILL_SETUP (id 2, cmd 3) and waits for one JPEG on the video pipe.

**trailer** - the 16-byte packet `FF FE 00 0A <u32 length> ... FF D9` that ends a video frame or photo in trailer mode. byte 9 is a sequence counter (1 for photos).

**WASAPI** - the Windows audio API whose devices only open at the sample rate Windows has set; psychea opens them with `auto_convert` so that Windows converts the rate. in `--list` the same microphone appears once per audio API (MME, DirectSound, WASAPI, WDM-KS).

**WinUSB** - the Microsoft driver that lets a program use a USB device directly. `--install-driver` registers it for the PSP's interfaces 0, 1 and 3.

**XMB** - the PSP's main menu ("cross media bar"). its built-in camera app is the `camera_plugin_module`.

**XMBPASS** - the plugin patch that makes the XMB camera app's one-time authentication check succeed (protocol.md, section 2).

**Zadig / wdi-simple / libwdi** ([Zadig](https://zadig.akeo.ie/), [libwdi](https://github.com/pbatard/libwdi)) - tools that install WinUSB. Zadig has a window; wdi-simple (from libwdi, the engine of Zadig) is a command-line program shipped in `psychea/binaries` and used by `--install-driver`.
