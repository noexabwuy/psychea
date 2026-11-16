import logging

import pytest

from psychea import logs


@pytest.fixture(autouse=True)
def clean_logging():
    yield
    root = logging.getLogger("psychea")
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.propagate = True
    root.setLevel(logging.NOTSET)


def test_console_shows_plain_info_lines_without_fields(capsys, tmp_path):
    logs.setup(logfile=None)
    logs.get("session").info("Game started the camera", event="video_start", w=480)
    out = capsys.readouterr().out
    assert out.endswith("Game started the camera\n") and out.startswith("[") and "w=480" not in out


def test_console_hides_debug_unless_verbose(capsys):
    logs.setup(logfile=None)
    logs.get("x").debug("protocol detail")
    assert capsys.readouterr().out == ""
    logs.setup(verbose=True, logfile=None)
    logs.get("x").debug("protocol detail")
    assert "protocol detail" in capsys.readouterr().out


def test_warnings_and_errors_are_labelled_on_the_console(capsys):
    logs.setup(logfile=None)
    logs.get("x").warning("careful")
    logs.get("x").error("broken")
    out = capsys.readouterr().out
    assert "WARNING: careful" in out and "ERROR: broken" in out


def test_the_log_file_has_debug_detail_component_and_key_value_fields(tmp_path):
    path = logs.setup(logfile=str(tmp_path / "t.log"))
    logs.get("session").debug("PSP message HELLO", event="psp_message", id=2, cmd="0x2B", payload=None)
    logs.get("winusb").warning("slow", event="slow", ms=19)
    text = path.read_text()
    lines = text.splitlines()
    assert len(lines) == 2
    assert " DEBUG " in lines[0] and " session " in lines[0] and lines[0].endswith("| event=psp_message id=2 cmd=0x2B")
    assert " WARN " in lines[1] and "| event=slow ms=19" in lines[1]
    assert lines[0][4] == "-" and lines[0][10] == " "                       # full date-time stamp


def test_exceptions_are_logged_with_a_traceback(tmp_path, capsys):
    path = logs.setup(logfile=str(tmp_path / "t.log"))
    try:
        raise ZeroDivisionError("boom")
    except ZeroDivisionError:
        logs.get("cli").error("The session crashed", exc_info=True, event="crash")
    assert "ZeroDivisionError" in path.read_text() and "ZeroDivisionError" in capsys.readouterr().out


def test_setup_twice_does_not_duplicate_lines(tmp_path, capsys):
    logs.setup(logfile=None)
    logs.setup(logfile=None)
    logs.get("x").info("once")
    assert capsys.readouterr().out.count("once") == 1


def test_environment_line_names_version_os_and_settings(tmp_path):
    from psychea.settings import Settings
    path = logs.setup(logfile=str(tmp_path / "t.log"))
    logs.describe_environment(Settings(), ["--micgain", "0.4"])
    line = path.read_text()
    assert "event=start" in line and "python=" in line and "argv=--micgain 0.4" in line and "mic" in line


def make_record(level=logging.INFO, name="psychea.session", msg="hello", fields=None, exc_info=None):
    record = logging.LogRecord(name, level, __file__, 1, msg, None, exc_info)
    record.created, record.msecs = 1_700_000_000.0, 7.9
    if fields is not None:
        record.fields = fields
    return record


def test_console_line_is_time_then_plain_message():
    import re
    line = logs.ConsoleFormatter().format(make_record())
    assert re.fullmatch(r"\[\d\d:\d\d:\d\d\.007\] hello", line)


def test_console_line_has_no_label_for_info_and_debug():
    for level in (logging.DEBUG, logging.INFO):
        assert logs.ConsoleFormatter().format(make_record(level)).endswith("] hello")


def test_console_traceback_follows_on_its_own_line():
    try:
        raise ValueError("bad")
    except ValueError:
        import sys
        text = logs.ConsoleFormatter().format(make_record(logging.ERROR, exc_info=sys.exc_info()))
    first, second = text.split("\n")[:2]
    assert first.endswith("ERROR: hello") and second == "Traceback (most recent call last):"
    assert text.endswith("ValueError: bad")


def test_file_line_layout_is_date_time_level_component_message_and_fields():
    import re
    line = logs.FileFormatter().format(make_record(fields={"event": "x", "n": 3, "skipped": None}))
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.007 INFO  session  hello \| event=x n=3", line)


def test_file_line_without_fields_has_no_separator_and_unlabelled_levels_show_a_question_mark():
    line = logs.FileFormatter().format(make_record(logging.CRITICAL))
    assert line.endswith(" ?     session  hello")
    assert line.endswith("hello") and "|" not in line
    assert logs.FileFormatter().format(make_record(fields={})).endswith(" session  hello")


@pytest.mark.parametrize("level,label", [(logging.DEBUG, "DEBUG"), (logging.INFO, "INFO "),
                                         (logging.WARNING, "WARN "), (logging.ERROR, "ERROR")])
def test_file_level_labels(level, label):
    assert (" %s session  hello" % label) in logs.FileFormatter().format(make_record(level))


