# the PSP camera protocol (USB 054C:0282) as psychea implements it

this is the one protocol document of the project. it describes what the code implements (`psychea/protocol.py`, `psychea/session.py`,
`psychea/sources.py`, `psychea/winusb.py`) and what was seen on hardware. terms are explained in [glossary.md](glossary.md).

## 0. how to read the confidence marks

| mark | meaning |
|---|---|
| **[HW]** | seen working on real hardware (Windows host, PSP Street (E1000) with ARK-4 and `psychea`). |
| **[DIS]** | read in a disassembly of the PSP firmware (not part of this project); implemented by psychea, not seen on hardware. |
| **[UNV]** | implemented by psychea but never confirmed, or unknown. |

## 1. roles and the USB layout

the PSP is the USB **device**, the camera (our PC) is the USB **host**. in camera mode the PSP enumerates as a composite
**full-speed** device 054C:0282 [HW].

| interface | function | endpoints | used by psychea |
|---|---|---|---|
| IF0 | vendor "USB Accessory" | EP1 IN, interrupt, 64 B | handshake, PSP messages |
| IF1 | audio control | none | its WinUSB handle reaches IF2 (`GetAssociatedInterface`) |
| IF2 | audio streaming, alt 0 idle, alt 1 streaming | EP2 OUT, isochronous, 128 B, 1 ms | microphone |
| IF3 | vendor, alt 0 idle, alt 1 streaming | alt 0: EP3 OUT bulk, 64 B; alt 1: EP4 OUT isochronous, 896 B, 1 ms | video and photos (alt 1) |

the rate that counts for the microphone is the one in the START_MIC message (section 10). constants in `protocol.py`: `EP_INT_IN=0x81`,
`EP_ISO_VIDEO=0x04`, `EP_ISO_MIC=0x02`, `PKT=896`.

on Windows interfaces 0, 1 and 3 need the WinUSB driver (`--install-driver`).

## 2. the accessory handshake [HW]

two vendor control requests on interface 0 (wIndex = 0):

1. `IN  bmRequestType=0xC1, bRequest=0x01, wValue=0x0003, wIndex=0, wLength=1` -> the PSP answers one byte, which must be `00`
   (any other value: psychea raises `HandshakeRejected`).
2. `OUT bmRequestType=0x41, bRequest=0x01, wValue=0, wIndex=0, 8 bytes` = the accessory info `4C 05 5B 02 01 10 01 00` (`INFO`).

the info bytes decode as VID 054C, PID 025B, version 0x1001, subtype 1 (little-endian u16). psychea sends exactly these bytes.

if the PSP's accessory-power flag (called G4C) is set, it answers `00` and latches the "authenticated" flag (G50) that
`sceUsbAccGetAuthStat()` reads (0 = authenticated, 0x80243701 = not yet) [DIS].

**two PSP-side patches make this work on a PSP without the real camera hardware** (both done by the `psychea` plugin; the keys `accessory_power` and `xmb_camera` of `ms0:/seplugins/psychea.ini` switch them, both on by default):

- the accessory-power flag G4C is forced to 1 (`sceUsbBus` export 48CCE3C1 returns 1). [HW: the handshake answers `00` with the patch.]
- **XMBPASS**: the XMB camera app (`camera_plugin_module`) checks `sceUsbAccGetAuthStat()` only **once**, right after the connection is "established",
  and deactivates USB if it fails. psychea rewrites that app's user-mode import stub of `GetAuthStat` to return 0, so the single check passes.
  XMBPASS can be switched off with `xmb_camera=0` (the switch-off itself is untested on hardware [UNV]). [HW: with XMBPASS the XMB camera runs; without it the PSP detaches about 1 ms after attaching and the host never sees a handshake.]
  games do not need XMBPASS but they do need the G4C patch: a game tried without `psychea` was rejected at the handshake [HW].

