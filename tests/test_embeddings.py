from collections.abc import Sequence

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.conftest import GitRepo, HashEmbedder
from why.embeddings import MAX_PATHS, commit_document, embed_repository
from why.ingest import get_or_create_repository, ingest_repository
from why.models import CommitEmbedding, Repository


def ingested(session: Session, git_repo: GitRepo, commits: int) -> Repository:
    for i in range(commits):
        git_repo.write(f"file{i}.py", f"{i}\n")
        git_repo.commit(f"commit {i}")
    repository = get_or_create_repository(session, git_repo.path)
    ingest_repository(session, repository)
    return repository


def embedded_models(session: Session) -> list[str]:
    return list(session.scalars(select(CommitEmbedding.model).order_by(CommitEmbedding.commit_id)))


def test_embeds_every_commit_once(
    session: Session, git_repo: GitRepo, embedder: HashEmbedder
) -> None:
    repository = ingested(session, git_repo, 5)

    assert embed_repository(session, repository.id, embedder, batch_size=2) == 5
    assert embed_repository(session, repository.id, embedder) == 0
    assert embedded_models(session) == ["test-hash"] * 5


def test_only_new_commits_are_embedded_after_ingest(
    session: Session, git_repo: GitRepo, embedder: HashEmbedder
) -> None:
    repository = ingested(session, git_repo, 2)
    embed_repository(session, repository.id, embedder)

    git_repo.write("new.py", "new\n")
    git_repo.commit("new work")
    ingest_repository(session, repository)

    assert embed_repository(session, repository.id, embedder) == 1


def test_changing_model_re_embeds(session: Session, git_repo: GitRepo) -> None:
    repository = ingested(session, git_repo, 3)
    embed_repository(session, repository.id, HashEmbedder("model-a"))

    assert embed_repository(session, repository.id, HashEmbedder("model-b")) == 3
    assert embedded_models(session) == ["model-b"] * 3
    assert session.scalar(select(func.count()).select_from(CommitEmbedding)) == 3


def test_rejects_vectors_of_the_wrong_size(session: Session, git_repo: GitRepo) -> None:
    class TinyEmbedder(HashEmbedder):
        def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

    repository = ingested(session, git_repo, 1)

    with pytest.raises(ValueError, match="384-dim"):
        embed_repository(session, repository.id, TinyEmbedder())


def test_commit_document_includes_changed_paths() -> None:
    assert commit_document("Fix bug", ["a.py", "b.py"]) == "Fix bug\n\nFiles changed:\na.py\nb.py"
    assert commit_document("No files", []) == "No files"

    many = [f"f{i}.py" for i in range(MAX_PATHS + 5)]
    document = commit_document("Big change", many)
    assert f"f{MAX_PATHS - 1}.py" in document
    assert f"f{MAX_PATHS}.py" not in document
    assert document.endswith("(and 5 more)")
