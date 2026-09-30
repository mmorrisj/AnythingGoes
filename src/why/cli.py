"""Command line entry point: ``why ingest|embed|search``."""

import argparse
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from why.db import get_sessionmaker
from why.embeddings import embed_repository, get_embedder
from why.git_log import is_git_repo
from why.ingest import get_or_create_repository, ingest_repository
from why.search import SearchMode, search_commits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="why")
    commands = parser.add_subparsers(dest="command", required=True)

    ingest_cmd = commands.add_parser("ingest", help="Import a repository's git history.")
    ingest_cmd.add_argument("path", type=Path)
    ingest_cmd.add_argument("--name", help="Display name (defaults to the directory name).")
    ingest_cmd.add_argument("--embed", action="store_true", help="Also embed new commits.")

    embed_cmd = commands.add_parser("embed", help="Embed commits for semantic search.")
    embed_cmd.add_argument("path", type=Path)

    search_cmd = commands.add_parser("search", help="Search a repository's commits.")
    search_cmd.add_argument("path", type=Path)
    search_cmd.add_argument("query")
    search_cmd.add_argument("--mode", type=SearchMode, choices=list(SearchMode), default="hybrid")
    search_cmd.add_argument("--limit", type=int, default=10)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not is_git_repo(args.path):
        parser.error(f"{args.path} is not a git repository")
    with get_sessionmaker()() as session:
        if args.command == "ingest":
            return _ingest(session, args.path, args.name, args.embed)
        if args.command == "embed":
            return _embed(session, args.path)
        return _search(session, args.path, args.query, args.mode, args.limit)


def _ingest(session: Session, path: Path, name: str | None, embed: bool) -> int:
    repository = get_or_create_repository(session, path, name)
    result = ingest_repository(session, repository)
    print(
        f"{repository.name}: +{result.commits_added} commits, "
        f"+{result.file_changes_added} file changes (HEAD {result.head_sha})"
    )
    if embed:
        return _embed(session, path)
    return 0


def _embed(session: Session, path: Path) -> int:
    repository = get_or_create_repository(session, path)
    embedder = get_embedder()
    count = embed_repository(session, repository.id, embedder)
    print(f"{repository.name}: embedded {count} commits with {embedder.model}")
    return 0


def _search(session: Session, path: Path, query: str, mode: SearchMode, limit: int) -> int:
    repository = get_or_create_repository(session, path)
    hits = search_commits(session, repository.id, query, get_embedder(), mode=mode, limit=limit)
    for hit in hits:
        commit = hit.commit
        print(f"{commit.sha[:10]}  {commit.committed_at:%Y-%m-%d}  {commit.summary}")
    if not hits:
        print("No matching commits.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
