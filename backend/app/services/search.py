"""Search service.

The search layer is deliberately replaceable: routes and generation code
depend only on the SearchService interface. Phase 1 ships the lexical
implementation; Phase 4 swaps in a hybrid lexical + vector search without
touching the rest of the application.
"""

from abc import ABC, abstractmethod
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

    Scores a dialogue segment by the fraction of query tokens it contains,
    plus a bonus when the normalized segment text contains the normalized
    query as a phrase. Clips are selected greedily so that chosen segments
    never overlap in time within the same video.
    """

    PHRASE_BONUS = 0.35

    async def search(self, db: AsyncIOMotorDatabase, query: str, limit: int) -> list[ClipMatch]:
        query_norm = normalize_text(query)
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        segments = await dialogue_repo.list_dialogue(db)
        videos = {v.id: v for v in await videos_repo.list_videos(db)}

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
        sentence order, capping total clips and duration via settings.
        """
        all_matches = await self.search(db, " ".join(phrases), limit=max(limit * 2, 20))
        if not all_matches:
            return []

        chosen: list[ClipMatch] = []
        used_spans: dict[str, list[tuple[float, float]]] = {}
        for phrase in phrases:
            candidates = await self.search(db, phrase, limit=5)
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

        if not chosen:
            return all_matches[:limit]
        return chosen[:limit]


def get_search_service() -> SearchService:
    """Factory so a future semantic/hybrid service can be swapped in here."""
    return LexicalSearchService()
