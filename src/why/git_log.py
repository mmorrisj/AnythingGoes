"""Read commit history from a local git repository.

Uses ``git log`` with NUL-delimited ``--raw`` and ``--numstat`` output, so paths
containing spaces, tabs or newlines parse correctly and no git bindings are needed.
"""

import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

RECORD_SEP = b"\x1e"
FIELD_SEP = "\x1f"
# The trailing field separator marks where the message ends and the diff output begins.
LOG_FORMAT = "%x1e%H%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%cI%x1f%B%x1f"
SHA_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
READ_CHUNK = 64 * 1024


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class ParsedFileChange:
    path: str
    change_type: str
    old_path: str | None = None
    additions: int | None = None
    deletions: int | None = None


@dataclass(frozen=True)
class ParsedCommit:
    sha: str
    parent_shas: list[str]
    author_name: str
    author_email: str
    authored_at: datetime
    committed_at: datetime
    message: str
    file_changes: list[ParsedFileChange] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return self.message.split("\n", 1)[0]


def _git(repo: Path, *args: str) -> list[str]:
    return ["git", "-C", str(repo), "-c", "log.showSignature=false", *args]


def is_git_repo(repo: Path) -> bool:
    result = subprocess.run(
        _git(repo, "rev-parse", "--is-inside-work-tree"),
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def resolve_rev(repo: Path, rev: str = "HEAD") -> str | None:
    """Return the commit sha ``rev`` points to, or None if it doesn't exist (e.g. empty repo)."""
    result = subprocess.run(
        _git(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{rev}^{{commit}}"),
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def iter_commits(repo: Path, rev: str = "HEAD", since: str | None = None) -> Iterator[ParsedCommit]:
    """Yield commits reachable from ``rev``, oldest first.

    If ``since`` is given, commits reachable from it are excluded (``since..rev``).
    Merge commits are included but carry no file changes, matching ``git log`` defaults.
    """
    if since is not None and not SHA_RE.match(since):
        raise ValueError(f"since must be a full commit sha, got {since!r}")
    rev_range = f"{since}..{rev}" if since else rev

    cmd = _git(
        repo, "log", "--reverse", f"--format={LOG_FORMAT}",
        "--raw", "--numstat", "-z", "-M", "--no-abbrev", "--end-of-options", rev_range,
    )  # fmt: skip
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        assert proc.stdout is not None and proc.stderr is not None
        buffer = b""
        while chunk := proc.stdout.read(READ_CHUNK):
            buffer += chunk
            *complete, buffer = buffer.split(RECORD_SEP)
            for record in complete:
                if record:
                    yield parse_record(record.decode("utf-8", errors="replace"))
        if buffer:
            yield parse_record(buffer.decode("utf-8", errors="replace"))
        stderr = proc.stderr.read().decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise GitError(f"git log failed ({proc.returncode}): {stderr.strip()}")


def parse_record(record: str) -> ParsedCommit:
    """Parse one commit record (without the leading record separator)."""
    header, _, diff = record.rpartition(FIELD_SEP)
    sha, parents, author_name, author_email, authored, committed, message = header.split(
        FIELD_SEP, 6
    )
    return ParsedCommit(
        sha=sha,
        parent_shas=parents.split(),
        author_name=author_name,
        author_email=author_email,
        authored_at=datetime.fromisoformat(authored),
        committed_at=datetime.fromisoformat(committed),
        message=message.rstrip("\n"),
        file_changes=_parse_diff(diff),
    )


def _parse_diff(diff: str) -> list[ParsedFileChange]:
    tokens = diff.strip("\0\n").split("\0")
    if tokens == [""]:
        return []

    raw: list[tuple[str, str | None, str]] = []  # (path, old_path, change_type)
    stats: dict[str, tuple[int | None, int | None]] = {}
    i = 0
    while i < len(tokens):
        token = tokens[i].lstrip("\n")
        if token.startswith(":"):
            # ":<mode> <mode> <sha> <sha> <status>" followed by one path, or two for R/C.
            status = token.rsplit(" ", 1)[-1]
            change_type = status[0]
            if change_type in "RC":
                raw.append((tokens[i + 2], tokens[i + 1], change_type))
                i += 3
            else:
                raw.append((tokens[i + 1], None, change_type))
                i += 2
        elif token:
            # "<added>\t<deleted>\t<path>", or an empty path followed by old and new for R/C.
            added, deleted, path = token.split("\t", 2)
            if path:
                i += 1
            else:
                path = tokens[i + 2]
                i += 3
            stats[path] = (_count(added), _count(deleted))
        else:
            i += 1

    return [
        ParsedFileChange(
            path=path,
            old_path=old_path,
            change_type=change_type,
            additions=stats.get(path, (None, None))[0],
            deletions=stats.get(path, (None, None))[1],
        )
        for path, old_path, change_type in raw
    ]


def _count(value: str) -> int | None:
    # Binary files are reported as "-".
    return None if value == "-" else int(value)
