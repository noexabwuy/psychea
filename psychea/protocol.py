import struct
from dataclasses import dataclass
from typing import Optional

VID, PID = 0x054C, 0x0282
INFO = bytes.fromhex("4C055B0201100100")            # accessory info sent after the handshake read
PKT = 896                                           # iso packet size of IF3 alt 1 / EP4
EP_INT_IN, EP_ISO_VIDEO, EP_ISO_MIC = 0x81, 0x04, 0x02
IF_CONTROL, IF_MIC, IF_VIDEO = 0, 2, 3
EP_INT_SIZE = 64                                     # max packet size of EP1: the biggest message the PSP sends
# Video frame limit of the PSP: every frame up to 36 packets (32256 bytes) is shown; the first frame of 37 packets
# (32768 bytes) stops the video.
PSP_FRAME_LIMIT = 36 * PKT
FRAME_MARGIN = 1500                                  # kept below the size the game asks for (never measured)
ISO_DUTY, ISO_OVERHEAD_MS, ISO_MS_PER_PACKET = 0.8, 4.6, 1.02       # sending a frame: 4.6 ms + 1.02 ms per packet
RES_CODES = {0: (1280, 1024), 1: (1024, 768), 2: (640, 480), 3: (352, 288), 4: (320, 240),
             5: (176, 144), 6: (160, 120), 7: (480, 272), 8: (360, 272), 9: (1280, 960)}
FPS_CODES = (3.75, 5, 7.5, 10, 15, 20, 30, 60)       # StartVideo byte 2
DEFAULT_FPS, MAX_FPS = 15.0, 30.0
MIC_RATES = {11: 11025, 22: 22050, 44: 44100, 48: 48000}
# bmRequestType and bRequest of the control transfers (docs/protocol.md sections 2 and 4)
REQ_VENDOR_IN, REQ_VENDOR_OUT = 0xC1, 0x41           # vendor request, recipient = interface
REQ_HANDSHAKE = 0x01                                 # bRequest of the handshake pair (IN wValue 3, then OUT = INFO)
HANDSHAKE_IN_VALUE = 0x0003
REQ_REPLY = 9                                        # bRequest of our replies to the PSP's commands
CONTROL_TIMEOUT_MS = 1000

