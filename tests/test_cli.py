import time

import pytest

from psychea.cli import Settings, connect_within_window, connector, parse_args, wait_until
from psychea.transport import UsbError


def test_defaults_on_a_libusb_host(monkeypatch):
    s = parse_args([])
    assert s.webcam == 0 and s.mic == "pc" and s.micgain == 1.0


def test_flags_and_options():
    s = parse_args(["--testpattern", "--micgain", "0.5", "--mic", "tone", "--micdev", "3"])
    assert s.webcam is None and s.micgain == 0.5 and s.mic == "tone" and s.micdev == 3


@pytest.mark.parametrize("argv", [["--bogus"], ["--micgain"], ["--mic", "radio"], ["--com"], ["--trailer"],
                                  ["--pkt", "576"], ["--fps", "15"], ["--jpeg-optimize"], ["--no-micfilter"],
                                  ["--nostream"], ["--no-logfile"]])      # removed options are rejected
def test_bad_input_prints_usage_and_exits(argv, capsys):
    with pytest.raises(SystemExit) as e:
        parse_args(argv)
    assert e.value.code == 2 and "Usage" in capsys.readouterr().out


def test_options_also_take_their_value_after_an_equals_sign():
    s = parse_args(["--mic=TONE", "--micgain=0.5", "--logfile=a=b.log"])
    assert s.mic == "tone" and s.micgain == 0.5 and s.logfile == "a=b.log"


def test_connect_retries_until_the_handshake_succeeds(monkeypatch):
    monkeypatch.setattr("psychea.cli.time.sleep", lambda s: None)
    calls = []

    def connect():
        calls.append(1)
        if len(calls) < 3:
            raise UsbError("busy")
        return "T", "ok"

    assert connect_within_window(connect) == ("T", 3)


def test_connect_gives_up_after_the_window(monkeypatch):
    monkeypatch.setattr("psychea.cli.ATTEMPT_WINDOW", 0.05)
    real_sleep = time.sleep
    monkeypatch.setattr("psychea.cli.time.sleep", lambda s: real_sleep(0.01))

    def connect():
        raise UsbError("nope")

    transport, attempts = connect_within_window(connect)
    assert transport is None and attempts >= 2


def test_help_prints_usage_and_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as e:
        parse_args(["--help"])
    assert e.value.code == 0 and "Usage" in capsys.readouterr().out


def test_logging_flags():
    s = parse_args(["--verbose", "--logfile", "x.log"])
    assert s.verbose and s.logfile == "x.log"
    assert parse_args([]).logfile == "psychea.log" and not parse_args([]).verbose


def test_list_flag_prints_the_devices_and_exits_quietly(monkeypatch, capsys):
    monkeypatch.setattr("psychea.devices.describe_devices", lambda: "camera 0: Test")
    with pytest.raises(SystemExit) as e:
        parse_args(["--list"])
    assert e.value.code == 0 and capsys.readouterr().out == "camera 0: Test\n"


def test_the_backend_setting_picks_the_transport_to_connect_with():
    from psychea.libusb_backend import LibusbTransport
    from psychea.winusb import WinTransport
    assert connector(Settings(backend="winusb")) == WinTransport.connect
    assert connector(Settings(backend="libusb")) == LibusbTransport.connect


def test_wait_until_sleeps_one_step_after_each_failed_check(monkeypatch):
    slept, answers = [], iter([False, False, True])
    monkeypatch.setattr("psychea.cli.time.sleep", slept.append)
    wait_until(lambda: next(answers), 0.5)
    assert slept == [0.5, 0.5]


def test_install_driver_flag_runs_the_installer_and_exits_with_its_code(monkeypatch):
    monkeypatch.setattr("psychea.driver.run_install", lambda: 7)
    with pytest.raises(SystemExit) as e:
        parse_args(["--install-driver"])
    assert e.value.code == 7


@pytest.mark.parametrize("argv, why", [
    (["--micgain"], "--micgain needs a value"), (["--micgain", "abc"], "--micgain: 'abc' is not a number"),
    (["--micgain", "nan"], "--micgain"),
    (["--micgain", "0"], "--micgain"), (["--micgain", "-1"], "--micgain"), (["--micgain", "inf"], "--micgain"),
    (["--webcam", "-1"], "--webcam"), (["--webcam", "x"], "--webcam: 'x' is not a whole number"),
    (["--mic", "radio"], "--mic")])
