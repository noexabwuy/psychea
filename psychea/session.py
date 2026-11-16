import struct
import time
from typing import Callable, Optional, Union

from . import logs
from .protocol import (
    BRIGHTNESS_NEUTRAL,
    CAMERA_TYPE,
    CMD_BRIGHTNESS,
    CMD_CAMERA_TYPE,
    CMD_CONTRAST,
    CMD_EV_LEVEL,
    CMD_FLIP_MIRROR,
    CMD_HELLO,
    CMD_IMAGE_EFFECT,
    CMD_QUERY_0X40,
    CMD_QUERY_0X43,
    CMD_SATURATION,
    CMD_SHARPNESS,
    CMD_START_MIC,
    CMD_START_MIC_EX,
    CMD_START_STILL_SETUP,
    CMD_START_VIDEO,
    CMD_STOP_MIC,
    CMD_STOP_VIDEO,
    CMD_STOP_VIDEO_4,
    CMD_WHITE_BALANCE,
    CMD_ZOOM,
    DEFAULT_FPS,
    ID_CAMERA,
    ID_MIC,
    ID_QUERY,
    MAX_FPS,
    MESSAGE_NAMES,
    SV_EFFECT,
    SV_FULL_LEN,
    SV_WHITE_BALANCE,
    SV_ZOOM,
    Message,
    MicRequest,
    StillRequest,
    VideoRequest,
    jpeg_budget,
    parse_message,
    parse_start_mic,
    parse_start_video,
    parse_still_setup,
    still_budget,
    video_frame,
)
from .settings import Settings
from .sources import Output, TestPattern, Webcam, make_source
from .stats import VideoStats
from .transport import Transport, UsbError, UsbGone

log = logs.get("session")
MAX_ISO_ERRORS = 5
PRESENCE_PERIOD = 1.0
PHOTO_LEAD_S, PHOTO_TAIL_S = 0.05, 0.1     # photo: wait for the PSP to listen before sending / to take the last packets
VIDEO_RETRY_PAUSE_S = 0.1
IDLE_POLL_MS = 10                          # how long to wait for a PSP message while no video is streaming
FRAME_SLICE_S = 0.010                      # while streaming, wait for messages at most this long before the next frame
IDLE_SLEEP_S = 0.001                       # a loop pass without video and without work
LOG_FIRST_FRAMES = 3                       # frames logged one by one at the start of a stream
READ_ERROR_LOG_GAP_S = 2.0                 # a failing message read is logged at most this often


