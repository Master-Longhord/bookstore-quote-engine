"""BookMatcher implementation using fuzzy string similarity.

Handles the reality that school lists say "New Gen. Maths SS1" while the
inventory says "New General Mathematics for Senior Secondary 1".

Strategy:
1. Normalize both sides (lowercase, strip punctuation, expand the
   abbreviations Nigerian book lists actually use).
2. Score every inventory title with token_set_ratio (order-insensitive,
   subset-friendly) blended with partial_ratio.
3. Small bonus when the author/publisher also agrees.

Uses rapidfuzz when installed (fast, deployed environment); falls back
to difflib (stdlib) so the test suite runs anywhere.
"""
from __future__ import annotations

import re

from app.domain.interfaces import InventoryRepository
from app.domain.models import ExtractedBook, InventoryItem, MatchCandidate, MatchResult

try:  # pragma: no cover - environment dependent
    from rapidfuzz import fuzz

    def _token_set(a: str, b: str) -> float:
        return float(fuzz.token_set_ratio(a, b))

    def _partial(a: str, b: str) -> float:
        return float(fuzz.partial_ratio(a, b))

except ImportError:  # pragma: no cover
    from difflib import SequenceMatcher

    def _ratio(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio() * 100

    def _token_set(a: str, b: str) -> float:
        ta, tb = set(a.split()), set(b.split())
        if not ta or not tb:
            return 0.0
        inter = " ".join(sorted(ta & tb))
        sa = " ".join(sorted(ta))
        sb = " ".join(sorted(tb))
        return max(_ratio(inter, sa), _ratio(inter, sb), _ratio(sa, sb))

    def _partial(a: str, b: str) -> float:
        short, long_ = (a, b) if len(a) <= len(b) else (b, a)
        if not short:
            return 0.0
        best = 0.0
        words = long_.split()
        n = len(short.split())
        for i in range(max(1, len(words) - n + 1)):
            window = " ".join(words[i : i + n])
            best = max(best, _ratio(short, window))
        return best


# Abbreviations seen constantly on Nigerian school lists.
_EXPANSIONS = {
    r"\bmaths?\b": "mathematics",
    r"\bgen\b": "general",
    r"\beng\b": "english",
    r"\blang\b": "language",
    r"\blit\b": "literature",
    r"\bsci\b": "science",
    r"\bintro\b": "introduction",
    r"\bgovt\b": "government",
    r"\bagric\b": "agricultural science",
    r"\bcomp\b": "computer",
    r"\bbk\b": "book",
    r"\bpry\b": "primary",
    r"\bsec\b": "secondary",
    r"\bss\s*(\d)\b": r"senior secondary \1",
    r"\bjss?\s*(\d)\b": r"junior secondary \1",
    r"\bcrs\b": "christian religious studies",
    r"\birs\b": "islamic religious studies",
    r"\bphe\b": "physical and health education",
    r"\bnig\b": "nigeria",
}

_PUNCT = re.compile(r"[^a-z0-9\s]")
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    t = text.lower()
    t = _PUNCT.sub(" ", t)
    for pat, rep in _EXPANSIONS.items():
        t = re.sub(pat, rep, t)
    return _SPACES.sub(" ", t).strip()


class FuzzyBookMatcher:
    """Depends only on the InventoryRepository port (Dependency Inversion)."""

    def __init__(
        self,
        inventory: InventoryRepository,
        candidate_floor: float = 55.0,
        confident_threshold: float = 88.0,
        max_alternatives: int = 4,
    ):
        self._inventory = inventory
        self._floor = candidate_floor
        self._confident = confident_threshold
        self._max_alt = max_alternatives

    def match(self, book: ExtractedBook) -> MatchResult:
        query = normalize(
            " ".join(filter(None, [book.title, book.grade or ""]))
        )
        query_author = normalize(book.author_or_publisher or "")

        scored: list[MatchCandidate] = []
        for item in self._inventory.all_items():
            score = self._score(query, query_author, item)
            if score >= self._floor:
                scored.append(MatchCandidate(item=item, score=round(score, 1)))

        scored.sort(key=lambda c: c.score, reverse=True)
        
        # Automatically discard 'best' if it falls below a reliable threshold (e.g., 70.0)
        best = scored[0] if scored and scored[0].score >= 70.0 else None
        
        result = MatchResult(
            requested=book,
            best=best,
            alternatives=scored[1 : 1 + self._max_alt] if best else scored[0 : self._max_alt],
        )
        return result
    
    def is_confident(self, result: MatchResult) -> bool:
        return result.best is not None and result.best.score >= self._confident

    # ---- internals ----

    def _score(self, query: str, query_author: str, item: InventoryItem) -> float:
        target = normalize(item.title)
        
        # Guard clause: If the query is a short title (e.g., "Othello"), 
        # require a minimum token match to prevent random high-score collisions.
        query_words = set(query.split())
        target_words = set(target.split())
        
        if len(query_words) <= 2 and not (query_words & target_words):
            return 0.0

        base = 0.6 * _token_set(query, target) + 0.4 * _partial(query, target)
        if query_author:
            author_sim = _token_set(query_author, normalize(item.author_or_publisher))
            base = min(100.0, base + 0.1 * author_sim)
        return base
