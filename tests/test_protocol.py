import struct

import pytest

from psychea import protocol as p

EYEPET_START_VIDEO = bytes.fromhex("02070602000002004000000100000000000000000002e803f401008000000108")
EYEPET_START_MIC = bytes.fromhex("00001e0000000000030002001600")


def jpeg(n):
    return b"\xff\xd8" + b"x" * n + b"\xff\xd9"


def test_trailer_frame_is_whole_packets_plus_16_byte_trailer_declaring_the_sent_size():
    data, lens = p.video_frame(jpeg(2000))
    assert lens[-1] == 16 and all(n == 896 for n in lens[:-1]) and sum(lens) == len(data)
    assert struct.unpack("<I", data[-12:-8])[0] == len(data) - 16
    assert data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9")




def test_message_layout():
    m = p.parse_message(struct.pack("<HBB", 2, 0x2B, 2) + b"ab" + b"junk")
    assert (m.ident, m.cmd, m.payload) == (2, 0x2B, b"ab")
    assert p.parse_message(b"\x01\x02") is None


def test_start_video_decodes_what_the_game_asks_for():
    req = p.parse_start_video(EYEPET_START_VIDEO)
    assert (req.width, req.height, req.fps_code, req.max_size) == (480, 272, 6, 32768)
    assert p.parse_start_video(b"short") is None


def test_jpeg_budget_stays_in_the_games_limit_the_psp_limit_and_the_usb_bandwidth():
    assert p.jpeg_budget(26624, 15, 15000) == 25124            # the game's frame size minus 1500 (XMB camera)
    assert p.jpeg_budget(32768, 30, 15000) == 18816            # 30 fps: 21 packets fit 80 % of the 33 ms period
    assert p.jpeg_budget(200000, 60, 15000) == 7168            # 60 fps: 8 packets
    assert p.jpeg_budget(0, 15, 12345) == 12345                # no usable limit: keep the current one


def test_start_mic_decodes_rate_and_gain():
    req = p.parse_start_mic(EYEPET_START_MIC)
    assert (req.rate, req.gain, req.alc) == (22050, 30, 0)
    assert p.parse_start_mic(b"\x00" * 13) is None


@pytest.mark.parametrize("code,rate", [(11, 11025), (22, 22050), (44, 44100), (48, 48000), (99, 22050)])
def test_mic_rates(code, rate):
    payload = bytearray(14)
    payload[12] = code
    assert p.parse_start_mic(bytes(payload)).rate == rate




