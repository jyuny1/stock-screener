"""Hybrid IBD industry-group classifier.

Assigns an IBD industry group to every active universe symbol that lacks an
authoritative one, using a free-first cascade that mirrors the theme matcher:

1. **skip** symbols already carrying an authoritative group (``csv``/``manual``);
2. **crosswalk** — deterministic GICS/sector → IBD group (free, high confidence);
3. **embedding** — local ``all-MiniLM-L6-v2`` nearest group centroid (free);
4. **llm** — closed-set shortlist tiebreaker for the residual (paid, optional).

All markets classify into the *same* canonical IBD taxonomy (the groups the
curated US CSV defines) so ``IBDGroupRankService`` works unchanged and groups are
cross-market comparable. The embedding engine and LLM client are injected so the
cascade is unit-testable without loading models or calling APIs.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional, Protocol

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    import numpy as np

from ..models.industry import IBDIndustryGroup
from ..models.stock import StockIndustry
from ..models.stock_universe import StockUniverse
from .ibd_crosswalk import IBDCrosswalk
from .ibd_industry_service import IBDIndustryService

logger = logging.getLogger(__name__)

# Source tags persisted on each assignment (see IBDIndustryGroup docstring).
SOURCE_CROSSWALK = "crosswalk"
SOURCE_EMBEDDING = "embedding"
SOURCE_LLM = "llm"


@dataclass
class StockContext:
    symbol: str
    market: str
    name: str = ""
    sector: str = ""
    industry: str = ""
    sub_industry: str = ""

    def text(self) -> str:
        """Descriptive text used for embedding / LLM input."""
        parts = [self.name, self.sector, self.industry, self.sub_industry]
        return " ".join(p for p in (s.strip() for s in parts) if p)


@dataclass
class Assignment:
    symbol: str
    market: str
    industry_group: str
    source: str
    confidence: Optional[float]
    method: str
    model_id: Optional[str] = None


@dataclass
class ClassificationResult:
    market: str
    assignments: list[Assignment] = field(default_factory=list)
    skipped_authoritative: int = 0
    unresolved: list[str] = field(default_factory=list)
    candidates: int = 0

    def summary(self) -> dict:
        by_method: dict[str, int] = {}
        for a in self.assignments:
            by_method[a.source] = by_method.get(a.source, 0) + 1
        total_active = self.candidates + self.skipped_authoritative
        classified = len(self.assignments) + self.skipped_authoritative
        return {
            "market": self.market,
            "active_total": total_active,
            "candidates": self.candidates,
            "skipped_authoritative": self.skipped_authoritative,
            "newly_classified": len(self.assignments),
            "unresolved": len(self.unresolved),
            "by_source": by_method,
            "coverage_pct": round(100.0 * classified / total_active, 2) if total_active else 0.0,
        }


class EmbeddingEngine(Protocol):
    def encode(self, text: str): ...  # returns vector or None

    @staticmethod
    def cosine_similarity(left, right) -> float: ...


# LLM tiebreaker contract: given a stock's text and a shortlist of candidate
# group names, return the chosen group (must be one of the shortlist) or None.
LLMTiebreaker = Callable[[str, list[str]], Optional[str]]


def _clean_group_name_for_embedding(group: str) -> str:
    """Turn an IBD group code into descriptive text, e.g.
    'Oil&Gas-Refining/Mktg' -> 'Oil Gas Refining Mktg'."""
    return re.sub(r"[-/&]+", " ", group).strip()


class IBDClassificationService:
    """Classify universe symbols into the canonical IBD taxonomy."""

    # Embedding cosine above which we auto-attach without the LLM.
    EMBEDDING_ATTACH_THRESHOLD = 0.55
    # Number of nearest group centroids handed to the LLM tiebreaker.
    LLM_SHORTLIST_SIZE = 6
    # Representative member texts blended into each group centroid (when present).
    MAX_MEMBERS_PER_CENTROID = 25

    def __init__(
        self,
        *,
        crosswalk: IBDCrosswalk | None,
        embedding_engine: EmbeddingEngine | None = None,
        llm_tiebreaker: LLMTiebreaker | None = None,
        llm_model_id: str | None = None,
        group_taxonomy: list[str] | None = None,
        attach_threshold: float | None = None,
    ):
        self.crosswalk = crosswalk
        self.engine = embedding_engine
        self.llm_tiebreaker = llm_tiebreaker
        self.llm_model_id = llm_model_id
        self._taxonomy = group_taxonomy
        self.attach_threshold = (
            attach_threshold if attach_threshold is not None else self.EMBEDDING_ATTACH_THRESHOLD
        )
        self._centroids: dict[str, "np.ndarray"] | None = None

    # ---- taxonomy & centroids ------------------------------------------------

    def canonical_taxonomy(self, db: Session) -> list[str]:
        """The canonical IBD group list (distinct US CSV/manual groups)."""
        if self._taxonomy is None:
            rows = (
                db.query(IBDIndustryGroup.industry_group)
                .filter(IBDIndustryGroup.industry_group.isnot(None))
                .distinct()
                .all()
            )
            self._taxonomy = sorted({r.industry_group for r in rows if r.industry_group})
        return self._taxonomy

    def _build_centroids(self, db: Session, taxonomy: list[str]) -> dict[str, "np.ndarray"]:
        """Centroid embedding per group: the cleaned group name, blended with a
        sample of labelled members' text when those rows are available locally."""
        if self.engine is None:
            return {}
        import numpy as np

        # Gather sample member texts per group (only works where the labelled
        # universe is loaded; degrades to name-only otherwise).
        member_texts: dict[str, list[str]] = {g: [] for g in taxonomy}
        rows = (
            db.query(
                IBDIndustryGroup.industry_group,
                StockUniverse.name,
                StockUniverse.sector,
                StockUniverse.industry,
            )
            .join(StockUniverse, StockUniverse.symbol == IBDIndustryGroup.symbol)
            .filter(IBDIndustryGroup.source.in_(IBDIndustryService.AUTHORITATIVE_SOURCES))
            .all()
        )
        for group, name, sector, industry in rows:
            bucket = member_texts.get(group)
            if bucket is None or len(bucket) >= self.MAX_MEMBERS_PER_CENTROID:
                continue
            txt = " ".join(p for p in (name, sector, industry) if p)
            if txt:
                bucket.append(txt)

        centroids: dict[str, object] = {}
        for group in taxonomy:
            texts = [_clean_group_name_for_embedding(group)] + member_texts.get(group, [])
            vectors = [v for v in (self.engine.encode(t) for t in texts) if v is not None]
            if not vectors:
                continue
            # Mean of unit vectors → centroid direction.
            centroids[group] = np.mean(np.stack(vectors), axis=0)
        return centroids

    def _centroids_for(self, db: Session, taxonomy: list[str]) -> dict[str, "np.ndarray"]:
        if self._centroids is None:
            self._centroids = self._build_centroids(db, taxonomy)
        return self._centroids

    # ---- candidate selection -------------------------------------------------

    def _authoritative_symbols(self, db: Session) -> set[str]:
        rows = (
            db.query(IBDIndustryGroup.symbol)
            .filter(IBDIndustryGroup.source.in_(IBDIndustryService.AUTHORITATIVE_SOURCES))
            .all()
        )
        return {r.symbol for r in rows}

    def _candidate_contexts(self, db: Session, market: str) -> tuple[list[StockContext], int]:
        """Return ``(contexts_to_classify, skipped_authoritative_count)`` from a
        single active-universe fetch, so the skip count can never drift from the
        skip logic that produced it."""
        authoritative = self._authoritative_symbols(db)
        universe = (
            db.query(StockUniverse)
            .filter(StockUniverse.market == market, StockUniverse.is_active.is_(True))
            .all()
        )
        symbols = [u.symbol for u in universe]
        sub_by_symbol: dict[str, str] = {}
        for start in range(0, len(symbols), 500):
            chunk = symbols[start:start + 500]
            for sym, sub in (
                db.query(StockIndustry.symbol, StockIndustry.sub_industry)
                .filter(StockIndustry.symbol.in_(chunk))
                .all()
            ):
                if sub:
                    sub_by_symbol[sym] = sub

        contexts: list[StockContext] = []
        skipped = 0
        for u in universe:
            if u.symbol in authoritative:
                skipped += 1
                continue
            contexts.append(StockContext(
                symbol=u.symbol,
                market=market,
                name=u.name or "",
                sector=u.sector or "",
                industry=u.industry or "",
                sub_industry=sub_by_symbol.get(u.symbol, ""),
            ))
        return contexts, skipped

    # ---- the cascade ---------------------------------------------------------

    def _embedding_ranking(self, ctx: StockContext, centroids: dict[str, "np.ndarray"]) -> list[tuple[str, float]]:
        if self.engine is None or not centroids:
            return []
        vec = self.engine.encode(ctx.text())
        if vec is None:
            return []
        scored = [
            (group, self.engine.cosine_similarity(vec, centroid))
            for group, centroid in centroids.items()
        ]
        scored.sort(key=lambda gs: (-gs[1], gs[0]))
        return scored

    def classify_one(self, ctx: StockContext, centroids: dict[str, "np.ndarray"]) -> Optional[Assignment]:
        # Tier 2: deterministic crosswalk.
        if self.crosswalk is not None:
            hit = self.crosswalk.lookup(
                sub_industry=ctx.sub_industry, sector=ctx.sector, industry=ctx.industry
            )
            if hit is not None:
                return Assignment(
                    symbol=ctx.symbol, market=ctx.market, industry_group=hit.group,
                    source=SOURCE_CROSSWALK, confidence=hit.confidence, method=hit.method,
                )

        # Tier 3/4: rank against group centroids; attach the nearest if confident,
        # otherwise let the LLM tiebreaker pick from that shortlist.
        ranking = self._embedding_ranking(ctx, centroids)
        if not ranking:
            return None

        best_group, best_score = ranking[0]
        if best_score >= self.attach_threshold:
            return Assignment(
                symbol=ctx.symbol, market=ctx.market, industry_group=best_group,
                source=SOURCE_EMBEDDING, confidence=round(float(best_score), 4),
                method="centroid_nn",
            )

        if self.llm_tiebreaker is not None:
            shortlist = [g for g, _ in ranking[: self.LLM_SHORTLIST_SIZE]]
            chosen = self.llm_tiebreaker(ctx.text(), shortlist)
            if chosen and chosen in shortlist:
                return Assignment(
                    symbol=ctx.symbol, market=ctx.market, industry_group=chosen,
                    source=SOURCE_LLM, confidence=None, method="llm_shortlist",
                    model_id=self.llm_model_id,
                )
        return None

    def classify_market(self, db: Session, market: str) -> ClassificationResult:
        market = (market or "US").upper()
        taxonomy = self.canonical_taxonomy(db)
        if not taxonomy:
            logger.warning("No canonical IBD taxonomy available; load the CSV first")
        centroids = self._centroids_for(db, taxonomy)

        contexts, skipped_authoritative = self._candidate_contexts(db, market)
        result = ClassificationResult(
            market=market, candidates=len(contexts), skipped_authoritative=skipped_authoritative
        )
        for ctx in contexts:
            assignment = self.classify_one(ctx, centroids)
            if assignment is not None:
                result.assignments.append(assignment)
            else:
                result.unresolved.append(ctx.symbol)

        logger.info("IBD classification %s: %s", market, result.summary())
        return result