**timing** [HW]. the PSP detaches about 90 ms after ATTACH if Windows has not bound WinUSB by then (CreateFile error 2, then 433); `C1/01` has been seen as early as 16 ms after ATTACH.
psychea therefore retries the handshake for `ATTEMPT_WINDOW = 2.0` s after the device appears, with a pause of 20 ms between attempts (an attempt that fails because Windows has no WinUSB driver for the interface is logged as a WARNING that names `--install-driver`; every other failed attempt is only a DEBUG line).

## 3. EP1: messages from the PSP

after the handshake the PSP sends messages on EP1 (interrupt IN, one packet per message):

```
[u16 id, little-endian][u8 cmd][u8 len][payload, len <= 60]        total = len + 4 bytes
```

- `id`: **1 = microphone, 2 = camera, 3 = queries** (CAMERA_TYPE_QUERY, QUERY_0x43, SET_LENS_0x44).
- psychea polls EP1 continuously (a read timeout of 10 ms when idle, shorter while streaming).
- responses never travel on EP1 (section 4).

### message table

"Reply" is the control request of section 4; the PSP waits for one only where it says yes [DIS]; psychea answers nothing else.
"Name" is `MESSAGE_NAMES` in `protocol.py`.

| id | cmd | len | payload | name | reply | psychea does |
|---|---|---|---|---|---|---|
| 2 | 0x2B | 0 | - | HELLO | yes | reply `[flip, mirror, 01, 00]` (section 4.1) |
| 3 | 0x09 | 0 | - | CAMERA_TYPE_QUERY | yes | reply `00 05` (u16 0x0500, section 4.2) |
| 2 | 0x40 | 1 | `[u8]` | QUERY_0x40 | yes | reply `00 00 00 00` [UNV; never received in any log] |
| 3 | 0x43 | 2 | `[u16]` | QUERY_0x43 | yes | reply `00 00 00 00` [UNV; never received] |
| 2 | 0x01 | 32 | video config | START_VIDEO | no | parse (section 5), set IF3 alt 1, stream |
| 2 | 0x02 | 0 | - | STOP_VIDEO | no | IF3 alt 0 |
| 2 | 0x04 | 0 | - | STOP_VIDEO_4 | no | same as 0x02 (the PSP sends it after a photo) |
| 2 | 0x03 | 20 | still setup | START_STILL_SETUP | no | section 7 |
| 2 | 0xAB | 2 | `[flip][mirror]` | SET_FLIP_MIRROR | no | remember, apply to the picture |
| 2 | 0x05 | 1 | zoom | SET_ZOOM | no | apply |
| 2 | 0xA3 | 1 | white balance | SET_WHITE_BALANCE | no | apply |
| 2 | 0xA4 | 1 | saturation | SET_SATURATION | no | apply |
| 2 | 0xA5 | 1 | brightness | SET_BRIGHTNESS | no | apply |
| 2 | 0xA6 | 1 | contrast | SET_CONTRAST | no | apply |
| 2 | 0xA7 | 1 | sharpness | SET_SHARPNESS | no | apply |
| 2 | 0xAC | 1 | image effect | SET_IMAGE_EFFECT | no | apply |
| 2 | 0xAF | 1 | exposure (EV) | SET_EV_LEVEL | no | apply |
| 2 | 0xA9 | 1 | anti-flicker | SET_ANTI_FLICKER | no | named in the log only |
| 2 | 0xA0 | 2 | resolution | SET_RESOLUTION | no | named only; the size comes from START_VIDEO |
| 2 | 0x41 | 2 | `[u8][u8]` | SET_0x41 | no | named only |
| 3 | 0x44 | 3 | `[u16][u8]` | SET_LENS_0x44 | no | named only |
| 1 | 0x01 | 14 | microphone start | START_MIC | no | section 10 |
| 1 | 0x03 | 15 | microphone start, extended | START_MIC_EX | no | section 10 |
| 1 | 0x02 | 0 | - | STOP_MIC | no | stop the stream, IF2 alt 0 |

messages with another (id, cmd) are logged once as `psp_message_unknown` and ignored. setters 0xA1, 0xA2, 0xA8, 0xAA, 0xAD, 0xAE and 0x06 have never been
seen on the wire: the XMB camera, EyePet, Go!Edit (JP) and Invizimals: The Lost Tribes did not send them [HW]. psychea answers 0x40 and 0x43 with four zero bytes in case the PSP waits for them [UNV].