def test_trailer_frame_keeps_the_jpeg_and_pads_with_zeros_to_whole_packets():
    j = jpeg(1000)
    data, lens = p.video_frame(j)
    body = data[:-16]
    assert body[:len(j)] == j and set(body[len(j):]) == {0} and len(body) % 896 == 0 and len(body) - len(j) < 896
    assert data[-16:] == b"\xff\xfe\x00\x0a" + struct.pack("<I", len(body)) + bytes(6) + b"\xff\xd9"
    assert lens == [896] * (len(body) // 896) + [16]


def test_trailer_frame_of_exact_packets_is_not_padded():
    data, lens = p.video_frame(b"\xff\xd8" + b"x" * 892 + b"\xff\xd9")
    assert len(data) == 896 + 16 and lens == [896, 16]



def test_message_without_payload_and_with_exactly_four_bytes():
    m = p.parse_message(struct.pack("<HBB", 3, 9, 0))
    assert (m.ident, m.cmd, m.payload) == (3, 9, b"")
    assert p.parse_message(b"\x01\x00\x02\x00") is not None


def test_message_payload_is_cut_to_its_declared_length():
    assert p.parse_message(struct.pack("<HBB", 1, 1, 3) + b"abcdef").payload == b"abc"


def test_start_video_needs_30_bytes_and_a_sane_limit():
    cfg = bytearray(EYEPET_START_VIDEO)
    assert p.parse_start_video(bytes(cfg[:30])).max_size == 32768
    assert p.parse_start_video(bytes(cfg[:29])) is None
    for bad in (1999, 200001, 0):
        cfg[26:30] = struct.pack("<I", bad)
        assert p.parse_start_video(bytes(cfg)).max_size == 0
    for ok in (2000, 200000):
        cfg[26:30] = struct.pack("<I", ok)
        assert p.parse_start_video(bytes(cfg)).max_size == ok


@pytest.mark.parametrize("code,size", [
    (0, (1280, 1024)), (2, (640, 480)), (7, (480, 272)), (9, (1280, 960)), (77, (480, 272))])
def test_start_video_resolution_codes(code, size):
    cfg = bytearray(EYEPET_START_VIDEO)
    cfg[1], cfg[2] = code, 5
    req = p.parse_start_video(bytes(cfg))
    assert (req.width, req.height, req.fps_code) == (*size, 5)


def test_start_mic_fields_and_length_boundary():
    payload = bytearray(14)
    payload[0], payload[2], payload[12] = 7, 31, 44
    req = p.parse_start_mic(bytes(payload))
    assert (req.alc, req.gain, req.rate) == (7, 31, 44100)
    assert p.parse_start_mic(bytes(payload) + b"\x00") is not None


def test_jpeg_budget_uses_the_smaller_limit_and_returns_an_int():
    assert isinstance(p.jpeg_budget(32768, 15, 1), int)
    assert p.jpeg_budget(20000, 15, 1) == 18500                  # the game's limit wins
    assert p.jpeg_budget(200000, 15, 1) == 32256                 # the PSP's limit wins (36 packets, size probe)
    assert p.jpeg_budget(200000, 5, 1) == 32256 and p.jpeg_budget(200000, 20, 1) == 30464   # 34 packets



@pytest.mark.parametrize("code,fps", [(0, 3.75), (4, 15), (6, 30), (7, 60), (9, 15)])
def test_frame_rate_codes(code, fps):
    cfg = bytearray(EYEPET_START_VIDEO)
    cfg[2] = code
    assert p.parse_start_video(bytes(cfg)).fps == fps


# START_STILL_SETUP from the XMB camera app taking a photo: max size 0x5C800, resolution code 2
XMB_STILL_SETUP = bytes.fromhex("00c805000201020701000000000000000000" "0100")


def test_still_setup_gives_the_output_resolution_from_byte_7_not_the_sensor_mode_in_byte_6():
    from psychea.protocol import parse_still_setup
    req = parse_still_setup(XMB_STILL_SETUP)                       # byte 6 = 02 (sensor 640x480), byte 7 = 07
    assert (req.w, req.h, req.max_size) == (480, 272, 0x5C800)
    vga = parse_still_setup(XMB_STILL_SETUP[:7] + b"\x02" + XMB_STILL_SETUP[8:])
    assert (vga.w, vga.h) == (640, 480)


def test_still_setup_that_is_too_short_or_has_an_unknown_resolution_is_refused():
    from psychea.protocol import parse_still_setup
    assert parse_still_setup(XMB_STILL_SETUP[:7]) is None
    assert parse_still_setup(XMB_STILL_SETUP[:7] + b"\x63" + XMB_STILL_SETUP[8:]) is None


def test_chunk_packets_splits_on_packet_boundaries_and_keeps_every_byte():
    from psychea.protocol import chunk_packets, packet_lengths
    data = bytes(range(256)) * 40
    lens = packet_lengths(len(data) - 16, 896) + [16]
    chunks = chunk_packets(data, lens, 4000)
    assert b"".join(chunks) == data
    assert all(len(c) <= 4000 for c in chunks)
    assert all(len(c) % 896 == 0 for c in chunks[:-1])
    assert len(chunk_packets(data[:100], [100], 4000)) == 1


def test_start_mic_carries_alc_gain_and_the_signed_noise_value():
    from psychea import protocol as p
    xmb = bytes.fromhex("000014 00 f6ff 0000 0300 0200 1600".replace(" ", ""))   # XMB camera app: gain 20, noize -10
    req = p.parse_start_mic(xmb)
    assert (req.alc, req.gain, req.noise, req.rate) == (0, 20, -10, 22050)
    assert p.parse_start_mic(EYEPET_START_MIC).noise == 0


def test_the_psp_is_found_by_its_usb_ids_and_endpoints_and_greeted_with_the_accessory_info():
    assert (p.VID, p.PID) == (0x054C, 0x0282)
    assert p.INFO == bytes([0x4C, 0x05, 0x5B, 0x02, 0x01, 0x10, 0x01, 0x00])
    assert (p.EP_INT_IN, p.EP_ISO_VIDEO, p.EP_ISO_MIC) == (0x81, 0x04, 0x02)
    assert (p.IF_CONTROL, p.IF_MIC, p.IF_VIDEO) == (0, 2, 3)
    assert p.PKT == 896 and p.PSP_FRAME_LIMIT == 36 * 896


def test_camera_type_reply_and_frame_rate_defaults():
    assert p.CAMERA_TYPE == 0x0500
    assert (p.DEFAULT_FPS, p.MAX_FPS, p.DEFAULT_MAX_JPEG) == (15.0, 30.0, 15000)


@pytest.mark.parametrize("code,size", [(0, (1280, 1024)), (1, (1024, 768)), (2, (640, 480)), (3, (352, 288)),
                                       (4, (320, 240)), (5, (176, 144)), (6, (160, 120)), (7, (480, 272)),
                                       (8, (360, 272)), (9, (1280, 960))])
def test_every_resolution_code_of_start_video_and_still_setup(code, size):
    cfg = bytearray(EYEPET_START_VIDEO)
    cfg[1] = code
    video = p.parse_start_video(bytes(cfg))
    assert (video.width, video.height) == size
    still = bytearray(XMB_STILL_SETUP)
    still[7] = code
    photo = p.parse_still_setup(bytes(still))
    assert (photo.w, photo.h) == size


@pytest.mark.parametrize("code,fps", [(0, 3.75), (1, 5), (2, 7.5), (3, 10), (4, 15), (5, 20), (6, 30), (7, 60)])
def test_every_frame_rate_code(code, fps):
    cfg = bytearray(EYEPET_START_VIDEO)
    cfg[2] = code
    req = p.parse_start_video(bytes(cfg))
    assert req.fps == fps and isinstance(req.fps, float)


def test_a_frame_rate_code_past_the_table_falls_back_to_the_default():
    cfg = bytearray(EYEPET_START_VIDEO)
    cfg[2] = 8
    assert p.parse_start_video(bytes(cfg)).fps == p.DEFAULT_FPS


@pytest.mark.parametrize("key,name", [
    ((2, 0x2B), "HELLO"), ((2, 0xAB), "SET_FLIP_MIRROR"), ((3, 0x09), "CAMERA_TYPE_QUERY"),
    ((2, 0x40), "QUERY_0x40"), ((3, 0x43), "QUERY_0x43"), ((2, 0x01), "START_VIDEO"), ((2, 0x02), "STOP_VIDEO"),
    ((2, 0x04), "STOP_VIDEO_4"), ((1, 0x01), "START_MIC"), ((1, 0x03), "START_MIC_EX"), ((1, 0x02), "STOP_MIC"),
    ((2, 0x03), "START_STILL_SETUP"), ((2, 0x05), "SET_ZOOM"), ((2, 0xA0), "SET_RESOLUTION"),
    ((2, 0xA4), "SET_SATURATION"), ((2, 0xA5), "SET_BRIGHTNESS"), ((2, 0xA6), "SET_CONTRAST"),
    ((2, 0xA3), "SET_WHITE_BALANCE"), ((2, 0xA7), "SET_SHARPNESS"), ((2, 0xA9), "SET_ANTI_FLICKER"),
    ((2, 0xAC), "SET_IMAGE_EFFECT"),
    ((2, 0xAF), "SET_EV_LEVEL"), ((3, 0x44), "SET_LENS_0x44"), ((2, 0x41), "SET_0x41")])
def test_messages_the_log_names(key, name):
    assert p.MESSAGE_NAMES[key] == name
    assert len(p.MESSAGE_NAMES) == 24


def test_a_message_is_immutable_and_a_still_request_is_not_a_message():
    m = p.parse_message(struct.pack("<HBB", 2, 1, 0))
    with pytest.raises(AttributeError):
        m.cmd = 5


def test_still_setup_needs_8_bytes_and_reads_the_size_little_endian():
    assert p.parse_still_setup(XMB_STILL_SETUP[:8]).max_size == 0x5C800
    assert p.parse_still_setup(XMB_STILL_SETUP[:7]) is None


def test_trailer_chunk_counter_is_byte_9_and_a_photo_chunk_gets_1():
    data, _ = p.video_frame(jpeg(10), seq=1)
    assert data[-16:-6] == b"\xff\xfe\x00\x0a" + struct.pack("<I", 896) + b"\x00\x01"
    assert data[-6:] == bytes(4) + b"\xff\xd9"
    assert p.video_frame(jpeg(10))[0][-7] == 0


def test_packet_lengths_of_exact_and_partial_packets():
    assert p.packet_lengths(0) == []
    assert p.packet_lengths(896) == [896]
    assert p.packet_lengths(897) == [896, 1]
    assert p.packet_lengths(300, pkt=100) == [100, 100, 100]
    assert p.packet_lengths(250, pkt=100) == [100, 100, 50]


def test_chunk_packets_fills_a_chunk_up_to_the_limit_inclusive():
    data = bytes(range(10)) * 3
    assert p.chunk_packets(data, [10, 10, 10], 20) == [data[:20], data[20:]]
    assert p.chunk_packets(data, [10, 10, 10], 30) == [data]
    assert p.chunk_packets(data, [10, 10, 10], 19) == [data[:10], data[10:20], data[20:]]
    assert p.chunk_packets(b"", [], 20) == []
    assert p.chunk_packets(data, [30], 10) == [data]                 # one packet bigger than the limit stays whole


def test_still_budget_keeps_a_photo_chunk_below_the_psp_limit_and_inside_its_buffer():
    assert p.still_budget(1_000_000) == 60_000                      # cap
    assert p.still_budget(0x5C800) == 60_000
    assert p.still_budget(64_096) == 60_000 and p.still_budget(64_095) == 59_999   # buffer minus 4096 margin
    assert p.still_budget(30_000) == 25_904
    assert p.still_budget(24_096) == 20_000 and p.still_budget(24_097) == 20_001   # floor
    assert p.still_budget(0) == 20_000


def test_jpeg_budget_boundaries():
    assert p.jpeg_budget(3000, 15, 1) == 1500                       # the game's limit minus 1500
    assert p.jpeg_budget(32768, 30, 1) == 18816                     # bandwidth: 21 packets < 32768 - 1500
    assert p.jpeg_budget(60000, 30, 1) == 18816
    assert p.jpeg_budget(60000, 15, 1) == 32256                     # the PSP's own limit: 36 packets, 37 stop the video
    assert p.jpeg_budget(40000, 15, 1) == 32256 and p.jpeg_budget(33756, 15, 1) == 32256
    assert p.jpeg_budget(33755, 15, 1) == 32255                     # the game's limit is the smaller one by 1 byte



def test_start_mic_noise_and_sample_rate_come_from_their_own_bytes():
    payload = bytearray(14)
    payload[0], payload[2], payload[4], payload[5] = 1, 2, 0x34, 0x12
    payload[12], payload[13] = 22, 0
    req = p.parse_start_mic(bytes(payload))
    assert (req.alc, req.gain, req.noise, req.rate) == (1, 2, 0x1234, 22050)
    payload[12], payload[13] = 0xB0, 0x04                           # 0x04B0 = 1200: not a known code
    assert p.parse_start_mic(bytes(payload)).rate == 22050
    payload[12], payload[13] = 44, 1                                # high byte counts: 0x012C = 300
    assert p.parse_start_mic(bytes(payload)).rate == 22050


def test_video_requests_are_frozen():
    req = p.parse_start_video(EYEPET_START_VIDEO)
    with pytest.raises(AttributeError):
        req.width = 1
    with pytest.raises(AttributeError):
        p.parse_start_mic(EYEPET_START_MIC).rate = 1
    with pytest.raises(AttributeError):
        p.parse_still_setup(XMB_STILL_SETUP).w = 1



def random_payloads(count, seed=7):
    import random
    rng = random.Random(seed)
    sizes = [0, 1, 2, 7, 8, 13, 14, 29, 30, 31, 32, 60, 64]
    return [bytes(rng.randrange(256) for _ in range(rng.choice(sizes))) for _ in range(count)]


def test_no_parser_raises_on_any_payload_and_every_result_is_in_range():
    for data in random_payloads(3000):
        m = p.parse_message(data)
        assert m is None or (len(data) >= 4 and len(m.payload) <= data[3] and len(m.payload) <= len(data) - 4)
        video = p.parse_start_video(data)
        if video:
            assert (video.width, video.height) in p.RES_CODES.values() or (video.width, video.height) == (480, 272)
            assert 0 < video.fps <= 60 and (video.max_size == 0 or 2000 <= video.max_size <= 200000)
        still = p.parse_still_setup(data)
        assert still is None or (still.w > 0 and still.h > 0 and 20_000 <= p.still_budget(still.max_size) <= 60_000)
        mic = p.parse_start_mic(data)
        assert mic is None or mic.rate in p.MIC_RATES.values() or mic.rate == 22050


@pytest.mark.parametrize("data", [bytearray(b"\x02\x00\x2b\x01\x07"), memoryview(b"\x02\x00\x2b\x01\x07")])
def test_a_message_may_arrive_as_any_bytes_like_buffer(data):
    m = p.parse_message(data)
    assert (m.ident, m.cmd, m.payload) == (2, 0x2B, b"\x07") and type(m.payload) is bytes


def test_a_payload_shorter_than_its_declared_length_is_kept_as_it_is():
    m = p.parse_message(struct.pack("<HBB", 2, 1, 60) + b"abc")
    assert m.payload == b"abc"


def test_the_smallest_limit_a_game_may_ask_for_still_gives_a_positive_budget():
    assert p.jpeg_budget(2000, 30.0, 15000) == 500
    assert p.jpeg_budget(200000, 3.75, 15000) == p.PSP_FRAME_LIMIT
