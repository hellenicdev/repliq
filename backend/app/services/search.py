"""Search service.

The search layer is deliberately replaceable: routes and generation code
depend only on the SearchService interface. Phase 1 ships the lexical
implementation; Phase 4 swaps in a hybrid lexical + vector search without
touching the rest of the application.
"""

from abc import ABC, abstractmethod
import asyncio
import re
from dataclasses import dataclass
from itertools import groupby

from motor.motor_asyncio import AsyncIOMotorDatabase

from ..config import settings
from ..models.dialogue import Dialogue
from ..models.video import Video
from ..repositories import dialogue as dialogue_repo
from ..repositories import videos as videos_repo
from ..utils.text import normalize_text, tokenize


@dataclass
class ClipMatch:
    videoId: str
    videoTitle: str
    character: str
    text: str
    startTime: float
    endTime: float
    score: float
    order: int  # position of the first matched query token (for sensible playback order)


class NoClipsFound(Exception):
    pass


class SearchService(ABC):
    @abstractmethod
    async def search(self, db: AsyncIOMotorDatabase, query: str, limit: int) -> list[ClipMatch]:
        """Return up to `limit` clips matching the query, best first."""
        raise NotImplementedError


class LexicalSearchService(SearchService):
    """Token-overlap scoring with an exact-phrase bonus.

    Each phrase is matched against dialogue segments that contain all of its
    words in order, and the clip times are *trimmed* to the words' span
    (subtitle durations are split proportionally by word count), so the
    extracted clip only plays the requested words even when the source
    segment contains extra dialogue. Clips are selected greedily so chosen
    segments never overlap in time within the same video, and clips may come
    from different films.
    """

    PHRASE_BONUS = 0.35
    MIN_CLIP_SECONDS = 0.5

    @staticmethod
    def _word_span(seg_tokens: list[str], query_tokens: list[str]) -> tuple[int, int] | None:
        """Index range (first, last) of the first contiguous occurrence of
        query_tokens inside seg_tokens, or None if absent. Contiguity is what
        makes the trimmed clip contain exactly the requested words — nothing
        in between, nothing around it."""
        n, m = len(seg_tokens), len(query_tokens)
        for i in range(n - m + 1):
            if seg_tokens[i : i + m] == query_tokens:
                return (i, i + m - 1)
        return None

    @staticmethod
    def _all_tokens_filter(query_tokens: list[str]) -> dict | None:
        """Mongo filter over `text` matching only segments that contain all
        query tokens (case-insensitive). A substring superset of what Python
        scoring can accept, so it never drops a real match."""
        if not query_tokens:
            return None
        return {"$and": [{"text": {"$regex": re.escape(t), "$options": "i"}} for t in query_tokens]}

    @staticmethod
    def _any_tokens_filter(query_tokens: list[str]) -> dict | None:
        if not query_tokens:
            return None
        return {"$or": [{"text": {"$regex": re.escape(t), "$options": "i"}} for t in query_tokens]}

    async def _phrase_matches(self, db: AsyncIOMotorDatabase, phrase: str, limit: int) -> list[ClipMatch]:
        query_tokens = tokenize(phrase)
        if not query_tokens:
            return []

        segments = await dialogue_repo.list_dialogue(db, self._all_tokens_filter(query_tokens))
        videos = {v.id: v for v in await videos_repo.list_videos(db)}
        return self._score_phrase(segments, videos, query_tokens, limit)

    def _score_phrase(
        self,
        segments: list[Dialogue],
        videos: dict[str, Video],
        query_tokens: list[str],
        limit: int,
    ) -> list[ClipMatch]:
        matches: list[ClipMatch] = []
        for seg in segments:
            seg_tokens = tokenize(seg.text)
            span = self._word_span(seg_tokens, query_tokens)
            if span is None:
                continue

            first, last = span
            n = len(seg_tokens)
            dur = max(seg.endTime - seg.startTime, 0.001)
            start = seg.startTime + (first / n) * dur
            end = seg.startTime + ((last + 1) / n) * dur
            if end - start < self.MIN_CLIP_SECONDS:
                mid = (start + end) / 2
                start = max(seg.startTime, mid - self.MIN_CLIP_SECONDS / 2)
                end = min(seg.endTime, mid + self.MIN_CLIP_SECONDS / 2)

            extra_words = n - len(query_tokens)
            score = 1.0 - 0.02 * extra_words
            if query_tokens == seg_tokens:
                score += self.PHRASE_BONUS

            video = videos.get(seg.videoId)
            matches.append(
                ClipMatch(
                    videoId=seg.videoId,
                    videoTitle=video.title if video else "Unknown",
                    character=seg.character,
                    text=seg.text,
                    startTime=round(start, 3),
                    endTime=round(end, 3),
                    score=round(score, 4),
                    order=first,
                )
            )

        matches.sort(key=lambda m: -m.score)
        return matches[:limit]

    async def search(self, db: AsyncIOMotorDatabase, query: str, limit: int) -> list[ClipMatch]:
        query_norm = normalize_text(query)
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        segments = await dialogue_repo.list_dialogue(db, self._any_tokens_filter(query_tokens))
        videos = {v.id: v for v in await videos_repo.list_videos(db)}
        return self._score_query(segments, videos, query_norm, query_tokens, limit)

    def _score_query(
        self,
        segments: list[Dialogue],
        videos: dict[str, Video],
        query_norm: str,
        query_tokens: list[str],
        limit: int,
    ) -> list[ClipMatch]:
        matches: list[ClipMatch] = []
        for seg in segments:
            seg_tokens = tokenize(seg.text)
            matched = set(query_tokens) & set(seg_tokens)
            if not matched:
                continue

            coverage = len(matched) / len(query_tokens)
            score = coverage
            if query_norm and query_norm in normalize_text(seg.text):
                score += self.PHRASE_BONUS

            first_hit = min(query_tokens.index(t) for t in matched)
            video = videos.get(seg.videoId)
            matches.append(
                ClipMatch(
                    videoId=seg.videoId,
                    videoTitle=video.title if video else "Unknown",
                    character=seg.character,
                    text=seg.text,
                    startTime=seg.startTime,
                    endTime=seg.endTime,
                    score=score,
                    order=first_hit,
                )
            )

        matches.sort(key=lambda m: (-m.score, m.order))
        return self._select_non_overlapping(matches, limit)

    @staticmethod
    def _select_non_overlapping(matches: list[ClipMatch], limit: int) -> list[ClipMatch]:
        selected: list[ClipMatch] = []
        per_video: dict[str, list[tuple[float, float]]] = {}

        for m in matches:
            if len(selected) >= limit:
                break
            spans = per_video.get(m.videoId, [])
            if any(m.startTime < end and m.endTime > start for start, end in spans):
                continue
            if sum(c.endTime - c.startTime for c in selected) + (m.endTime - m.startTime) > settings.max_total_duration:
                continue
            spans.append((m.startTime, m.endTime))
            per_video[m.videoId] = spans
            selected.append(m)

        selected.sort(key=lambda m: (m.order, -m.score))
        return selected

    async def search_phrases(self, db: AsyncIOMotorDatabase, phrases: list[str], limit: int) -> list[ClipMatch]:
        """Search each phrase and assemble a sentence-ordered clip list.

        Picks the single best non-overlapping match per phrase and keeps the
        sentence order, capping total clips and duration via settings. Clips
        may come from different films. If no phrase matches cleanly, the
        sentence is simply not playable — better than playing wrong words.
        """
        chosen: list[ClipMatch] = []
        used_spans: dict[str, list[tuple[float, float]]] = {}
        candidate_sets = await asyncio.gather(
            *(self._phrase_matches(db, phrase, limit=5) for phrase in phrases)
        )
        for candidates in candidate_sets:
            best = None
            for c in candidates:
                spans = used_spans.get(c.videoId, [])
                if any(c.startTime < end and c.endTime > start for start, end in spans):
                    continue
                best = c
                break
            if best is None:
                continue
            used_spans.setdefault(best.videoId, []).append((best.startTime, best.endTime))
            chosen.append(best)
            if len(chosen) >= limit:
                break
        return chosen


def get_search_service() -> SearchService:
    """Factory so a future semantic/hybrid service can be swapped in here."""
    return LexicalSearchService()
