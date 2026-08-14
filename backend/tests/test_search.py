from types import SimpleNamespace

import pytest

from app.services.search import LexicalSearchService
from app.utils.text import tokenize

svc = LexicalSearchService()


def seg(video_id, text, start, end, character="Unknown"):
    return SimpleNamespace(
        id=f"{video_id}-{start}", videoId=video_id, character=character,
        text=text, startTime=start, endTime=end, confidence=0.9, createdAt=None,
    )


def videos(*titles):
    return {str(i): SimpleNamespace(id=str(i), title=t) for i, t in enumerate(titles)}


def test_word_span_contiguous():
    toks = tokenize("I do believe I dont know anything")
    span = svc._word_span(toks, tokenize("I dont know"))
    assert span == (3, 5)


def test_word_span_not_contiguous():
    toks = tokenize("I do believe I know dont anything")
    assert svc._word_span(toks, tokenize("I dont know")) is None


def test_score_phrase_trims_to_span():
    toks = tokenize("I dont know")
    s = seg("0", "Well I dont know about that one", 10.0, 14.0)
    res = svc._score_phrase([s], videos("Film A"), toks, 5)
    assert len(res) == 1
    # 7 tokens: span at (1,3), so start = 10 + (1/7)*4, end = 10 + (4/7)*4
    assert res[0].startTime == round(10 + (1 / 7) * 4, 3)
    assert res[0].endTime == round(10 + (4 / 7) * 4, 3)
    assert res[0].videoTitle == "Film A"


def test_score_phrase_exact_match_bonus():
    toks = tokenize("hello there")
    exact = seg("0", "Hello there", 1.0, 3.0)
    loose = seg("1", "Well hello there my friend", 1.0, 4.0)
    res = svc._score_phrase([exact, loose], videos("A", "B"), toks, 5)
    assert res[0].videoId == "0"
    assert res[0].score == 1.0 + svc.PHRASE_BONUS


def test_score_phrase_min_clip_length():
    toks = tokenize("go")
    s = seg("0", "Go", 5.0, 5.3)  # 0.3s below minimum: clamps within segment bounds
    res = svc._score_phrase([s], videos("A"), toks, 5)
    assert len(res) == 1
    assert res[0].endTime - res[0].startTime == pytest.approx(0.3)


def test_score_query_overlap_coverage():
    toks = tokenize("help me")
    s = seg("0", "Please help me now", 1.0, 4.0)
    res = svc._score_query([s], videos("A"), "help me", toks, 5)
    assert res[0].score == pytest.approx(1.0 + svc.PHRASE_BONUS)  # full coverage + phrase bonus


def test_score_query_partial_overlap():
    toks = tokenize("help me now")
    s = seg("0", "Help yourself", 1.0, 3.0)
    res = svc._score_query([s], videos("A"), "help me now", toks, 5)
    assert len(res) == 1
    assert res[0].score == pytest.approx(1 / 3)


def test_select_non_overlapping_same_video():
    ms = [
        SimpleNamespace(videoId="0", startTime=0.0, endTime=2.0, order=0, score=0.9, **{}),
        SimpleNamespace(videoId="0", startTime=1.5, endTime=3.0, order=1, score=0.9, **{}),
        SimpleNamespace(videoId="0", startTime=5.0, endTime=6.0, order=2, score=0.9, **{}),
    ]
    selected = svc._select_non_overlapping(ms, 5)
    assert len(selected) == 2  # overlapping one dropped


def test_all_tokens_filter():
    f = svc._all_tokens_filter(tokenize("I dont know"))
    assert f == {
        "$and": [
            {"text": {"$regex": "i", "$options": "i"}},
            {"text": {"$regex": "dont", "$options": "i"}},
            {"text": {"$regex": "know", "$options": "i"}},
        ]
    }


def test_any_tokens_filter():
    f = svc._any_tokens_filter(tokenize("run away"))
    assert "$or" in f and len(f["$or"]) == 2