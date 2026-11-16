# Webcam capture -> crop/resize/mirror -> JPEG, with cv2 present but a fake camera.
import threading
import time

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
from psychea import sources  # noqa: E402


class FakeCapture:
    def __init__(self, *a):
        frame = np.zeros((480, 640, 3), np.uint8)
        frame[:, :320] = (0, 0, 255)          # BGR: left half red
        frame[:, 320:] = (255, 0, 0)          # right half blue
        self.frame = frame

    def isOpened(self):
        return True

    def set(self, *a):
        return True

    def get(self, prop):
        return 640.0

    def read(self):
        time.sleep(0.005)
        return True, self.frame.copy()

    released = False

    def release(self):
        self.released = True


def grab(out):
    cam = sources.Webcam(0, out)
    try:
        return cam.get()
    finally:
        cam.close()


@pytest.fixture(autouse=True)
def fake_camera(monkeypatch):
    monkeypatch.setattr(cv2, "VideoCapture", FakeCapture)


def corner_colours(jpeg):
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)[:, :, ::-1]       # -> RGB
    h, w = img.shape[:2]
    return (w, h), tuple(img[h // 2, 20]), tuple(img[h // 2, w - 20])


def test_picture_is_cropped_to_the_games_size_and_fits_its_budget():
    out = sources.Output(w=480, h=272, max_jpeg=14884)
    jpeg = grab(out)
    size, left, right = corner_colours(jpeg)
    assert size == (480, 272) and len(jpeg) <= 14884
    assert left[0] > 150 > left[2] and right[2] > 150 > right[0]      # red left, blue right


def test_mirror_swaps_left_and_right():
    out = sources.Output(mirror=1)
    _, left, right = corner_colours(grab(out))
    assert left[2] > 150 > left[0] and right[0] > 150 > right[2]


def test_smaller_resolution_requests_are_honoured():
    out = sources.Output(w=320, h=240, max_jpeg=9000)
    jpeg = grab(out)
    assert corner_colours(jpeg)[0] == (320, 240) and len(jpeg) <= 9000


def test_a_camera_that_cannot_be_opened_is_an_error(monkeypatch):
    class Closed(FakeCapture):
        def isOpened(self):
            return False

    monkeypatch.setattr(cv2, "VideoCapture", Closed)
    with pytest.raises(RuntimeError, match="cannot open webcam 3"):
        sources.Webcam(3, sources.Output())


def test_the_default_backend_is_tried_when_the_preferred_one_does_not_open(monkeypatch):
    calls = []

    class Second(FakeCapture):
        def __init__(self, *a):
            super().__init__(*a)
            calls.append(a)
            self.ok = len(calls) > 1

        def isOpened(self):
            return self.ok

    monkeypatch.setattr(cv2, "VideoCapture", Second)
    monkeypatch.setattr(sources.os, "name", "nt")
    cam = sources.Webcam(0, sources.Output())
    cam.close()
    assert calls == [(0, cv2.CAP_DSHOW), (0,)]


def test_a_failing_camera_read_surfaces_as_an_error(monkeypatch):
    class Broken(FakeCapture):
        def read(self):
            return False, None

    monkeypatch.setattr(cv2, "VideoCapture", Broken)
    with pytest.raises(RuntimeError, match="webcam read failed"):
        sources.Webcam(0, sources.Output())


def test_camera_properties_that_cannot_be_set_are_ignored(monkeypatch):
    class Picky(FakeCapture):
        def set(self, *a):
            raise OSError("unsupported")

    monkeypatch.setattr(cv2, "VideoCapture", Picky)
    assert grab(sources.Output(w=480, h=272, max_jpeg=14884))


def test_a_still_needs_a_frame_and_a_healthy_camera(monkeypatch):
    cam = sources.Webcam(0, sources.Output())
    cam.close()
    time.sleep(0.05)
    cam.raw = None
    with pytest.raises(RuntimeError, match="no camera frame yet"):
        cam.still(100, 100, 20000)
    cam.raw = np.zeros((480, 640, 3), np.uint8)
    assert cam.still(100, 100, 20000)[:2] == b"\xff\xd8"
    cam.err = "boom"
    with pytest.raises(RuntimeError, match="boom"):
        cam.still(100, 100, 20000)
    with pytest.raises(RuntimeError, match="boom"):
        cam.get()


def test_the_jpeg_quality_drops_until_the_picture_fits(monkeypatch):
    cam = sources.Webcam(0, sources.Output(w=480, h=272, max_jpeg=3000))
    cam.close()
    noise = np.random.default_rng(1).integers(0, 255, (272, 480, 3), dtype=np.uint8)
    cam.q = 70
    cam._encode(noise)
    assert cam.q < 70


def test_closing_the_webcam_ends_its_thread_and_releases_the_camera():
    # Without it every session left a camera thread behind: after unplug and replug two threads held the camera.
    cam = sources.Webcam(0, sources.Output())
    cam.close()
    assert not cam.th.is_alive() and cam.cap.released
    cam.close()                                           # closing twice is harmless


def test_a_camera_that_fails_while_starting_is_released(monkeypatch):
    # Webcam() raised after opening the camera and left it open (a second start then found it busy).
    opened = []

    class Broken(FakeCapture):
        def __init__(self, *a):
            super().__init__(*a)
            opened.append(self)

        def read(self):
            return False, None

    monkeypatch.setattr(cv2, "VideoCapture", Broken)
    with pytest.raises(RuntimeError, match="webcam read failed"):
        sources.Webcam(0, sources.Output())
    assert opened and all(cap.released for cap in opened)


class Flaky(FakeCapture):
    # Frames whose encoding fails for the first `bad` reads (like a corrupt frame from a USB hiccup).
    bad = 3

    def read(self):
        ok, frame = super().read()
        if Flaky.bad > 0:
            Flaky.bad -= 1
            return True, np.zeros((480, 640), np.uint8)             # grey: the black&white effect cannot take it
        return ok, frame


def test_a_frame_that_cannot_be_encoded_is_skipped_not_fatal(monkeypatch):
    # An exception in the capture thread does not end it: the frame is skipped and the error is logged.
    monkeypatch.setattr(cv2, "VideoCapture", Flaky)
    Flaky.bad = 3
    cam = sources.Webcam(0, sources.Output(effect=2))
    try:
        time.sleep(0.1)
        assert cam.th.is_alive() and cam.err is None and cam.get() is not None
    finally:
        cam.close()


def test_a_camera_whose_frames_never_encode_ends_the_session_with_an_error(monkeypatch):
    monkeypatch.setattr(cv2, "VideoCapture", Flaky)
    Flaky.bad = 10 ** 6
    with pytest.raises(RuntimeError, match="webcam"):
        sources.Webcam(0, sources.Output(effect=2))


def test_a_read_that_says_ok_without_a_frame_is_a_read_failure(monkeypatch):
    class Empty(FakeCapture):
        def read(self):
            return True, None

    monkeypatch.setattr(cv2, "VideoCapture", Empty)
    with pytest.raises(RuntimeError, match="webcam read failed"):
        sources.Webcam(0, sources.Output())


def test_the_age_belongs_to_the_picture_that_get_returns(monkeypatch):
    # captured_at changes only when an encode ends, so age() describes the picture that get() returns.
    cam = sources.Webcam(0, sources.Output())
    cam.next_send = time.monotonic() + 10.0                      # no new encode on its own
    time.sleep(0.05)
    stamp = cam.captured_at
    gate = threading.Event()
    real = cam._encode

    def slow(small):
        gate.wait(2)
        return real(small)

    cam._encode = slow
    cam.next_send = 0.0
    time.sleep(0.1)                                         # an encode has started and waits at the gate
    assert cam.captured_at == stamp
    gate.set()
    cam.close()


def test_the_frame_interval_is_a_running_average_that_ignores_stalls():
    assert sources.smoothed_dt(0.04, 0.02) == pytest.approx(0.038)
    assert sources.smoothed_dt(0.03, 0.03) == pytest.approx(0.03)
    assert sources.smoothed_dt(0.03, 5.0) == pytest.approx(0.03 + 0.1 * (0.2 - 0.03))          # a stall counts as 0.2 s
    assert sources.smoothed_dt(0.3, 0.0) == pytest.approx(0.27)


def test_the_capture_thread_tracks_the_camera_rate(monkeypatch):
    cam = sources.Webcam(0, sources.Output())
    try:
        time.sleep(0.4)                                      # the fake camera needs about 5 ms a frame
        assert 0.004 < cam.cam_dt < 0.025
    finally:
        cam.close()


def test_windows_asks_for_directshow_and_other_systems_for_the_default_backend(monkeypatch):
    calls = []

    class Spy(FakeCapture):
        def __init__(self, *a):
            super().__init__(*a)
            calls.append(a)

    monkeypatch.setattr(cv2, "VideoCapture", Spy)
    for name, expected in (("posix", [(2,)]), ("nt", [(2, cv2.CAP_DSHOW)])):
        calls.clear()
        monkeypatch.setattr(sources.os, "name", name)
        sources.Webcam(2, sources.Output()).close()
        assert calls == expected, name


def test_the_error_messages_are_exactly_what_the_user_reads(monkeypatch):
    class Closed(FakeCapture):
        def isOpened(self):
            return False

    monkeypatch.setattr(cv2, "VideoCapture", Closed)
    with pytest.raises(RuntimeError) as err:
        sources.Webcam(7, sources.Output())
    assert str(err.value) == "cannot open webcam 7"
    monkeypatch.setattr(cv2, "VideoCapture", FakeCapture)
    cam = sources.Webcam(0, sources.Output())
    cam.close()
    cam.raw = None
    with pytest.raises(RuntimeError) as err:
        cam.still(10, 10, 9999)
    assert str(err.value) == "no camera frame yet"


def test_a_streak_of_unencodable_frames_is_logged_once_and_ends_at_exactly_30(monkeypatch, caplog):
    monkeypatch.setattr(cv2, "VideoCapture", Flaky)
    Flaky.bad = sources.MAX_ENCODE_FAILURES - 1                    # one short of giving up: the camera survives
    cam = sources.Webcam(0, sources.Output(effect=2))
    try:
        time.sleep(0.05)
        assert cam.err is None and cam.failures == 0                # a good frame ends the streak
    finally:
        cam.close()
    warned = [r for r in caplog.records if r.fields.get("event") == "frame_encode_failed"]
    assert len(warned) == 1 and warned[0].levelname == "WARNING"
    assert warned[0].getMessage().startswith("A camera frame could not be encoded: ")
    Flaky.bad = sources.MAX_ENCODE_FAILURES
    with pytest.raises(RuntimeError) as err:
        sources.Webcam(0, sources.Output(effect=2))
    assert str(err.value).startswith("webcam frames cannot be encoded: ")


def test_a_fresh_webcam_reports_a_picture_age_and_a_valid_quality(monkeypatch):
    cam = sources.Webcam(0, sources.Output())
    try:
        assert 0.0 <= cam.age() < 1.0 and cam.q in range(1, 101) and cam.generation == 0
    finally:
        cam.close()