# PSP messages are [u16 id][u8 cmd][u8 len][payload] (section 3); id 1 = microphone, 2 = camera, 3 = queries
MSG_HEADER_LEN = 4
ID_MIC, ID_CAMERA, ID_QUERY = 1, 2, 3
CMD_START_MIC, CMD_STOP_MIC, CMD_START_MIC_EX = 0x01, 0x02, 0x03
CMD_START_VIDEO, CMD_STOP_VIDEO, CMD_START_STILL_SETUP, CMD_STOP_VIDEO_4 = 0x01, 0x02, 0x03, 0x04
CMD_HELLO, CMD_QUERY_0X40, CMD_SET_0X41, CMD_FLIP_MIRROR = 0x2B, 0x40, 0x41, 0xAB
CMD_CAMERA_TYPE, CMD_QUERY_0X43, CMD_LENS_0X44 = 0x09, 0x43, 0x44                      # id 3
# camera settings the game may send (the sceUSBCam_Driver setting ids)
CMD_ZOOM, CMD_RESOLUTION = 0x05, 0xA0
CMD_WHITE_BALANCE, CMD_SATURATION, CMD_BRIGHTNESS, CMD_CONTRAST, CMD_SHARPNESS = 0xA3, 0xA4, 0xA5, 0xA6, 0xA7
CMD_ANTI_FLICKER, CMD_IMAGE_EFFECT, CMD_EV_LEVEL = 0xA9, 0xAC, 0xAF
MESSAGE_NAMES = {
    # answered or acted on by Session
    (ID_CAMERA, CMD_HELLO): "HELLO", (ID_CAMERA, CMD_FLIP_MIRROR): "SET_FLIP_MIRROR",
    (ID_QUERY, CMD_CAMERA_TYPE): "CAMERA_TYPE_QUERY",
    (ID_CAMERA, CMD_QUERY_0X40): "QUERY_0x40", (ID_QUERY, CMD_QUERY_0X43): "QUERY_0x43",
    (ID_CAMERA, CMD_START_VIDEO): "START_VIDEO", (ID_CAMERA, CMD_STOP_VIDEO): "STOP_VIDEO",
    (ID_CAMERA, CMD_STOP_VIDEO_4): "STOP_VIDEO_4", (ID_MIC, CMD_START_MIC): "START_MIC",
    (ID_MIC, CMD_START_MIC_EX): "START_MIC_EX", (ID_MIC, CMD_STOP_MIC): "STOP_MIC",
    # camera settings the game may send; the real camera needs no reply to them, so they are only named in the log
    (ID_CAMERA, CMD_START_STILL_SETUP): "START_STILL_SETUP", (ID_CAMERA, CMD_ZOOM): "SET_ZOOM",
    (ID_CAMERA, CMD_RESOLUTION): "SET_RESOLUTION",
    (ID_CAMERA, CMD_SATURATION): "SET_SATURATION", (ID_CAMERA, CMD_BRIGHTNESS): "SET_BRIGHTNESS",
    (ID_CAMERA, CMD_CONTRAST): "SET_CONTRAST",
    (ID_CAMERA, CMD_SHARPNESS): "SET_SHARPNESS", (ID_CAMERA, CMD_ANTI_FLICKER): "SET_ANTI_FLICKER",
    (ID_CAMERA, CMD_IMAGE_EFFECT): "SET_IMAGE_EFFECT",
    (ID_CAMERA, CMD_EV_LEVEL): "SET_EV_LEVEL", (ID_CAMERA, CMD_WHITE_BALANCE): "SET_WHITE_BALANCE",
    (ID_QUERY, CMD_LENS_0X44): "SET_LENS_0x44", (ID_CAMERA, CMD_SET_0X41): "SET_0x41",
}

# Field positions in the payloads (docs/protocol.md sections 5, 7 and 10)
SV_LEN, SV_FULL_LEN = 30, 32                         # StartVideo: bytes up to the JPEG limit / the whole config
SV_RESOLUTION, SV_FPS, SV_WHITE_BALANCE, SV_EFFECT, SV_ZOOM, SV_MAX_JPEG = 1, 2, 5, 18, 20, 26
GAME_JPEG_LIMITS = (2000, 200_000)                   # a JPEG limit outside this is not believed (0 = none given)
STILL_LEN, STILL_BUFFER, STILL_RESOLUTION = 8, 0, 7  # StillSetup: bytes needed, u32 buffer size at 0, resolution at 7
MIC_LEN, MIC_ALC, MIC_GAIN, MIC_NOISE, MIC_RATE = 14, 0, 2, 4, 12     # StartMic: u16 rate in kHz at 12
DEFAULT_SIZE = (480, 272)                            # the PSP screen; used when a resolution code is unknown
DEFAULT_MIC_RATE = 22050
# Picture settings: the code at which nothing is changed (what the XMB app sends at start; saturation's is a guess)
CONTRAST_NEUTRAL = BRIGHTNESS_NEUTRAL = 128
EV_NEUTRAL = 10                                      # exposure code 4..16, lower = brighter
SATURATION_NEUTRAL = 3
ZOOM_NEUTRAL, ZOOM_MAX = 10, 80                      # zoom code = 10 x the factor (0 = off); 8.0x at most
CAMERA_TYPE = 0x0500                                # camera-type reply: frames end with a trailer (0 breaks the API)
DEFAULT_MAX_JPEG = 15000


# ------------------------------------------------------------------ frames

# the 16-byte trailer: head, u32 size, 00, seq, 4 x 00, tail
TRAILER_HEAD, TRAILER_TAIL = b"\xff\xfe\x00\x0a", b"\xff\xd9"

def build_frame_trailer(jpeg: bytes, pkt: int, seq: int) -> tuple[bytes, bytes]:
    if len(jpeg) % pkt:
        jpeg = jpeg + b"\x00" * (pkt - len(jpeg) % pkt)
    trailer = TRAILER_HEAD + struct.pack("<I", len(jpeg)) + b"\x00" + bytes([seq]) + b"\x00" * 4 + TRAILER_TAIL
    return jpeg, trailer


