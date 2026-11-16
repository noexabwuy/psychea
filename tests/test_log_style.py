# The logging style of the whole program, enforced on the source: every message is an event the log file can be
# searched for, named the same way, and worded the same way.
import ast
import re
from pathlib import Path

LEVELS = {"debug", "info", "warning", "error"}
SOURCE = Path(__file__).resolve().parent.parent / "psychea"


def log_calls():
    # (file, line, level, message node, {keyword: value node}) of every log.<level>(...) in the program.
    found = []
    for path in sorted(SOURCE.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LEVELS):
                continue
            owner = node.func.value
            if isinstance(owner, ast.Name) and owner.id == "log" or isinstance(owner, ast.Call):
                found.append((path.name, node.lineno, node.func.attr, node.args[0],
                              {k.arg: k.value for k in node.keywords if k.arg}))
    return found


def message_start(node):
    # The first literal text of a message: "text" or "text %d" % x or "a" "b" % x.
    if isinstance(node, ast.BinOp):
        node = node.left
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def test_both_backends_word_the_audio_lines_the_same_way():
    from psychea import transport
    assert transport.audio_failure_text("0xC000000D", 2) == (
        "An audio transfer failed (status 0xC000000D, failures so far: 2)")
    assert transport.audio_stats_text(5000, 1) == "Audio: 5 s sent | failed transfers 1"
    assert transport.audio_stats_text(5000, 0, ", delay 30 ms") == (
        "Audio: 5 s sent | failed transfers 0, delay 30 ms")
    assert [n for n in range(1, 700) if transport.report_failure(n)] == [1, 2, 3, 100, 200, 300, 400, 500, 600]


def test_both_backends_use_the_shared_audio_wording():
    for name in ("libusb_backend.py", "winusb.py"):
        text = (SOURCE / name).read_text(encoding="utf-8")
        assert "audio_failure_text" in text and "audio_stats_text" in text and "report_failure" in text, name


def test_the_scan_finds_the_log_calls():
    assert len(log_calls()) > 40


def test_every_message_names_an_event_in_snake_case():
    bad = [(f, line) for f, line, _, _, kw in log_calls()
           if not (isinstance(kw.get("event"), ast.Constant) and re.fullmatch(r"[a-z][a-z0-9_]*", kw["event"].value))]
    assert bad == []


def test_failures_are_named_failed_not_error():
    bad = [(f, line, kw["event"].value) for f, line, _, _, kw in log_calls()
           if "event" in kw and kw["event"].value.endswith("_error")]
    assert bad == []


def test_messages_start_with_a_capital_letter():
    bad = [(f, line, message_start(msg)) for f, line, _, msg, _ in log_calls()
           if message_start(msg) and not message_start(msg)[0].isupper()]
    assert bad == []


def test_a_problem_is_never_only_debug_when_it_says_it_could_not_do_something():
    bad = [(f, line) for f, line, level, msg, _ in log_calls()
           if level == "debug" and (message_start(msg) or "").startswith(("Could not", "Cannot"))]
    assert bad == []
