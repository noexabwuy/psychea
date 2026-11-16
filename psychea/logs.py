import logging
import platform
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

LOG_FILE_BYTES, LOG_BACKUPS = 5_000_000, 3      # psychea.log is rotated at 5 MB and three older files are kept

ROOT = "psychea"
LEVEL_NAMES = {logging.DEBUG: "DEBUG", logging.INFO: "INFO", logging.WARNING: "WARN", logging.ERROR: "ERROR"}


def _stamp(record: logging.LogRecord, date: bool = False) -> str:
    t = time.localtime(record.created)
    out = time.strftime("%Y-%m-%d %H:%M:%S" if date else "%H:%M:%S", t)
    return "%s.%03d" % (out, int(record.msecs))


def format_fields(fields: dict[str, Any]) -> str:
    return " ".join("%s=%s" % (k, v) for k, v in fields.items() if v is not None)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        prefix = {logging.WARNING: "WARNING: ", logging.ERROR: "ERROR: "}.get(record.levelno, "")
        text = "[%s] %s%s" % (_stamp(record), prefix, record.getMessage())
        if record.exc_info:
            text += "\n" + self.formatException(record.exc_info)
        return text


class FileFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = format_fields(getattr(record, "fields", {}))
        component = record.name.removeprefix(ROOT + ".")
        text = "%s %-5s %-8s %s" % (_stamp(record, date=True), LEVEL_NAMES.get(record.levelno, "?"), component,
                                    record.getMessage())
        if fields:
            text += " | " + fields
        if record.exc_info:
            text += "\n" + self.formatException(record.exc_info)
        return text


class Log:
    def __init__(self, name: str) -> None:
        self._logger = logging.getLogger("%s.%s" % (ROOT, name))

    def _emit(self, level: int, text: str, fields: dict[str, Any], exc_info: bool = False) -> None:
        self._logger.log(level, text, extra={"fields": fields}, exc_info=exc_info)

    def debug(self, text: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, text, fields)

    def info(self, text: str, **fields: Any) -> None:
        self._emit(logging.INFO, text, fields)

    def warning(self, text: str, **fields: Any) -> None:
        self._emit(logging.WARNING, text, fields)

    def error(self, text: str, exc_info: bool = False, **fields: Any) -> None:
        self._emit(logging.ERROR, text, fields, exc_info)


def get(name: str) -> Log:
    return Log(name)


def setup(verbose: bool = False, logfile: Optional[str] = "psychea.log") -> Optional[Path]:
    root = logging.getLogger(ROOT)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.DEBUG)
    root.propagate = False
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(ConsoleFormatter())
    root.addHandler(console)
    if logfile is None:
        return None
    path = Path(logfile)
    try:
        file_handler = RotatingFileHandler(path, maxBytes=LOG_FILE_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
    except OSError as e:                         # read-only folder, a directory, a bad path: the console still works
        get("cli").warning("Cannot write the log file %s: %s. Continuing without it" % (path, e),
                           event="logfile_failed", path=str(path), error=e)
        return None
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(FileFormatter())
    root.addHandler(file_handler)
    return path


def describe_environment(settings: Any, argv: list[str]) -> None:
    from importlib import metadata
    try:
        version = metadata.version("psychea")
    except metadata.PackageNotFoundError:
        version = "dev"
    get("cli").debug("Run started", event="start", version=version, python=platform.python_version(),
                     os=platform.platform(), argv=" ".join(argv) or "-", settings=vars(settings))
