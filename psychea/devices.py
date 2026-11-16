import os


def camera_lines() -> list[str]:
    try:
        import cv2
        from cv2_enumerate_cameras import enumerate_cameras
        backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
        lines = ["  --webcam %d   %s" % (c.index, c.name) for c in enumerate_cameras(backend)]
    except Exception as e:                              # a missing library, or the system's camera listing failing
        return ["  (cannot list cameras: %s)" % e]
    return lines or ["  (no camera found)"]


def microphone_lines() -> list[str]:
    try:
        import sounddevice as sd
        devices, apis = sd.query_devices(), sd.query_hostapis()
    except Exception as e:                              # PortAudio missing or no audio system
        return ["  (cannot list microphones: %s)" % e]
    lines = ["  --micdev %d   %s  [%s]" % (i, d["name"], apis[d["hostapi"]]["name"])
             for i, d in enumerate(devices) if d["max_input_channels"] > 0]
    return lines or ["  (no microphone found)"]


def describe_devices() -> str:
    return "\n".join(["Cameras:"] + camera_lines() + ["Microphones:"] + microphone_lines())
