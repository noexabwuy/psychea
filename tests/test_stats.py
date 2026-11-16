import pytest

from psychea.stats import VideoStats


def stats_with(frames, start=100.0):
    stats = VideoStats(start)
    for nbytes, send_ms, age_ms in frames:
        stats.frame(nbytes, send_ms, age_ms)
    return stats


def test_a_summary_is_due_after_ten_seconds_with_at_least_one_frame():
    stats = VideoStats(100.0)
    assert not stats.due(111.0)                                  # period over but nothing to report
    stats.frame(1000, 1, 1)
    assert not stats.due(109.9) and stats.due(110.0) and stats.due(120.0)


def test_summary_text_and_fields_for_a_period_with_a_limit_and_a_quality():
    stats = stats_with([(10240, 2.0, 30.0), (20480, 4.0, 50.0)])
    text, fields = stats.summary(104.0, quality=80, limit=30720)
    assert text == "Video: 0.5 fps | 15.0 KB/frame of 30.0 | picture delay 40 ms (max 50) | JPEG quality 80"
    assert fields == {"fps": 0.5, "kb_per_frame": 15.0, "limit_kb": 30.0, "send_ms": 3.0, "send_ms_max": 4.0,
                      "age_ms": 40, "age_ms_max": 50, "jpeg_quality": 80}


def test_summary_text_without_limit_and_quality_leaves_those_parts_out():
    text, fields = stats_with([(2048, 1.26, 9.0)]).summary(101.0)
    assert text == "Video: 1.0 fps | 2.0 KB/frame | picture delay 9 ms (max 9)"
    assert fields["limit_kb"] is None and fields["jpeg_quality"] is None
    assert fields["send_ms"] == 1.3 and fields["send_ms_max"] == 1.3


def test_a_zero_limit_is_shown_as_none_in_the_fields_but_the_text_still_names_it():
    text, fields = stats_with([(1024, 1, 1)]).summary(101.0, limit=0)
    assert fields["limit_kb"] == 0 and " of 0.0 " in text


def test_quality_zero_is_still_reported():
    text, _ = stats_with([(1024, 1, 1)]).summary(101.0, quality=0)
    assert text.endswith("| JPEG quality 0")


def test_summary_rounds_age_to_whole_milliseconds_and_kb_to_one_decimal():
    _, fields = stats_with([(1500, 1, 10.6), (1500, 1, 10.7)]).summary(102.0, limit=2000)
    assert fields["age_ms"] == 11 and fields["age_ms_max"] == 11 and fields["kb_per_frame"] == 1.5
    assert fields["limit_kb"] == 2.0 and fields["fps"] == 1.0


def test_summary_starts_the_next_period():
    stats = stats_with([(1000, 1, 1)])
    stats.summary(110.0)
    assert stats.start == 110.0 and stats.sizes == [] and stats.send_ms == [] and stats.age_ms == []
    assert not stats.due(130.0)                                  # no frames since
    stats.frame(500, 1, 1)
    assert stats.due(120.0) and not stats.due(119.9)


def test_summary_of_a_zero_length_period_does_not_divide_by_zero():
    text, fields = stats_with([(1024, 1, 1)]).summary(100.0)
    assert fields["fps"] > 1e6 and text.startswith("Video: ")


def test_restart_forgets_the_frames_and_moves_the_start():
    stats = stats_with([(1, 1, 1)])
    stats.restart(55.0)
    assert (stats.start, stats.sizes) == (55.0, [])


def test_the_period_is_ten_seconds():
    assert VideoStats.PERIOD == pytest.approx(10.0)


def test_kilobytes_are_binary_kilobytes_with_one_decimal():
    text, fields = stats_with([(102_400, 1, 1)]).summary(101.0, limit=1_024_000)
    assert fields["kb_per_frame"] == 100.0 and fields["limit_kb"] == 1000.0
    assert text.startswith("Video: 1.0 fps | 100.0 KB/frame of 1000.0 |")
    _, fields = stats_with([(1000, 1, 1)]).summary(101.0, limit=1500)
    assert fields["kb_per_frame"] == 1.0 and fields["limit_kb"] == 1.5


def test_fps_is_frames_over_the_period_rounded_to_one_decimal():
    text, fields = stats_with([(1024, 1, 1)] * 3).summary(107.0)
    assert fields["fps"] == 0.4 and text.startswith("Video: 0.4 fps")
