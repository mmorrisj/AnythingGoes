"""Hybrid commit search: Postgres full-text search fused with pgvector similarity."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from why.embeddings import Embedder
from why.models import Commit, CommitEmbedding

# Standard Reciprocal Rank Fusion constant (Cormack et al., 2009); damps the top ranks.
RRF_K = 60
# How many candidates each method contributes before fusion.
CANDIDATES = 50


class SearchMode(StrEnum):
    HYBRID = "hybrid"
    KEYWORD = "keyword"
    SEMANTIC = "semantic"


@dataclass(frozen=True)
class SearchHit:
    commit: Commit
    score: float
    keyword_rank: int | None
    semantic_rank: int | None


def search_commits(
    session: Session,
    repository_id: int,
    query: str,
    embedder: Embedder,
    mode: SearchMode = SearchMode.HYBRID,
    limit: int = 20,
) -> list[SearchHit]:
    keyword = (
        keyword_candidates(session, repository_id, query)
        if mode in (SearchMode.HYBRID, SearchMode.KEYWORD)
        else []
    )
    semantic = (
        semantic_candidates(session, repository_id, embedder.embed_query(query), embedder.model)
        if mode in (SearchMode.HYBRID, SearchMode.SEMANTIC)
        else []
    )

    fused = reciprocal_rank_fusion([keyword, semantic])[:limit]
    commits = {
        c.id: c for c in session.scalars(select(Commit).where(Commit.id.in_([i for i, _ in fused])))
    }
    keyword_ranks = {commit_id: rank for rank, commit_id in enumerate(keyword, 1)}
    semantic_ranks = {commit_id: rank for rank, commit_id in enumerate(semantic, 1)}
    return [
        SearchHit(commits[i], score, keyword_ranks.get(i), semantic_ranks.get(i))
        for i, score in fused
    ]


def keyword_candidates(session: Session, repository_id: int, query: str) -> list[int]:
    """Commit ids matching ``query`` by full-text search, best first."""
    # websearch_to_tsquery accepts arbitrary user input ("quoted phrases", -exclusions, or)
    # and never raises a syntax error.
    tsquery = func.websearch_to_tsquery("english", query)
    rank = func.ts_rank_cd(Commit.search_vector, tsquery)
    return list(
        session.scalars(
            select(Commit.id)
            .where(Commit.repository_id == repository_id, Commit.search_vector.op("@@")(tsquery))
            .order_by(rank.desc(), Commit.id.desc())
            .limit(CANDIDATES)
        )
    )


def semantic_candidates(
    session: Session, repository_id: int, query_vector: Sequence[float], model: str
) -> list[int]:
    """Commit ids nearest to ``query_vector`` by cosine distance, best first."""
    distance = CommitEmbedding.embedding.cosine_distance(query_vector)
    return list(
        session.scalars(
            select(CommitEmbedding.commit_id)
            .join(Commit, Commit.id == CommitEmbedding.commit_id)
            .where(Commit.repository_id == repository_id, CommitEmbedding.model == model)
            .order_by(distance, CommitEmbedding.commit_id.desc())
            .limit(CANDIDATES)
        )
    )


def reciprocal_rank_fusion(rankings: Sequence[Sequence[int]]) -> list[tuple[int, float]]:
    """Merge ranked id lists: each list adds 1 / (RRF_K + rank) to an id's score.

    Rank-based fusion avoids having to normalise ts_rank against cosine distance,
    which live on unrelated scales.
    """
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, 1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(scores.items(), key=lambda pair: (-pair[1], -pair[0]))