class Session:
    def __init__(self, transport: Transport, settings: Settings, present: Callable[[], bool],
                     out: Optional[Output] = None) -> None:
        self.t, self.settings, self.present = transport, settings, present
        self.out = out or Output(mirror_extra=int(settings.mirror))
        self.fps = DEFAULT_FPS            # until the game says what it wants
        self.stats = VideoStats(time.monotonic())
        self.streaming = False
        self.gone = False
        self.frames = 0                       # of the current camera start
        self.frames_total = 0                 # of the earlier starts in this session
        self.iso_errors = 0
        self.source: Union[Webcam, TestPattern, None] = None
        self.next_frame_at = 0.0
        self.last_read_error = float("-inf")
        self.unknown_seen: set[tuple[int, int]] = set()
        self.handlers: dict[tuple[int, int], Callable[[Message], None]] = {
            (ID_CAMERA, CMD_HELLO): self.on_hello, (ID_CAMERA, CMD_FLIP_MIRROR): self.on_flip_mirror,
            (ID_QUERY, CMD_CAMERA_TYPE): self.on_type_query,
            (ID_CAMERA, CMD_QUERY_0X40): self.on_query, (ID_QUERY, CMD_QUERY_0X43): self.on_query,
            (ID_CAMERA, CMD_START_VIDEO): self.on_start_video,
            (ID_CAMERA, CMD_STOP_VIDEO): self.on_stop_video, (ID_CAMERA, CMD_STOP_VIDEO_4): self.on_stop_video,
            (ID_CAMERA, CMD_START_STILL_SETUP): self.on_still_setup,
            (ID_MIC, CMD_START_MIC): self.on_start_mic, (ID_MIC, CMD_START_MIC_EX): self.on_start_mic,
            (ID_MIC, CMD_STOP_MIC): self.on_stop_mic,
        }
        for cmd in self.SETTINGS:                 # one table decides which commands are picture settings
            self.handlers[(ID_CAMERA, cmd)] = self.on_setting

    # --- PSP -> us
    def dispatch(self, data: bytes) -> None:
        msg = parse_message(data)
        if msg is None:
            log.warning("Ignored a too-short message from the PSP", event="psp_message_short", raw=bytes(data).hex(" "))
            return
        name = MESSAGE_NAMES.get((msg.ident, msg.cmd), "UNKNOWN")
        log.debug("PSP message %s" % name, event="psp_message", id=msg.ident, cmd="0x%02X" % msg.cmd,
                  len=len(msg.payload), payload=msg.payload.hex(" "))
        handler = self.handlers.get((msg.ident, msg.cmd))
        if handler:
            self.run_handler(handler, msg, name)
        elif (msg.ident, msg.cmd) not in MESSAGE_NAMES and (msg.ident, msg.cmd) not in self.unknown_seen:
            self.unknown_seen.add((msg.ident, msg.cmd))
            log.info("The PSP sent a message this program does not know (id=%d cmd=0x%02X); ignoring it" % (
                msg.ident, msg.cmd), event="psp_message_unknown", payload=msg.payload.hex(" "))

    def run_handler(self, handler: Callable[[Message], None], msg: Message, name: str) -> None:
        try:
            handler(msg)
        except UsbGone:
            self.lose()
        except Exception as e:
            log.error("Could not handle the PSP message %s: %r" % (name, e), event="psp_message_failed",
                      exc_info=True, id=msg.ident, cmd="0x%02X" % msg.cmd)

    def lose(self) -> None:
        if not self.gone:
            self.gone = True
            log.info("The PSP disconnected", event="unplugged", how="usb_error")

    def lost(self, e: UsbError) -> bool:
        if isinstance(e, UsbGone):
            self.lose()
        return isinstance(e, UsbGone)

    def reply(self, cmd: int, data: bytes) -> None:
        try:
            self.t.reply(cmd, data)
            log.debug("Answered PSP command 0x%02X" % cmd, event="reply", cmd="0x%02X" % cmd, data=data.hex(" "))
        except UsbGone:
            self.lose()
        except UsbError as e:
            log.warning("Could not answer PSP command 0x%02X: %s" % (cmd, e), event="reply_failed",
                        cmd="0x%02X" % cmd, error=e)

    def on_hello(self, msg: Message) -> None:
        self.reply(CMD_HELLO, bytes([self.out.flip, self.out.mirror, 1, 0]))        # flip, mirror, lens normal

    def on_flip_mirror(self, msg: Message) -> None:
        if len(msg.payload) >= 2:
            self.out.flip, self.out.mirror = msg.payload[0] & 1, msg.payload[1] & 1
            log.info("Game set the picture orientation: mirror %s, flip %s" % (
                "on" if self.out.mirror else "off", "on" if self.out.flip else "off"),
                event="orientation", mirror=self.out.mirror, flip=self.out.flip)

    SETTINGS = {CMD_ZOOM: ("zoom", "Zoom"), CMD_IMAGE_EFFECT: ("effect", "Image effect"),
                CMD_CONTRAST: ("contrast", "Contrast"), CMD_BRIGHTNESS: ("brightness", "Brightness"),
                CMD_SATURATION: ("saturation", "Saturation"), CMD_EV_LEVEL: ("ev", "Exposure"),
                CMD_WHITE_BALANCE: ("wb", "White balance"), CMD_SHARPNESS: ("sharpness", "Sharpness")}

    def on_setting(self, msg: Message) -> None:
        if not msg.payload:
            return
        field, label = self.SETTINGS[msg.cmd]
        value = msg.payload[0]
        if msg.cmd == CMD_BRIGHTNESS:
            value = BRIGHTNESS_NEUTRAL + (-1 - (value & 0x7F) if value & 0x80 else value)      # wire: sign + magnitude
        setattr(self.out, field, value)
        log.info("Game set %s to %d" % (label.lower(), value), event="setting", name=field, value=value)

    def on_type_query(self, msg: Message) -> None:
        self.reply(CMD_CAMERA_TYPE, struct.pack("<H", CAMERA_TYPE))

    def on_query(self, msg: Message) -> None:
        self.reply(msg.cmd, b"\x00\x00\x00\x00")                               # four zero bytes; no program has asked yet

    def on_start_video(self, msg: Message) -> None:
        req = parse_start_video(msg.payload)
        if req:
            self.apply_video_request(msg.payload, req)
        else:
            log.warning("The PSP sent a start-video request that is too short; keeping the earlier picture settings",
                        event="video_request_unusable", payload=msg.payload.hex(" "))
        try:
            self.t.set_video_alt(1)
        except UsbError as e:
            self.streaming = False                    # the transport has dropped its video buffer: nothing to send on
            if not self.lost(e):
                log.error("Could not start the video stream: %s" % e, event="video_start_failed", error=e)
            return
        self.streaming = True
        self.next_frame_at = time.monotonic()
        self.stats.restart(self.next_frame_at)        # fps is counted over the time the camera really runs
        self.frames_total += self.frames
        self.frames = self.iso_errors = 0
        if self.source is not None:
            self.source.reset()
        self.tell_source()
        log.debug("Video interface switched on (IF3 alt 1)", event="video_alt", alt=1)

    def apply_video_request(self, cfg: bytes, req: VideoRequest) -> None:
        self.out.w, self.out.h = req.width, req.height
        if len(cfg) >= SV_FULL_LEN:
            # white balance, effect and zoom are in the config too (docs/protocol.md, section 5)
            self.out.wb, self.out.effect, self.out.zoom = cfg[SV_WHITE_BALANCE], cfg[SV_EFFECT], cfg[SV_ZOOM]
            log.info("Game set white balance to %d" % self.out.wb, event="setting", name="wb", value=self.out.wb)
        self.fps = min(req.fps, MAX_FPS)
        self.out.max_jpeg = jpeg_budget(req.max_size, self.fps, self.out.max_jpeg)
        log.info("Game started the camera: %dx%d at %g fps, frames up to %.1f KB" % (
            req.width, req.height, self.fps, self.out.max_jpeg / 1024),
            event="video_start", w=req.width, h=req.height, asked_fps=req.fps, fps=self.fps,
            game_max_jpeg=req.max_size, max_jpeg=self.out.max_jpeg)

    def on_still_setup(self, msg: Message) -> None:
        req = parse_still_setup(msg.payload)
        if req is None or self.source is None:
            log.warning("The PSP asked for a photo, but the request could not be used", event="still_unusable",
                        payload=msg.payload.hex(" "))
            return
        self.streaming = False
        try:
            jpeg, bad = self.send_photo(self.source, req)
        except UsbGone:
            self.lose()
            return
        except Exception as e:                    # a UsbError, or the camera had no picture for us
            log.error("Could not send the photo: %s" % e, event="still_failed", error=e)
            self.video_interface_off()
            return
        try:
            self.t.set_video_alt(0)               # without this the camera app hangs when it goes back
        except UsbError as e:
            if not self.lost(e):
                log.warning("Could not switch the video interface off: %s" % e, event="still_stop_failed", error=e)
        log.info("Game took a photo: %dx%d, %.0f KB%s" % (req.w, req.h, len(jpeg) / 1024.0,
                                                          ", the transfer failed" if bad else ""),
                 event="still", w=req.w, h=req.h, bytes=len(jpeg), buffer=req.max_size, failed=bad)

    def send_photo(self, source: Union[Webcam, TestPattern], req: StillRequest) -> tuple[bytes, int]:
        self.t.set_video_alt(1)
        time.sleep(PHOTO_LEAD_S)              # let the PSP start listening before the picture comes
        jpeg = source.still(req.w, req.h, still_budget(req.max_size))
        data, lens = video_frame(jpeg, seq=1)
        bad = self.t.send_video(data, lens)
        time.sleep(PHOTO_TAIL_S)              # the PSP has taken the last packets
        return jpeg, bad

    def video_interface_off(self) -> None:
        try:
            self.t.set_video_alt(0)
        except UsbError as e:
            self.lost(e)

    def on_stop_video(self, msg: Message) -> None:
        self.streaming = False
        self.stats.restart(time.monotonic())
        try:
            self.t.set_video_alt(0)
            log.info("Game stopped the camera", event="video_stop", frames=self.frames)
        except UsbError as e:
            if not self.lost(e):
                log.warning("Could not switch the video interface off: %s" % e, event="video_stop_failed", error=e)

    def on_start_mic(self, msg: Message) -> None:
        req = parse_start_mic(msg.payload)
        if req is None:
            log.warning("The PSP sent a start-microphone request that is too short; no audio started",
                        event="mic_request_unusable", payload=msg.payload.hex(" "))
            return
        extended = msg.cmd == CMD_START_MIC_EX
        log.info("Game asked for the microphone: %d Hz, gain %d, alc %d, noise %d%s" % (
            req.rate, req.gain, req.alc, req.noise, ", extended framing" if extended else ""),
            event="mic_request", rate=req.rate, gain=req.gain, alc=req.alc, noise=req.noise, extended=extended)
        if self.settings.mic == "off":
            log.info("Game started the microphone; audio is off (--mic off)", event="mic_start", source="off")
            return
        self.t.stop_mic()
        self.open_mic(req, extended)

    def open_mic(self, req: MicRequest, extended: bool) -> None:
        source = make_source(self.settings, req.rate)
        try:
            self.t.start_mic(source, req.rate, extended)
        except Exception as e:
            source.close()                        # else the sound card stays open
            if not isinstance(e, UsbError):
                raise
            if not self.lost(e):
                self.mic_start_failed(e)
            return
        log.info("Game started the microphone: %d Hz, source: %s" % (
            req.rate, "PC microphone" if self.settings.mic == "pc" else "1 kHz test tone"),
            event="mic_start", rate=req.rate, source=self.settings.mic, extended=extended)

    def mic_start_failed(self, e: UsbError) -> None:
        log.error("Could not start the microphone stream: %s. Run --install-driver again (or install WinUSB on "
                  "'USB Accessory Mic (Interface 1)' in Zadig), or use --mic off" % e,
                  event="mic_start_failed", error=e)

    def on_stop_mic(self, msg: Message) -> None:
        log.info("Game stopped the microphone", event="mic_stop")
        self.t.stop_mic()

    # --- us -> PSP
    def send_frame(self) -> None:
        if self.source is None:
            return
        jpeg = self.source.get()
        if jpeg is None:
            return                                     # nothing encoded since the start yet: try at the next due time
        age_ms = self.source.age() * 1000
        data, lens = video_frame(jpeg)
        t0 = time.monotonic()
        bad = self.t.send_video(data, lens)
        self.stats.frame(len(jpeg), (time.monotonic() - t0) * 1000, age_ms)
        if self.frames < LOG_FIRST_FRAMES or bad:
            (log.warning if bad else log.debug)(
                "Frame %d: %d bytes in %d packets%s" % (self.frames, len(data), len(lens),
                                                        ", the transfer failed" if bad else ""),
                event="frame", n=self.frames, bytes=len(data), packets=len(lens), failed=bad)
        self.iso_errors = self.iso_errors + bad if bad else 0      # only failures in a row count
        self.frames += 1

    def log_stats(self) -> None:
        now = time.monotonic()
        if self.stats.due(now):
            text, fields = self.stats.summary(now, getattr(self.source, "q", None), self.out.max_jpeg)
            log.info(text, event="stats_video", **fields)

    def tell_source(self) -> None:
        if self.source is not None:
            self.source.next_send = self.next_frame_at

    def stream_due_frame(self, period: float) -> None:
        if time.monotonic() < self.next_frame_at:
            return
        try:
            self.send_frame()
        except UsbGone:
            self.lose()
            return
        except UsbError as e:
            log.warning("Video transfer failed: %s" % e, event="video_transfer_failed", error=e)
            self.iso_errors += 1
            time.sleep(VIDEO_RETRY_PAUSE_S)
        self.next_frame_at = max(self.next_frame_at + period, time.monotonic() - period)
        self.tell_source()
        if self.iso_errors >= MAX_ISO_ERRORS:
            log.error("Too many failed video transfers; video stopped (try re-plugging the cable)",
                      event="video_gave_up")
            self.streaming = False
            self.iso_errors = 0

    def poll(self) -> None:
        wait = IDLE_POLL_MS
        if self.streaming:
            wait = max(1, int(min(FRAME_SLICE_S, max(0.0, self.next_frame_at - time.monotonic())) * 1000))
        try:
            data = self.t.read_message(wait)
        except UsbGone:
            self.lose()
            return
        except UsbError as e:
            if time.monotonic() - self.last_read_error > READ_ERROR_LOG_GAP_S:
                log.warning("Cannot read the PSP's messages: %s" % e, event="psp_read_failed", error=e)
                self.last_read_error = time.monotonic()
            return
        if data:
            self.dispatch(data)

    def make_source(self) -> Union[Webcam, TestPattern]:
        if self.settings.webcam is not None:
            return Webcam(self.settings.webcam, self.out, self.settings.camres)
        return TestPattern(self.out)

    def stream_step(self) -> None:
        if self.streaming and not self.gone:
            self.stream_due_frame(1.0 / self.fps)
            self.log_stats()
        elif not self.streaming:
            time.sleep(IDLE_SLEEP_S)

    def still_present(self) -> bool:
        if self.present():
            return True
        log.info("The PSP disconnected", event="unplugged", how="not_detected")
        return False

    def serve(self) -> None:
        last_check = time.monotonic()
        while not self.gone:
            self.poll()
            self.stream_step()
            if time.monotonic() - last_check > PRESENCE_PERIOD:
                last_check = time.monotonic()
                if not self.still_present():
                    break

    def open_source(self) -> bool:
        try:
            self.source = self.make_source()
        except Exception as e:                    # no such camera, busy, or it gave no picture
            log.error("Could not open the PC camera: %s. Check the number with --list (--webcam N), or use "
                      "--testpattern" % (e,), event="camera_failed", error=e)
            return False
        return True

    def run(self) -> None:
        if not self.open_source():
            return
        try:
            self.serve()
        finally:
            if self.source is not None:
                self.source.close()                    # the camera is free again for the next session
        total = self.frames_total + self.frames
        log.info("Session finished: %d frames sent" % total, event="session_end", frames=total)