the PSP's own order when a camera session starts [HW, from the logs]: HELLO, START_MIC, SET_ANTI_FLICKER, CAMERA_TYPE_QUERY, then
START_VIDEO, then SET_FLIP_MIRROR. the XMB camera app restarts the whole sequence on every change of size or effect (STOP_MIC,
STOP_VIDEO, HELLO, START_MIC, SET_EV_LEVEL, START_VIDEO, SET_FLIP_MIRROR, then SET_IMAGE_EFFECT or SET_CONTRAST only sometimes).

## 4. replies: control OUT, bRequest 9

```
bmRequestType = 0x41 (host -> device, vendor, interface)
bRequest      = 0x09
wValue        = cmd of the message that is answered
wIndex        = 3 (IF3, the camera driver's interface)  [HW: works]
wLength / data = the reply payload
```

in psychea every reply is `Session.reply(cmd, data)`.

### 4.1 HELLO reply (cmd 0x2B) [HW]

four bytes `[flip][mirror][lens][00]`: byte 0 bit 0 = current flip state, byte 1 bit 0 = mirror state, byte 2 bit 0 = lens direction
(1 = normal), byte 3 = 0 [DIS]. psychea sends `flip, mirror, 1, 0` with the last orientation the game set (default `00 00 01 00`) and answers every HELLO.
what a different lens byte would do to a game is [UNV].

### 4.2 Camera type reply (id 3, cmd 0x09)

a u16 little-endian. psychea replies `CAMERA_TYPE = 0x0500` (bytes `00 05`) and uses trailer mode for everything [HW] (section 8). the PSP tells values below 0x500
(another framing, "COM mode", not implemented) from values of 0x500 and above [DIS]. a reply of 0 breaks the camera API (`protocol.py`).
until the host has answered this query `sceUsbCamSetupVideo` returns 0x80243902.

### 4.3 Other requests

psychea makes no control requests other than the handshake, the replies of this section and SET_INTERFACE.

## 5. START_VIDEO: the 32-byte video configuration (id 2, cmd 0x01)

built by the PSP from the game's `sceUsbCamSetupVideo(Ex)` parameters [DIS]. psychea reads bytes 1, 2, 5, 18, 20 and 26..29; the rest is
ignored and psychea works without it [HW].

| byte | meaning | values | used by psychea |
|---|---|---|---|
| 1 | **output size** | resolution code, below | yes |
| 2 | **frame rate** | 0..7 = 3.75, 5, 7.5, 10, 15, 20, 30, 60 fps (any other code: 15 fps) | yes (capped at 30) |
| 5 | **white balance** | 0 auto, 1 daylight, 2 fluorescent, 3 incandescent | yes |
| 18 | **image effect** | 0 normal .. 6 green | yes |
| 20 | **zoom** | 0 = off, or 10..80 = 1.0x..8.0x | yes |
| 26-29 | **maximum JPEG size per frame the game wants** | u32 LE | yes |
| 31 | exposure (EV) | 0..16, see section 6 | **no** (see below) |
| others | not read | | no |

psychea reads the white balance, effect and zoom at every start because the XMB app sends `SET_IMAGE_EFFECT` only when the effect is not "normal": without the reset a leftover effect would stay [HW].
exposure is **not** taken from byte 31: the XMB app's neutral code is 10 and EyePet sends 8, so there is no common base [HW]; psychea treats 10 as neutral.

`parse_start_video` needs at least 30 bytes (it reads bytes 1, 2 and 26..29); the extra start settings are read only when the message is the full 32 bytes. an unknown
resolution code falls back to 480x272; a max-JPEG value outside 2000..200000 is treated as "no limit given".

### resolution codes (byte 1, and still byte 7) [DIS]

| code | size | | code | size |
|---|---|---|---|---|
| 00 | 1280x1024 | | 05 | 176x144 |
| 01 | 1024x768 | | 06 | 160x120 |
| 02 | 640x480 | | 07 | 480x272 |
| 03 | 352x288 | | 08 | 360x272 |
| 04 | 320x240 | | 09 | 1280x960 |