def test_bad_values_say_which_option_is_wrong(argv, why, capsys):
    with pytest.raises(SystemExit) as e:
        parse_args(argv)
    assert e.value.code == 2 and why in capsys.readouterr().out.split("Usage")[0]


def test_option_order_does_not_matter():
    assert parse_args(["--webcam", "2", "--testpattern"]).webcam is None
    assert parse_args(["--testpattern", "--webcam", "2"]).webcam is None


def test_mic_mode_is_case_insensitive():
    assert parse_args(["--mic", "TONE"]).mic == "tone"


def _patch_serve(monkeypatch, present_values=None):
    import psychea.cli as cli
    monkeypatch.setattr(cli, "present", lambda: False)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    return cli


def test_serve_once_runs_a_session_closes_the_transport_and_waits_for_unplug(monkeypatch, caplog):
    cli = _patch_serve(monkeypatch)
    monkeypatch.setattr(cli, "present", lambda: True)           # the PSP is still plugged in after the session
    caplog.set_level("INFO", logger="psychea")
    seen = []

    class FakeTransport:
        def close(self):
            seen.append("closed")

    class FakeSession:
        def __init__(self, transport, settings, present):
            seen.append("created")

        def run(self):
            seen.append("run")

    transport = FakeTransport()
    monkeypatch.setattr(cli, "Session", FakeSession)
    monkeypatch.setattr(cli, "wait_until", lambda cond, step: seen.append(("wait", step)))
    monkeypatch.setattr(cli, "connect_within_window", lambda connect: (transport, 1))
    cli.serve_once(cli.Settings(), lambda: (transport, ""))
    assert seen == [("wait", 0.01), "created", "run", "closed", ("wait", 0.2)]
    assert "PSP detected on USB" in caplog.text and "Unplug the cable to start again." in caplog.text


def test_serve_once_keeps_going_when_the_session_crashes(monkeypatch, caplog):
    cli = _patch_serve(monkeypatch)
    closed = []

    class FakeTransport:
        def close(self):
            closed.append(True)

    class Crashing:
        def __init__(self, *a):
            pass

        def run(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(cli, "Session", Crashing)
    monkeypatch.setattr(cli, "wait_until", lambda cond, step: None)
    monkeypatch.setattr(cli, "connect_within_window", lambda connect: (FakeTransport(), 1))
    cli.serve_once(cli.Settings(), lambda: None)
    assert closed == [True]
    assert "The session crashed: RuntimeError('boom')" in caplog.text


def test_serve_once_reports_a_failed_handshake(monkeypatch, caplog):
    cli = _patch_serve(monkeypatch)
    monkeypatch.setattr(cli, "wait_until", lambda cond, step: None)
    monkeypatch.setattr(cli, "connect_within_window", lambda connect: (None, 7))
    cli.serve_once(cli.Settings(), lambda: None)
    assert "Could not connect to the PSP (7 attempts)" in caplog.text


def _patch_main(monkeypatch, cli, log_path):
    calls = []
    monkeypatch.setattr(cli.logs, "setup", lambda verbose, logfile: log_path)
    monkeypatch.setattr(cli.logs, "describe_environment", lambda s, argv: calls.append(("env", argv)))
    monkeypatch.setattr(cli, "connector", lambda s: "connect")

    def stop(settings, connect):
        calls.append(("serve", connect))
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "serve_once", stop)
    monkeypatch.setattr(cli.os, "_exit", lambda code: calls.append(("exit", code)))
    return calls


def test_main_logs_ready_serves_until_ctrl_c_and_exits(monkeypatch, caplog):
    import psychea.cli as cli
    caplog.set_level("INFO", logger="psychea")
    calls = _patch_main(monkeypatch, cli, None)
    cli.main(["--testpattern"])
    assert calls == [("env", ["--testpattern"]), ("serve", "connect"), ("exit", 0)]
    assert "Ready." in caplog.text and "Stopped by the user (Ctrl+C)" in caplog.text
    assert "Detailed log" not in caplog.text


def test_main_announces_the_logfile_when_there_is_one(monkeypatch, caplog, tmp_path):
    import psychea.cli as cli
    caplog.set_level("INFO", logger="psychea")
    _patch_main(monkeypatch, cli, tmp_path / "x.log")
    cli.main([])
    assert "Detailed log: %s" % (tmp_path / "x.log").resolve() in caplog.text


# ---- the connect / run / unplug loop must survive and clean up on every exit path

