import types

from psychea import devices


def test_microphones_list_only_inputs(monkeypatch):
    fake = types.SimpleNamespace(
        query_devices=lambda: [{"name": "Speakers", "max_input_channels": 0, "hostapi": 0},
                               {"name": "USB Mic", "max_input_channels": 1, "hostapi": 0},
                               {"name": "USB Mic", "max_input_channels": 1, "hostapi": 1}],
        query_hostapis=lambda: [{"name": "MME"}, {"name": "Windows WASAPI"}])
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", fake)
    assert devices.microphone_lines() == ["  --micdev 1   USB Mic  [MME]", "  --micdev 2   USB Mic  [Windows WASAPI]"]


def test_missing_audio_system_is_reported_not_raised(monkeypatch):
    def boom():
        raise OSError("PortAudio library not found")
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", types.SimpleNamespace(query_devices=boom))
    assert "PortAudio" in devices.microphone_lines()[0]


def test_describe_has_both_sections():
    text = devices.describe_devices()
    assert "Cameras:" in text and "Microphones:" in text


def fake_camera_modules(monkeypatch, cameras, backends):
    import sys
    cv2 = types.SimpleNamespace(CAP_DSHOW=700, CAP_ANY=0)

    def enumerate_cameras(backend):
        backends.append(backend)
        return cameras
    monkeypatch.setitem(sys.modules, "cv2", cv2)
    listing = types.SimpleNamespace(enumerate_cameras=enumerate_cameras)
    monkeypatch.setitem(sys.modules, "cv2_enumerate_cameras", listing)


def test_cameras_are_listed_with_the_number_to_pass_to_webcam(monkeypatch):
    backends = []
    fake_camera_modules(monkeypatch, [types.SimpleNamespace(index=0, name="Integrated"),
                                      types.SimpleNamespace(index=2, name="USB Cam")], backends)
    assert devices.camera_lines() == ["  --webcam 0   Integrated", "  --webcam 2   USB Cam"]


def test_cameras_are_enumerated_with_directshow_on_windows_and_the_default_elsewhere(monkeypatch):
    import os
    backends = []
    fake_camera_modules(monkeypatch, [], backends)
    with monkeypatch.context() as m:
        m.setattr(os, "name", "nt")
        devices.camera_lines()
    with monkeypatch.context() as m:
        m.setattr(os, "name", "posix")
        devices.camera_lines()
    assert backends == [700, 0]


def test_no_camera_and_no_microphone_are_said_so(monkeypatch):
    import sys
    fake_camera_modules(monkeypatch, [], [])
    assert devices.camera_lines() == ["  (no camera found)"]
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(
        query_devices=lambda: [{"name": "Speakers", "max_input_channels": 0, "hostapi": 0}],
        query_hostapis=lambda: [{"name": "MME"}]))
    assert devices.microphone_lines() == ["  (no microphone found)"]


def test_a_missing_camera_library_is_reported_not_raised(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "cv2_enumerate_cameras", None)
    (line,) = devices.camera_lines()
    assert line.startswith("  (cannot list cameras: ") and line.endswith(")")


def test_a_failing_audio_system_is_named_in_the_line(monkeypatch):
    import sys

    def boom():
        raise OSError("no PortAudio")
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(query_devices=boom))
    assert devices.microphone_lines() == ["  (cannot list microphones: no PortAudio)"]


def test_the_device_overview_is_two_titled_sections_one_entry_per_line(monkeypatch):
    monkeypatch.setattr(devices, "camera_lines", lambda: ["  cam A", "  cam B"])
    monkeypatch.setattr(devices, "microphone_lines", lambda: ["  mic A"])
    assert devices.describe_devices() == "Cameras:\n  cam A\n  cam B\nMicrophones:\n  mic A"


def test_a_camera_listing_that_fails_is_reported_not_raised(monkeypatch):
    import sys

    def boom(backend):
        raise OSError("DirectShow is not available")
    monkeypatch.setitem(sys.modules, "cv2", types.SimpleNamespace(CAP_DSHOW=700, CAP_ANY=0))
    monkeypatch.setitem(sys.modules, "cv2_enumerate_cameras", types.SimpleNamespace(enumerate_cameras=boom))
    assert devices.camera_lines() == ["  (cannot list cameras: DirectShow is not available)"]
    assert "cannot list cameras" in devices.describe_devices()