### example [HW]

EyePet (`sceUsbCamSetupVideo`, non-Ex, 480x272 at 30 fps):

```
02 07 06 02 00 00 02 00 40 00 00 01 00 00 00 00 00 00 00 00 00 02 e8 03 f4 01 00 80 00 00 01 08
```

byte 1 = 07 (480x272), byte 2 = 06 (30 fps), byte 5 = 00 (auto), bytes 26-29 = `00 80 00 00` = 0x8000 = 32768 bytes max, byte 31 = 08.
the XMB camera app starts at 480x272 with 15 or 30 fps and switches sizes [HW].

### what psychea sends

- output size = byte 1; the picture is centre-cropped to that aspect ratio from the webcam frame (opened at 640x480, or at `--camres` when the camera offers that size; 30 fps requested) and resized. **send exactly the requested size** (a different size is untested [UNV]).
- frame rate: `min(requested, 30)`.
- frame budget: `jpeg_budget = min(max_size - 1500, 32256, bandwidth_cap(fps))` bytes. 32 256 bytes (36 packets) is the PSP's limit: in a size probe with the XMB camera every frame up to that size played, and the PSP stopped the video at the first frame of 32 768 bytes (37 packets) [HW]. the game's own maximum stays a ceiling, because only the XMB camera was probed. `bandwidth_cap` is the biggest whole-packet frame that can be sent in 80 % of the frame period (the 80 % is chosen, not measured); a frame takes 4.6 ms + 1.02 ms per packet to send [HW]: 42 112 bytes at 15 fps, 18 816 at 30 fps. the webcam path picks the highest JPEG quality (1..100) that fits (`fit_quality`). seen on hardware without failed frames [HW]: the XMB camera and Go!Edit (JP) at 15 fps, EyePet and Go!Edit (JP) at 30 fps. frames are fitted to the budget; at the lowest JPEG quality an oversize frame can still be sent.
- JPEG: baseline, YCbCr 4:2:0 (OpenCV default), JFIF header kept, standard Huffman tables [HW]. optimised Huffman tables, 4:2:2 and restart markers were never tried on the PSP [UNV].

## 6. setters

none of these is answered. they change the picture on the PC. values as sent by the PSP; "psychea" shows what the code does.

| cmd | setting | wire value | psychea [HW unless marked] |
|---|---|---|---|
| 0x05 | zoom | 0, or 10..80 (tenths) | digital centre crop of 1/f and resize; <= 10 means no zoom |
| 0xAC | image effect | 0 normal, 1 negative, 2 black and white, 3 sepia, 4 blue, 5 red, 6 green | as named; 4-6 are a grey picture tinted blue / red / green |
| 0xA6 | contrast | 0..255, 128 neutral (the XMB app sends 0x80) | factor 259(c+255)/(255(259-c)) around 128, c = (v-128)*2 |
| 0xA5 | brightness | **sign + magnitude**: 0 neutral, `k` = +k, `0x80` plus `k` = -(k+1) | converted to 128 + value; added to the picture |
| 0xA4 | saturation | byte = value / 42, 0..6; neutral is 3 **[UNV: neutral is a guess]**; games and the XMB start with the value 125, which is byte 2, not 3 [HW] | luma blend, factor byte/3 |
| 0xA7 | sharpness | 0..3 (the XMB app and EyePet start at 0) | unsharp mask, 0.5 per level [UNV: strength chosen by us] |
| 0xAF | exposure | 0..16, 0 = +2.0 EV .. 8 = 0.0 .. 16 = -2.0; the XMB app uses 4..16 around 10 | gain 2^((10-code)/4) in linear light (gamma 2.2); lower code is brighter |
| 0xA3 | white balance | 0 auto, 1 daylight, 2 fluorescent, 3 incandescent (also byte 5 of START_VIDEO) | 2 and 3: colour gains for 4000 K and 2800 K light against 6500 K; 0 and 1 unchanged [HW for codes 0-2, 3 not seen; the colour is not compared with the real camera] |
| 0xAB | mirror, flip | `[flip][mirror]`, bit 0 of each byte | flip and mirror applied by psychea |

