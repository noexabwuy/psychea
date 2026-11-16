import cv2
import numpy as np
import pytest

from psychea import cli, sources
from psychea.settings import Settings


def noisy(seed, sigma=4.0):
    base = np.full((272, 480, 3), 120, np.float32)
    base[:, 240:] = 200
    return np.clip(base + np.random.default_rng(seed).normal(0, sigma, base.shape), 0, 255).astype(np.uint8)


def test_camres_option():
    s = cli.parse_args(["--camres", "1280x720"])
    assert s.camres == (1280, 720)
    assert cli.parse_args(["--camres=1920X1080"]).camres == (1920, 1080)
    d = Settings()
    assert d.camres is None


@pytest.mark.parametrize("bad", ["1280", "0x720", "axb", "1280x", "5000x5000", "-1x5"])
def test_camres_rejects_nonsense(bad):
    with pytest.raises(SystemExit):
        cli.parse_args(["--camres", bad])


def test_webcam_asks_the_camera_for_the_chosen_size_and_mjpg(monkeypatch):
    sets = []

    class Cap:
        def __init__(self, *a):
            pass

        def isOpened(self):
            return True

        def set(self, prop, value):
            sets.append((prop, value))
            return True

        def get(self, prop):
            return 0.0

        def read(self):
            return True, np.zeros((720, 1280, 3), np.uint8)

        def release(self):
            pass

    monkeypatch.setattr(cv2, "VideoCapture", Cap)
    cam = sources.Webcam(0, sources.Output(), (1280, 720))
    cam.close()
    assert (cv2.CAP_PROP_FRAME_WIDTH, 1280) in sets and (cv2.CAP_PROP_FRAME_HEIGHT, 720) in sets
    assert (cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*"MJPG")) in sets
    assert (cv2.CAP_PROP_BUFFERSIZE, 1) in sets and (cv2.CAP_PROP_FPS, 30) in sets
    sets.clear()
    sources.Webcam(0, sources.Output()).close()
    assert (cv2.CAP_PROP_FRAME_WIDTH, 640) in sets and not any(p == cv2.CAP_PROP_FOURCC for p, _ in sets)


def _fake_camera_opening_at(monkeypatch, w, h):
    class Cap:
        def __init__(self, *a):
            pass

        def isOpened(self):
            return True

        def set(self, *a):
            return True

        def get(self, prop):
            return {cv2.CAP_PROP_FRAME_WIDTH: float(w), cv2.CAP_PROP_FRAME_HEIGHT: float(h)}.get(prop, 30.0)

        def read(self):
            return True, np.zeros((h, w, 3), np.uint8)

        def release(self):
            pass

    monkeypatch.setattr(cv2, "VideoCapture", Cap)


def test_a_camera_that_ignores_camres_is_reported(monkeypatch, caplog):
    _fake_camera_opening_at(monkeypatch, 640, 480)
    sources.Webcam(0, sources.Output(), (960, 540)).close()
    warned = [r for r in caplog.records if r.fields.get("event") == "camres_ignored"]
    assert len(warned) == 1 and warned[0].levelname == "WARNING"
    assert "960x540" in warned[0].getMessage() and "640x480" in warned[0].getMessage()
    assert (warned[0].fields["asked_w"], warned[0].fields["asked_h"]) == (960, 540)


def test_no_camres_warning_when_the_camera_gives_the_size(monkeypatch, caplog):
    _fake_camera_opening_at(monkeypatch, 1280, 720)
    sources.Webcam(0, sources.Output(), (1280, 720)).close()
    sources.Webcam(0, sources.Output()).close()
    assert not [r for r in caplog.records if r.fields.get("event") == "camres_ignored"]
