import index_library as lib


def test_hms_to_seconds():
    assert lib.hms_to_seconds("00", "01", "02", "5") == 62.5
    assert lib.hms_to_seconds("1", "00", "00", "000") == 3600.0


def test_parse_srt_basic():
    srt = "1\n00:00:01,000 --> 00:00:03,500\nHello there friend.\n\n2\n00:00:04,000 --> 00:00:05,000\nHow are you?\n"
    segs = lib.parse_srt(srt)
    assert len(segs) == 2
    assert segs[0]["text"] == "Hello there friend."
    assert segs[0]["start"] == 1.0
    assert segs[0]["end"] == 3.5


def test_parse_srt_multiline_cue():
    srt = "1\n00:00:01,000 --> 00:00:04,000\nLine one\nLine two\n"
    segs = lib.parse_srt(srt)
    assert segs[0]["text"] == "Line one Line two"


def test_parse_srt_filters_cues_and_tags():
    srt = (
        "1\n00:00:01,000 --> 00:00:02,000\n(music)\n\n"
        "2\n00:00:03,000 --> 00:00:05,000\n<i>Keep going</i>\n"
    )
    segs = lib.parse_srt(srt)
    assert len(segs) == 1
    assert segs[0]["text"] == "Keep going"


def test_parse_srt_duration_filter():
    srt = "1\n00:00:01,000 --> 00:00:30,000\nToo long to be dialogue\n"
    assert lib.parse_srt(srt) == []


def test_parse_vtt():
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:03.500\nHello from vtt\n"
    segs = lib.parse_vtt(vtt)
    assert segs and segs[0]["text"] == "Hello from vtt"


def test_pick_srt_smallest():
    files = [
        {"name": "a.en.srt", "size": "5000"},
        {"name": "b.srt", "size": "200"},
        {"name": "no_subs.txt", "size": "10"},
    ]
    assert lib.pick_srt(files)["name"] == "b.srt"


def test_pick_video_smallest_mp4():
    files = [
        {"name": "big.mp4", "size": "999999"},
        {"name": "small.mp4", "size": "12345"},
        {"name": "audio.mp3", "size": "1"},
    ]
    assert lib.pick_video(files)["name"] == "small.mp4"


def test_clean_text_cue_only():
    assert lib.clean_text("(applause)") is None
    assert lib.clean_text("speech") is None