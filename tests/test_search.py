from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from tests.conftest import GitRepo, HashEmbedder
from why.embeddings import embed_repository
from why.ingest import get_or_create_repository, ingest_repository
from why.models import Repository
from why.search import RRF_K, SearchMode, reciprocal_rank_fusion, search_commits

HISTORY = [
    ("client/http.py", "Add retry logic to HTTP client"),
    ("client/http.py", "Cap retries at three attempts to avoid a thundering herd"),
    ("README.md", "Update README badges"),
    ("db/pool.py", "Refactor database pool configuration"),
]


@pytest.fixture
def repository(session: Session, git_repo: GitRepo, embedder: HashEmbedder) -> Repository:
    for i, (path, message) in enumerate(HISTORY):
        git_repo.write(path, f"version {i}\n")
        git_repo.commit(message)
    repository = get_or_create_repository(session, git_repo.path)
    ingest_repository(session, repository)
    embed_repository(session, repository.id, embedder)
    return repository


def summaries(hits: list) -> list[str]:  # type: ignore[type-arg]
    return [hit.commit.summary for hit in hits]


def test_keyword_search_uses_stemming(
    session: Session, repository: Repository, embedder: HashEmbedder
) -> None:
    hits = search_commits(session, repository.id, "retry", embedder, mode=SearchMode.KEYWORD)

    # "retry" and "retries" share a stem.
    assert set(summaries(hits)) == {HISTORY[0][1], HISTORY[1][1]}
    assert all(hit.semantic_rank is None for hit in hits)


def test_semantic_search_ranks_by_similarity(
    session: Session, repository: Repository, embedder: HashEmbedder
) -> None:
    hits = search_commits(
        session, repository.id, "thundering herd", embedder, mode=SearchMode.SEMANTIC
    )

    assert summaries(hits)[0] == HISTORY[1][1]
    assert hits[0].keyword_rank is None
    assert hits[0].semantic_rank == 1


def test_hybrid_search_prefers_commits_found_by_both(
    session: Session, repository: Repository, embedder: HashEmbedder
) -> None:
    hits = search_commits(session, repository.id, "retry attempts", embedder)

    top = hits[0]
    assert top.commit.summary == HISTORY[1][1]
    assert top.keyword_rank == 1
    assert top.semantic_rank is not None
    assert [hit.score for hit in hits] == sorted((hit.score for hit in hits), reverse=True)


def test_hybrid_falls_back_to_keywords_without_embeddings(
    session: Session, git_repo: GitRepo, embedder: HashEmbedder
) -> None:
    git_repo.write("a.py", "a\n")
    git_repo.commit("Introduce caching layer")
    repository = get_or_create_repository(session, git_repo.path)
    ingest_repository(session, repository)

    assert search_commits(session, repository.id, "caching", embedder, SearchMode.SEMANTIC) == []
    assert summaries(search_commits(session, repository.id, "caching", embedder)) == [
        "Introduce caching layer"
    ]


def test_ignores_embeddings_from_other_models(session: Session, repository: Repository) -> None:
    other = HashEmbedder("another-model")

    assert search_commits(session, repository.id, "herd", other, SearchMode.SEMANTIC) == []


def test_results_are_scoped_to_the_repository(
    session: Session, repository: Repository, embedder: HashEmbedder, tmp_path: Path
) -> None:
    other_repo = GitRepo(tmp_path / "repos" / "other")
    other_repo.write("x.py", "x\n")
    other_repo.commit("Add retry logic somewhere else")
    other = get_or_create_repository(session, other_repo.path)
    ingest_repository(session, other)
    embed_repository(session, other.id, embedder)

    hits = search_commits(session, repository.id, "retry logic", embedder)

    assert "Add retry logic somewhere else" not in summaries(hits)
    assert HISTORY[0][1] in summaries(hits)


@pytest.mark.parametrize("query", ["'); DROP TABLE commits; --", '"unclosed', "-", "&|!"])
def test_hostile_queries_do_not_error(
    session: Session, repository: Repository, embedder: HashEmbedder, query: str
) -> None:
    assert isinstance(search_commits(session, repository.id, query, embedder), list)


def test_limit(session: Session, repository: Repository, embedder: HashEmbedder) -> None:
    hits = search_commits(session, repository.id, "update", embedder, SearchMode.SEMANTIC, limit=2)

    assert len(hits) == 2


def test_reciprocal_rank_fusion() -> None:
    fused = dict(reciprocal_rank_fusion([[1, 2, 3], [3, 4]]))

    assert fused[3] == pytest.approx(1 / (RRF_K + 3) + 1 / (RRF_K + 1))
    assert fused[1] == pytest.approx(1 / (RRF_K + 1))
    assert max(fused, key=fused.__getitem__) == 3
    assert reciprocal_rank_fusion([[], []]) == []