def test_file_traceback_follows_on_its_own_line():
    try:
        raise ValueError("bad")
    except ValueError:
        import sys
        text = logs.FileFormatter().format(make_record(logging.ERROR, fields={"event": "e"}, exc_info=sys.exc_info()))
    lines = text.split("\n")
    assert lines[0].endswith("| event=e") and lines[1] == "Traceback (most recent call last):"


def test_the_component_name_loses_only_the_leading_psychea_prefix():
    line = logs.FileFormatter().format(make_record(name="psychea.psychea.x"))
    assert " psychea.x " in line


def test_components_log_under_the_psychea_logger_and_errors_default_to_no_traceback(tmp_path):
    path = logs.setup(logfile=str(tmp_path / "t.log"))
    log = logs.get("session")
    assert log._logger.name == "psychea.session"
    log.error("plain", event="e")
    text = path.read_text()
    assert "Traceback" not in text and "NoneType" not in text


def test_log_levels_reach_the_file_in_full_and_the_console_from_info(tmp_path, capsys):
    path = logs.setup(logfile=str(tmp_path / "t.log"))
    log = logs.get("x")
    log.debug("d")
    log.info("i")
    log.warning("w")
    log.error("e")
    assert [line.split()[2] for line in path.read_text().splitlines()] == ["DEBUG", "INFO", "WARN", "ERROR"]
    assert [line.split("] ", 1)[1] for line in capsys.readouterr().out.splitlines()] == ["i", "WARNING: w", "ERROR: e"]


def test_setup_without_arguments_logs_to_psychea_log_in_the_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = logs.setup()
    logs.get("x").info("hi")
    assert str(path) == "psychea.log" and (tmp_path / "psychea.log").read_text().count("hi") == 1


def test_setup_without_a_logfile_returns_none_and_writes_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert logs.setup(logfile=None) is None
    logs.get("x").info("hi")
    assert list(tmp_path.iterdir()) == []


def test_psychea_records_do_not_reach_the_root_logger(tmp_path):
    seen = []

    class Collect(logging.Handler):
        def emit(self, record):
            seen.append(record)
    collector = Collect()
    logging.getLogger().addHandler(collector)
    try:
        logs.setup(logfile=None)
        logs.get("x").error("once")
    finally:
        logging.getLogger().removeHandler(collector)
    assert seen == []


def test_setup_replaces_an_earlier_file_handler_and_closes_it(tmp_path):
    logs.setup(logfile=str(tmp_path / "a.log"))
    first = logging.getLogger("psychea").handlers[-1]
    logs.setup(logfile=str(tmp_path / "b.log"))
    assert first.stream is None                                    # closed
    logs.get("x").info("only b")
    assert "only b" not in (tmp_path / "a.log").read_text()
    assert "only b" in (tmp_path / "b.log").read_text()


def test_the_log_file_is_utf8(tmp_path):
    path = logs.setup(logfile=str(tmp_path / "t.log"))
    logs.get("x").info("Grüße 日本")
    assert "Grüße 日本" in path.read_bytes().decode("utf-8")


def test_the_log_file_rotates_below_5_mb_and_keeps_three_old_files(tmp_path):
    path = logs.setup(logfile=str(tmp_path / "t.log"))
    log = logs.get("x")
    log.info("")
    probe = len(path.read_bytes())
    message = "." * (1000 - probe)                                  # every line is exactly 1000 bytes
    for _ in range(21_000):
        log.info(message)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["t.log", "t.log.1", "t.log.2", "t.log.3"]
    # the handler counts a line as len+1 bytes, Windows writes CR LF (one byte more per line): ~0.1 % slack
    assert all(0 < p.stat().st_size < 5_010_000 for p in tmp_path.iterdir())
    assert (tmp_path / "t.log.1").stat().st_size > 4_900_000


def test_environment_line_is_a_debug_line_of_the_cli_with_all_fields(tmp_path, monkeypatch):
    from importlib import metadata

    from psychea.settings import Settings
    monkeypatch.setattr(metadata, "version", lambda name: "9.8.7" if name == "psychea" else "wrong")
    path = logs.setup(logfile=str(tmp_path / "t.log"))
    logs.describe_environment(Settings(mic="tone"), [])
    line = path.read_text().strip()
    assert " DEBUG " in line and " cli " in line and "Run started | " in line
    assert "event=start version=9.8.7 python=" in line and " argv=- settings={" in line and "'mic': 'tone'" in line


def test_environment_line_says_dev_when_the_package_is_not_installed(tmp_path, monkeypatch):
    from importlib import metadata

    from psychea.settings import Settings

    def missing(name):
        raise metadata.PackageNotFoundError(name)
    monkeypatch.setattr(metadata, "version", missing)
    path = logs.setup(logfile=str(tmp_path / "t.log"))
    logs.describe_environment(Settings(), ["a", "b"])
    assert "version=dev " in path.read_text() and "argv=a b " in path.read_text()


def test_a_log_file_that_cannot_be_opened_does_not_stop_the_program(tmp_path, capsys):
    # Run from a read-only folder (or with --logfile pointing at a folder): the console log must still work.
    path = logs.setup(logfile=str(tmp_path))                 # a directory: opening it for writing fails
    assert path is None
    logs.get("cli").info("Ready", event="ready")
    out = capsys.readouterr().out
    assert "Ready" in out and "Cannot write the log file" in out
