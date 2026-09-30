from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.conftest import GitRepo
from why.ingest import get_or_create_repository, ingest_repository
from why.models import Commit, FileChange


def count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_ingest_stores_history(session: Session, git_repo: GitRepo) -> None:
    git_repo.write("a.txt", "a\n")
    git_repo.commit("add a")
    git_repo.write("b.txt", "b\n")
    head = git_repo.commit("add b")
    repository = get_or_create_repository(session, git_repo.path)

    result = ingest_repository(session, repository)

    assert (result.commits_added, result.file_changes_added, result.head_sha) == (2, 2, head)
    assert repository.head_sha == head
    assert repository.last_ingested_at is not None
    assert count(session, Commit) == 2
    assert count(session, FileChange) == 2


def test_ingest_is_incremental_and_idempotent(session: Session, git_repo: GitRepo) -> None:
    git_repo.write("a.txt", "a\n")
    git_repo.commit("add a")
    repository = get_or_create_repository(session, git_repo.path)
    ingest_repository(session, repository)

    assert ingest_repository(session, repository).commits_added == 0

    git_repo.write("a.txt", "aa\n")
    git_repo.commit("change a")
    assert ingest_repository(session, repository).commits_added == 1
    assert count(session, Commit) == 2


def test_ingest_recovers_when_previous_head_is_gone(session: Session, git_repo: GitRepo) -> None:
    git_repo.write("a.txt", "a\n")
    git_repo.commit("keep")
    git_repo.write("a.txt", "b\n")
    git_repo.commit("will be rewritten")
    repository = get_or_create_repository(session, git_repo.path)
    ingest_repository(session, repository)

    # Simulate a force push: rewrite the tip and garbage-collect the old commit.
    git_repo.git("reset", "-q", "--hard", "HEAD~1")
    git_repo.write("a.txt", "c\n")
    new_head = git_repo.commit("replacement")
    git_repo.git("reflog", "expire", "--expire=now", "--all")
    git_repo.git("gc", "-q", "--prune=now")

    result = ingest_repository(session, repository)

    assert result.commits_added == 1
    assert repository.head_sha == new_head


def test_ingest_empty_repository(session: Session, git_repo: GitRepo) -> None:
    repository = get_or_create_repository(session, git_repo.path)

    result = ingest_repository(session, repository)

    assert result.commits_added == 0
    assert result.head_sha is None


def test_get_or_create_repository_reuses_existing(session: Session, git_repo: GitRepo) -> None:
    first = get_or_create_repository(session, git_repo.path, name="custom")
    second = get_or_create_repository(session, git_repo.path)

    assert first.id == second.id
    assert second.name == "custom"
