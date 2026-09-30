"""Command line entry point: ``why ingest <path>``."""

import argparse
import logging
from pathlib import Path

from why.db import get_sessionmaker
from why.git_log import is_git_repo
from why.ingest import get_or_create_repository, ingest_repository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="why")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest_cmd = commands.add_parser("ingest", help="Import a repository's git history.")
    ingest_cmd.add_argument("path", type=Path)
    ingest_cmd.add_argument("--name", help="Display name (defaults to the directory name).")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not is_git_repo(args.path):
        parser.error(f"{args.path} is not a git repository")
    with get_sessionmaker()() as session:
        repository = get_or_create_repository(session, args.path, args.name)
        result = ingest_repository(session, repository)
    print(
        f"{repository.name}: +{result.commits_added} commits, "
        f"+{result.file_changes_added} file changes (HEAD {result.head_sha})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
