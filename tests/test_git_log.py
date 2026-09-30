from datetime import UTC, datetime

import pytest

from tests.conftest import GitRepo
from why import git_log
from why.git_log import ParsedFileChange, is_git_repo, iter_commits, resolve_rev


def test_parses_commit_metadata(git_repo: GitRepo) -> None:
    git_repo.write("a.txt", "one\n")
    sha = git_repo.commit("Add a\n\nExplains why a exists.")

    [commit] = list(iter_commits(git_repo.path))

    assert commit.sha == sha
    assert commit.parent_shas == []
    assert commit.author_name == "Ada Lovelace"
    assert commit.author_email == "ada@example.com"
    assert commit.authored_at == datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC)
    assert commit.summary == "Add a"
    assert commit.message == "Add a\n\nExplains why a exists."
    assert commit.file_changes == [ParsedFileChange("a.txt", "A", None, 1, 0)]


def test_parses_all_change_types(git_repo: GitRepo) -> None:
    git_repo.write("keep.txt", "1\n2\n3\n")
    git_repo.write("move.txt", "line\n" * 20)
    git_repo.write("gone.txt", "bye\n")
    git_repo.write("image.bin", b"\x00\x01\x02")
    first = git_repo.commit("initial")
    git_repo.write("keep.txt", "1\nTWO\n3\n")
    git_repo.git("mv", "move.txt", "moved.txt")
    git_repo.git("rm", "-q", "gone.txt")
    git_repo.write("image.bin", b"\x00\x09")
    git_repo.commit("change things")

    initial, second = iter_commits(git_repo.path)

    assert initial.sha == first
    assert ParsedFileChange("image.bin", "A", None, None, None) in initial.file_changes
    assert sorted(second.file_changes, key=lambda c: c.path) == [
        ParsedFileChange("gone.txt", "D", None, 0, 1),
        ParsedFileChange("image.bin", "M", None, None, None),
        ParsedFileChange("keep.txt", "M", None, 1, 1),
        ParsedFileChange("moved.txt", "R", "move.txt", 0, 0),
    ]
    assert second.parent_shas == [first]


def test_handles_awkward_file_names(git_repo: GitRepo) -> None:
    names = ["with space.txt", "tab\there.txt", "new\nline.txt", "ünïcode.txt"]
    for name in names:
        git_repo.write(name, "x\n")
    git_repo.commit("weird names")

    [commit] = list(iter_commits(git_repo.path))

    assert sorted(c.path for c in commit.file_changes) == sorted(names)


def test_merge_commits_have_parents_but_no_file_changes(git_repo: GitRepo) -> None:
    git_repo.write("base.txt", "base\n")
    git_repo.commit("base")
    git_repo.git("checkout", "-q", "-b", "feature")
    git_repo.write("feature.txt", "f\n")
    git_repo.commit("feature")
    git_repo.git("checkout", "-q", "main")
    git_repo.write("main.txt", "m\n")
    git_repo.commit("main work")
    git_repo.git("merge", "-q", "--no-edit", "--no-ff", "feature")

    merge = list(iter_commits(git_repo.path))[-1]

    assert len(merge.parent_shas) == 2
    assert merge.file_changes == []


def test_empty_commit_has_no_file_changes(git_repo: GitRepo) -> None:
    git_repo.write("a.txt", "a\n")
    git_repo.commit("a")
    git_repo.commit("nothing", "--allow-empty")

    assert list(iter_commits(git_repo.path))[-1].file_changes == []


def test_since_excludes_already_seen_commits(git_repo: GitRepo) -> None:
    git_repo.write("a.txt", "1\n")
    first = git_repo.commit("first")
    git_repo.write("a.txt", "2\n")
    second = git_repo.commit("second")

    assert [c.sha for c in iter_commits(git_repo.path, since=first)] == [second]
    assert list(iter_commits(git_repo.path, since=second)) == []


def test_since_must_be_a_full_sha(git_repo: GitRepo) -> None:
    with pytest.raises(ValueError):
        list(iter_commits(git_repo.path, since="--output=/tmp/x"))


def test_streams_across_read_chunks(git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(git_log, "READ_CHUNK", 7)
    for i in range(5):
        git_repo.write(f"f{i}.txt", f"{i}\n")
        git_repo.commit(f"commit {i}")

    assert [c.summary for c in iter_commits(git_repo.path)] == [f"commit {i}" for i in range(5)]


def test_repo_helpers(git_repo: GitRepo, tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert is_git_repo(git_repo.path)
    assert not is_git_repo(tmp_path / "missing")
    assert resolve_rev(git_repo.path) is None  # no commits yet

    git_repo.write("a.txt", "a\n")
    sha = git_repo.commit("a")
    assert resolve_rev(git_repo.path) == sha
    assert resolve_rev(git_repo.path, "0" * 40) is None