observed [HW]: `02 00 AB 02 00 01` (cmd 0xAB, payload `00 01`) = mirror on, flip off. the PSP sends it at every
stream start and sends `02 00 A9 01 00` (anti-flicker 0) before START_VIDEO.
the XMB camera app sends zoom, effect, exposure, contrast 0x80 and mirror/flip; brightness, saturation and sharpness have been seen only from games [HW].

## 7. still photos

used by the XMB camera app and by games that take photos (Go!Edit (JP) took a 480x272 photo through psychea [HW]). the flow in trailer mode [HW; photos are saved and the preview works afterwards]:

1. the PSP stops the video (STOP_VIDEO), may re-run HELLO, then sends **START_STILL_SETUP** (id 2, cmd 3, 20 bytes) and then waits for one picture on the video pipe.
2. psychea: IF3 alt 1, wait 50 ms (so the PSP is listening), build one picture, send it as video framing with trailer byte 9 = 1, wait 100 ms, **IF3 alt 0**.
3. the PSP later sends STOP_VIDEO_4 (cmd 0x04) and, to return to the preview, a new START_VIDEO. without the alt 0 after a photo the camera app hangs when it returns to the preview [HW].

payload of cmd 3 [DIS; the used bytes also HW]. psychea uses these bytes and ignores the rest:

| byte | meaning |
|---|---|
| 0-3 | u32 LE size of the PSP's picture buffer (the largest JPEG it asks for) |
| 7 | **output size code**; the XMB app's 480 and 640 photo sizes arrive as 07 and 02 |

byte 6 is not the size: using it as the size once made every photo 640x480 [HW].

photo picture: the newest raw webcam frame, centre-cropped to the size's aspect, resized, the same adjustments as video, JPEG starting at quality 92 and
decreasing by 10 down to 10 until it fits `still_budget = max(20000, min(60000, buffer - 4096))` bytes. the cap of 60 000 keeps a photo below 0xFC00 (64 512) bytes, the PSP's photo chunk size [DIS]. sizes above the webcam frame (640x480 unless `--camres`) are
enlarged from it. a 1280x960 photo works [HW]: with the plugin key `photos_1280x960=1` the XMB app's `SetupStillEx` resolution is rewritten to 1280x960 (the app itself only asks for 640x480 and 480x272); the photos (about 50 KB, split into consecutive writes where they exceed the 48 KB slot) were saved by the app and the preview came back. the extra detail is only 1.4 % (the picture is stretched from 640x480), so there is nothing to gain without a bigger webcam frame. **trailer byte 9 must be 1** [HW: with 0 the PSP froze].

## 8. video frames: isochronous EP4

1. on START_VIDEO the host selects **IF3 alt 1** (SET_INTERFACE, bmRequestType 0x01, bRequest 0x0B, wValue 1, wIndex 3).
2. the host sends one JPEG per frame as isochronous OUT on EP4 (0x04), 896-byte packets (`PKT`).
3. on STOP_VIDEO (cmd 0x02 or 0x04) the host selects **IF3 alt 0**; the PSP waits for it [DIS].

### frame layout in trailer mode [HW]

```
data   : JPEG (FF D8 ... FF D9), zero-padded to a whole number of 896-byte packets      -> n packets of 896 B
trailer: 16 bytes in a packet of its own
         [0..1]  FF FE
         [2..3]  00 0A            (a JPEG COM segment length; must be 0x000A)
         [4..7]  u32 LE           number of data bytes sent before the trailer (the padded length)
         [8]     00
         [9]     sequence byte    0 for video; 1 for a photo (section 7)
         [10..13] 00 00 00 00
         [14..15] FF D9
```

the PSP expects [DIS]: the first packet starts with `FF D8`; the frame ends at a packet that is exactly 16 bytes and starts with
`FF FE` and ends with `FF D9`; the u32 equals the number of data bytes received, otherwise the frame is dropped silently. in the size probe the XMB camera accepted every frame up to 32 256 bytes (36 packets) and the PSP itself sent STOP_VIDEO about 130 ms after the first frame of 32 768 bytes (37 packets); sizes in between were not tried [HW].

