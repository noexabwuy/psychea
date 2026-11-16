# scripts/size_probe.py: frames of exactly known sizes, climbing in steps, to find the PSP's real size limit.
import importlib.util
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "size_probe.py"
spec = importlib.util.spec_from_file_location("size_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def decode(jpeg):
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)


def base_jpeg():
    img = np.full((272, 480, 3), 40, np.uint8)
    cv2.putText(img, "TEST", (50, 150), cv2.FONT_HERSHEY_SIMPLEX, 3, (255, 255, 255), 5)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 60])[1].tobytes()


def test_padding_makes_the_jpeg_exactly_the_requested_size_and_keeps_the_picture():
    jpeg = base_jpeg()
    padded = probe.pad_jpeg(jpeg, 13000)
    assert len(padded) == 13000
    assert padded[:2] == b"\xff\xd8" and padded[-2:] == b"\xff\xd9"
    assert np.array_equal(decode(padded), decode(jpeg))


def test_padding_works_for_the_smallest_possible_comment_and_for_a_large_one():
    jpeg = base_jpeg()
    assert len(probe.pad_jpeg(jpeg, len(jpeg) + 4)) == len(jpeg) + 4
    assert len(probe.pad_jpeg(jpeg, 60000)) == 60000
    assert np.array_equal(decode(probe.pad_jpeg(jpeg, 60000)), decode(jpeg))


def test_a_size_that_cannot_hold_a_comment_is_refused():
    jpeg = base_jpeg()
    for total in (len(jpeg) - 1, len(jpeg) + 1, len(jpeg) + 3):
        with pytest.raises(ValueError):
            probe.pad_jpeg(jpeg, total)


def test_the_steps_climb_from_start_to_stop_in_equal_increments():
    steps = probe.STEPS
    assert steps[0] == probe.START and steps[-1] == probe.STOP
    assert all(b - a == probe.INCREMENT for a, b in zip(steps, steps[1:]))
    assert len(steps) == (probe.STOP - probe.START) // probe.INCREMENT + 1


def test_the_step_follows_the_time_and_stays_on_the_last_one():
    assert probe.step_at(0.0) == 0
    assert probe.step_at(probe.HOLD_S - 0.01) == 0
    assert probe.step_at(probe.HOLD_S) == 1
    assert probe.step_at(10 ** 6) == len(probe.STEPS) - 1


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_the_source_sends_a_frame_of_the_current_steps_size_and_climbs_with_time():
    clock = Clock()
    source = probe.ProbeSource(clock=clock)
    first = source.get()
    assert len(first) == probe.STEPS[0]
    clock.now += probe.HOLD_S * 5
    assert len(source.get()) == probe.STEPS[5]
    clock.now += 10 ** 5
    assert len(source.get()) == probe.STEPS[-1]
    assert decode(first) is not None and decode(first).shape == (272, 480, 3)


def test_the_source_restarts_the_climb_on_reset():
    clock = Clock()
    source = probe.ProbeSource(clock=clock)
    clock.now += probe.HOLD_S * 7
    assert len(source.get()) == probe.STEPS[7]
    source.reset()
    assert len(source.get()) == probe.STEPS[0]


def test_the_label_shows_the_jpeg_size_and_the_packets_it_takes():
    assert probe.label(probe.STEPS[0]) == "%d B  %d packets" % (probe.STEPS[0], -(-probe.STEPS[0] // 896))


def test_different_steps_show_different_pictures():
    clock = Clock()
    source = probe.ProbeSource(clock=clock)
    first = decode(source.get())
    clock.now += probe.HOLD_S
    assert not np.array_equal(first, decode(source.get()))


def test_the_source_has_the_interface_the_session_expects():
    source = probe.ProbeSource(clock=Clock())
    assert source.age() == 0.0 and source.close() is None
    source.next_send = 5.0
    with pytest.raises(RuntimeError):
        source.still(640, 480, 20000)


def close(a, b):
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean()) < 2.0      # JPEG is not exactly flip-symmetric


def test_the_picture_follows_the_games_mirror_request_and_the_mirror_option():
    from psychea.sources import Output
    plain = decode(probe.ProbeSource(clock=Clock(), out=Output()).get())
    mirrored = decode(probe.ProbeSource(clock=Clock(), out=Output(mirror=1)).get())
    assert close(mirrored, plain[:, ::-1]) and not close(mirrored, plain)
    both = decode(probe.ProbeSource(clock=Clock(), out=Output(mirror=1, mirror_extra=1)).get())
    assert close(both, plain)                                # --mirror cancels the game's request


def test_padding_reaches_every_size_a_comment_segment_can_make():
    from hypothesis import given, settings
    from hypothesis import strategies as st
    jpeg = base_jpeg()
    picture = decode(jpeg)

    @settings(max_examples=200, deadline=None, database=None)
    @given(extra=st.integers(4, 65537))
    def check(extra):
        padded = probe.pad_jpeg(jpeg, len(jpeg) + extra)
        assert len(padded) == len(jpeg) + extra and padded[:2] == b"\xff\xd8" and padded[-2:] == b"\xff\xd9"
        assert padded[2:4] == b"\xff\xfe" and int.from_bytes(padded[4:6], "big") == extra - 2
    check()
    for extra in (4, 5, 65537):
        assert np.array_equal(decode(probe.pad_jpeg(jpeg, len(jpeg) + extra)), picture)
    with pytest.raises(ValueError):
        probe.pad_jpeg(jpeg, len(jpeg) + 65538)
