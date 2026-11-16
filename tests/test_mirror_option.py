from psychea import cli
from psychea.session import Session
from psychea.settings import Settings


class NoTransport:
    pass


def test_mirror_flag_is_off_by_default_and_parsed():
    assert Settings().mirror is False
    assert cli.parse_args(["--mirror"]).mirror is True


def test_session_hands_the_flag_to_the_picture_settings():
    s = Session(NoTransport(), Settings(mirror=True, webcam=None, mic="off"), present=lambda: True)   # type: ignore[arg-type]
    assert s.out.mirror_extra == 1
    s = Session(NoTransport(), Settings(webcam=None, mic="off"), present=lambda: True)               # type: ignore[arg-type]
    assert s.out.mirror_extra == 0