psychea pads the JPEG with zeros to whole packets, so a photo or frame carries up to 895 zero bytes after FF D9 (decode fine [HW]). photos larger than the Windows slot (48 KB) are sent as several consecutive writes split on packet boundaries.

**rate:** one packet per millisecond, 896 B, about 896 kB/s at most. measured send time of a frame: 4.6 ms + 1.02 ms per packet [HW]. measured video: 15.0 and 30.0 fps steady [HW].

## 9. stopping and restarting

- STOP_VIDEO: IF3 alt 0. the webcam keeps running between starts; on a new START_VIDEO psychea drops the last picture and re-encodes at the new size.
- unplug: psychea sees `UsbGone` or notices that 054C:0282 has left the bus (checked once a second), ends the session and waits for the next plug-in.
- mic and camera are independent; either may restart without the other.

## 10. microphone

### 10.1 START_MIC (id 1, cmd 0x01, 14 bytes)

| bytes | value | meaning |
|---|---|---|
| 0-1 | alc | `sceUsbCamSetupMic` parameter (automatic level control); psychea only logs it |
| 2-3 | gain | parameter; EyePet sends 30 (0x1E); psychea only logs it |
| 4-5 | noise | parameter; logged only |
| 6-7 | 0 | not read |
| 8-9 | 3 | not read |
| 10-11 | 2 | not read |
| 12-13 | rate / 1000 | 11, 22, 44 or 48 (u16 LE) |

EyePet's message: `01 00 01 0E | 00 00 1E 00 00 00 00 00 03 00 02 00 16 00` = alc 0, gain 30, noise 0, **22050 Hz** [HW]. bytes 6-11 were `00 00`, `03 00`, `02 00` in it. psychea maps 11/22/44/48 to
11025/22050/44100/48000 and uses 22050 for any other value. 11025 Hz was asked by Invizimals: The Lost Tribes and ran 5 minutes without a failed audio packet [HW]; the XMB asks 22050 Hz. psychea streams at the announced rate.

**START_MIC_EX (cmd 0x03, 15 bytes)** is parsed like START_MIC. every audio packet then carries a **2-byte header**
before the samples; psychea sends `00 00` + up to 62 samples. the meaning of the header
and whether any game uses this form are [UNV]; no hardware run has used it.

STOP_MIC (cmd 0x02, no payload). on every START_MIC psychea first stops the running stream.

### 10.2 The USB side

1. keep **IF2 at alt 0** until START_MIC arrives. if the host selected alt 1 earlier, the PSP's `StartMic` fails with 0x80243909 and **no message is sent** [DIS].
2. on START_MIC: SET_INTERFACE IF2 alt 1 (bmRequestType 0x01, bRequest 0x0B, wValue 1, wIndex 2).
3. stream on **EP2 (0x02)**, isochronous OUT, 1 ms per packet.
4. on STOP_MIC: stop sending and **always** SET_INTERFACE IF2 alt 0 (wValue 0, wIndex 2), also if already at alt 0. without it the PSP's microphone stays in the "stopped"
   state and the next StartMic fails with 0x80243909 [DIS; the stop and restart sequence is HW]. **on Windows** the registered iso buffer must be closed before the alt 0, else Windows answers error 170 (psychea does this [HW]).
5. no reply to a microphone message, and no USB Audio Class requests (GET_CUR/SET_CUR, volume, mute).

### 10.3 Sample format [HW]

- 16-bit signed PCM, **little-endian**, mono, no header (normal form). confirmed with a 1 kHz sine at +-7999 on the PSP side and later with live voice.
- samples per millisecond = rate / 1000 on average; psychea keeps an accumulator so that exactly `rate` samples go out per 1000 packets: for 22050 Hz that is 22 samples (44 B)
  with 23 (46 B) every 20th packet. at most 63 samples (126 bytes) per packet in the normal form, 62 in the extended form (plus the 2 header bytes).
- never pause while IF2 is at alt 1: the PSP waits for the samples [DIS].