def _crashing_session(error):
    class Crashing:
        def __init__(self, *a):
            pass

        def run(self):
            if error:
                raise error
    return Crashing


class ClosingTransport:
    def __init__(self, error=None):
        self.closed, self.error = [], error

    def close(self):
        self.closed.append(True)
        if self.error:
            raise self.error


def test_ctrl_c_during_a_session_still_closes_the_transport(monkeypatch):
    cli = _patch_serve(monkeypatch)
    transport = ClosingTransport()
    monkeypatch.setattr(cli, "Session", _crashing_session(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        cli.run_session(transport, cli.Settings())
    assert transport.closed == [True]            # the microphone stream and the USB handles are released


def test_a_transport_that_fails_to_close_does_not_end_the_program(monkeypatch, caplog):
    cli = _patch_serve(monkeypatch)
    monkeypatch.setattr(cli, "Session", _crashing_session(RuntimeError("boom")))
    monkeypatch.setattr(cli, "wait_until", lambda cond, step: None)
    transport = ClosingTransport(OSError("handle"))
    monkeypatch.setattr(cli, "connect_within_window", lambda connect: (transport, 1))
    cli.serve_once(cli.Settings(), lambda: None)           # must not raise
    assert transport.closed == [True] and "handle" in caplog.text


def test_the_unplug_hint_is_not_shown_when_a_session_ended_normally_and_the_cable_is_already_out(monkeypatch, caplog):
    cli = _patch_serve(monkeypatch)                       # present() is False: the PSP left, the session ended
    caplog.set_level("INFO", logger="psychea")
    monkeypatch.setattr(cli, "Session", _crashing_session(None))
    monkeypatch.setattr(cli, "wait_until", lambda cond, step: None)
    monkeypatch.setattr(cli, "connect_within_window", lambda connect: (ClosingTransport(), 1))
    cli.serve_once(cli.Settings(), lambda: None)
    assert "Unplug the cable" not in caplog.text


def test_main_keeps_serving_after_an_unexpected_error_in_the_loop(monkeypatch, caplog):
    import psychea.cli as cli
    caplog.set_level("INFO", logger="psychea")
    calls = _patch_main(monkeypatch, cli, None)
    turns = iter([OSError("usb stack"), KeyboardInterrupt()])

    def serve(settings, connect):
        raise next(turns)
    monkeypatch.setattr(cli, "serve_once", serve)
    pauses = []
    monkeypatch.setattr(cli.time, "sleep", pauses.append)
    cli.main([])
    assert calls[-1] == ("exit", 0) and pauses == [1.0]
    assert "usb stack" in caplog.text and "Stopped by the user" in caplog.text


def test_an_unexpected_error_in_one_handshake_attempt_does_not_use_up_the_window(monkeypatch, caplog):
    # The PSP's game checks the handshake for a fraction of a second: an attempt that fails in some other way
    # than UsbError (the registry key vanishing while the PSP re-enumerates) must be retried like any other.
    import types

    cli = _patch_serve(monkeypatch)
    now = [100.0]
    monkeypatch.setattr(cli, "time", types.SimpleNamespace(
        monotonic=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s)))
    results = iter([OSError("registry key gone"), ("T", "ok")])

    def connect():
        r = next(results)
        if isinstance(r, Exception):
            raise r
        return r
    assert cli.connect_within_window(connect) == ("T", 2)
    assert "registry key gone" in caplog.text


def test_usage_doc_prints_exactly_what_help_prints():
    # docs/usage.md promises that --help prints 'exactly this text': keep the two copies from drifting apart.
    import re
    from pathlib import Path

    from psychea.cli import USAGE
    usage = (Path(__file__).resolve().parent.parent / "docs" / "usage.md").read_text(encoding="utf-8")
    block = re.search(r"prints exactly this text:\n\n```\n(.*?)\n```", usage, re.S)
    assert block, "the Command line block of docs/usage.md was not found"
    assert block.group(1).strip() == USAGE.strip()


def test_ctrl_c_right_after_the_handshake_does_not_leak_the_transport(monkeypatch):
    # KeyboardInterrupt is not an Exception: the transport that connect() just opened must still be closed.
    from psychea import cli

    class T:
        closed = False

        def close(self):
            self.closed = True
    t = T()

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli.log, "info", interrupted)               # the line right after connect() returns
    with pytest.raises(KeyboardInterrupt):
        connect_within_window(lambda: (t, "ok"))
    assert t.closed