def packet_lengths(n: int, pkt: int = PKT) -> list[int]:
    full, rem = divmod(n, pkt)
    return [pkt] * full + ([rem] if rem else [])


def video_frame(jpeg: bytes, seq: int = 0) -> tuple[bytes, list[int]]:
    body, trailer = build_frame_trailer(jpeg, PKT, seq)
    return body + trailer, packet_lengths(len(body)) + [len(trailer)]


def chunk_packets(data: bytes, lens: list[int], limit: int) -> list[bytes]:
    chunks, start, size = [], 0, 0
    for n in lens:
        if size and size + n > limit:
            chunks.append(data[start:start + size])
            start, size = start + size, 0
        size += n
    if size:
        chunks.append(data[start:start + size])
    return chunks


# ------------------------------------------------------------------ messages from the PSP (EP1)

@dataclass(frozen=True)
class Message:
    ident: int
    cmd: int
    payload: bytes


def parse_message(data: bytes) -> Optional[Message]:
    if len(data) < MSG_HEADER_LEN:
        return None
    ident, cmd, ln = struct.unpack("<HBB", data[:MSG_HEADER_LEN])
    return Message(ident, cmd, bytes(data[MSG_HEADER_LEN:MSG_HEADER_LEN + ln]))


@dataclass(frozen=True)
class VideoRequest:
    width: int
    height: int
    fps_code: int
    max_size: int          # 0 when the message carries no usable limit

    @property
    def fps(self) -> float:
        return float(FPS_CODES[self.fps_code]) if self.fps_code < len(FPS_CODES) else DEFAULT_FPS


def parse_start_video(cfg: bytes) -> Optional[VideoRequest]:
    if len(cfg) < SV_LEN:
        return None
    w, h = RES_CODES.get(cfg[SV_RESOLUTION], DEFAULT_SIZE)
    mx = struct.unpack_from("<I", cfg, SV_MAX_JPEG)[0]
    low, high = GAME_JPEG_LIMITS
    return VideoRequest(w, h, cfg[SV_FPS], mx if low <= mx <= high else 0)


@dataclass(frozen=True)
class StillRequest:
    w: int
    h: int
    max_size: int


def parse_still_setup(p: bytes) -> Optional[StillRequest]:
    if len(p) < STILL_LEN or p[STILL_RESOLUTION] not in RES_CODES:
        return None
    w, h = RES_CODES[p[STILL_RESOLUTION]]
    return StillRequest(w, h, struct.unpack_from("<I", p, STILL_BUFFER)[0])


STILL_MAX_BYTES = 60_000           # one photo chunk must stay below 0xFC00 (64512) bytes: the PSP's chunk size
STILL_MIN_BYTES = 20_000           # a photo gets at least this, whatever the buffer size says
STILL_BUFFER_MARGIN = 4096


def still_budget(max_size: int) -> int:
    return max(STILL_MIN_BYTES, min(STILL_MAX_BYTES, max_size - STILL_BUFFER_MARGIN))


def bandwidth_cap(fps: float) -> int:
    return int((ISO_DUTY * 1000 / fps - ISO_OVERHEAD_MS) / ISO_MS_PER_PACKET) * PKT


def jpeg_budget(max_size: int, fps: float, current: int) -> int:
    if not max_size:
        return current
    return int(min(max_size - FRAME_MARGIN, PSP_FRAME_LIMIT, bandwidth_cap(fps)))


@dataclass(frozen=True)
class MicRequest:
    alc: int
    gain: int
    noise: int
    rate: int


def parse_start_mic(p: bytes) -> Optional[MicRequest]:
    if len(p) < MIC_LEN:
        return None
    return MicRequest(alc=p[MIC_ALC], gain=p[MIC_GAIN], noise=struct.unpack_from("<h", p, MIC_NOISE)[0],
                      rate=MIC_RATES.get(struct.unpack_from("<H", p, MIC_RATE)[0], DEFAULT_MIC_RATE))