### 10.4 psychea's audio chain

PC microphone (sounddevice, low-latency stream) -> 100 Hz DC blocker and a 2nd-order 10 kHz low-pass (at most 0.45 of the sample rate) -> `--micgain` -> soft limiter
(linear to 70 % of full scale, then tanh) -> packets of 1 ms.

- **delay.** the Windows USB side keeps 24 ms queued (`DEPTH`), the PC side buffers a few ms; measured delay 27-42 ms, not growing over minutes [HW]. when the PC buffer stays above 40 ms the source reads one extra sample every 10th ms and merges it with its neighbour (under 1 % faster) until the buffer is down to 25 ms; a pile-up beyond 100 ms is cut at once to 20 ms. at every stream (re)start the buffer is cut to 20 ms first. the 40 ms threshold was not reached in the hardware logs, so the gradual drain has **not** been seen working on hardware [UNV].
- **WASAPI.** on Windows a microphone is listed once per audio API (`--list`). WASAPI shared mode only opens the rate Windows has set; opening the device at 22050 Hz failed with PaErrorCode -9997 and psychea fell back to the test tone. WASAPI devices are opened with `auto_convert` so Windows converts the rate [HW: delay 31-33 ms, 0 failures]. other APIs are opened as before; the default device is still MME, so use `--micdev N` with the WASAPI number.
- **failures.** a failed 1 ms write restarts the stream. statuses are named in the log (0xC000000D STATUS_INVALID_PARAMETER, 0x80000011 STATUS_DEVICE_BUSY, 0xC0000120 STATUS_CANCELLED). isolated 0xC000000D failures occur during game sessions; the cause is unknown [HW]. 100 failures in a row stop the audio once (`mic_gave_up`, ERROR).
- **unplug.** when the cable is pulled the first failed write is 0xC0000120, then every write says 0x80000011. a failure streak that begins with 0xC0000120 is logged at DEBUG while each failure is 0xC0000120 or 0x80000011; any other status is logged as WARNING/ERROR again, and a streak that was quiet but ends with successful writes is reported afterwards as `mic_recovered` (WARNING). leaving the camera screen is quiet with this rule [HW]; a pulled cable has not been tried with it [UNV].

## 11. what is verified and what is not

verified on hardware (Windows host, PSP Street (E1000) with ARK-4 and `psychea`; other PSP models not tried) [HW]: the handshake; the HELLO and type replies; START_VIDEO and trailer-mode frames at 15 and 30 fps; size switching, effects, zoom, contrast, exposure, mirror / flip and white balance codes 0-2 from the XMB app; photos at 480x272, 640x480 and 1280x960 (with the plugin key); EyePet, Go!Edit (JP), Invizimals: The Lost Tribes (video and microphone at 11025 and 22050 Hz); microphone stop and restart; the two PSP-side patches; recording video in the XMB app.

not verified [UNV]:

- the Linux / libusb backend (tests only).
- brightness, saturation and sharpness: applied from the wire values, sent only by games; the neutral point of saturation (3) is a guess, and the strength of the sharpening is chosen by us.
- the fluorescent and incandescent colour correction against the real camera.
- the replies to 0x40 and 0x43 (never asked in any log), the extended microphone form (cmd 3) and its 2-byte header, 44100 and 48000 Hz microphone.
- optimised Huffman tables, 4:2:2 JPEGs and restart markers.
- the gradual drain of the PC microphone buffer above 40 ms (never reached).
- `xmb_camera=0` (XMBPASS off), and `--install-driver` removing the first-plug error 433.
- COM mode and bulk stills are not implemented.

## 12. open questions

- the meaning of the START_VIDEO bytes psychea does not read and of the anti-flicker values. the fields of `SetupVideoEx` behind them are constants in every call seen (XMB, EyePet, Go!Edit, Invizimals) [HW].
- what retail cameras (PSP-300, PSP-450x) answer to the type query and HELLO.
- what sets the PSP's accessory-power flag on an unmodified PSP; without it a standalone camera needs the PSP-side patch.
- how the real camera behaves when the JPEG size differs from the request.
