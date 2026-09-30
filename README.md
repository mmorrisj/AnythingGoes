# Why?

**Why?** answers the question every engineer (and every coding agent) runs into:
*why is this code the way it is?*

Code shows *what* a system does. The *why* is spread across commit messages,
PR threads and review comments that nobody rereads. Why? imports that history into
Postgres and makes it searchable, so you can ask things like "why does the retry
policy cap at three attempts?" and get an answer that cites the commits behind it.

## Status

Early development. Planned stages:

1. ✅ Project setup: FastAPI, SQLAlchemy 2.0, Alembic, Postgres
2. ✅ Commit import: git history → `commits` / `file_changes`, incremental and idempotent
3. ⬜ Embeddings + hybrid search (pgvector + Postgres full-text search)
4. ⬜ `/why?path=…&line=…`: trace a line back through blame and renames
5. ⬜ LLM answers that cite their commits
6. ⬜ Pull requests and review comments from the GitHub API

## Quick start

```bash
cp .env.example .env
docker compose up -d db
uv sync
uv run alembic upgrade head

# Import a repository from the command line…
uv run why ingest /path/to/some/repo

# …or run the API
WHY_REPOS_ROOT=/path/to/your/repos uv run uvicorn why.main:app --reload
```

Or run everything in Docker. Repositories under `./repos` are mounted read-only at `/repos`:

```bash
docker compose up --build
curl -X POST localhost:8000/repositories -H 'content-type: application/json' \
     -d '{"path": "/repos/my-project"}'
curl -X POST localhost:8000/repositories/1/ingest
curl 'localhost:8000/repositories/1/commits?path=src/app.py'
```

Interactive API docs are served at <http://localhost:8000/docs>.

## API

| Method | Path | Description |
| ------ | ---- | ----------- |
| `GET`  | `/health` | Liveness + database check |
| `POST` | `/repositories` | Register a git repository under `WHY_REPOS_ROOT` |
| `GET`  | `/repositories` | List repositories |
| `GET`  | `/repositories/{id}` | Get one repository |
| `POST` | `/repositories/{id}/ingest` | Import new commits since the last ingest |
| `GET`  | `/repositories/{id}/commits?path=` | Commits, newest first; optionally only those touching `path` |
| `GET`  | `/repositories/{id}/commits/{sha}` | One commit with its file changes |

## Configuration

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `WHY_DATABASE_URL` | `postgresql+psycopg://why:why@localhost:5432/why` | SQLAlchemy URL |
| `WHY_REPOS_ROOT` | `/repos` | Only repositories under this directory can be registered through the API |

## Design notes

- **Git parsing** shells out to `git log --raw --numstat -z` and streams the output,
  so there are no native git bindings and large histories don't have to fit in memory.
  NUL-delimited output handles any file name.
- **Ingest is incremental and idempotent.** Each repository remembers the HEAD it last
  imported, and inserts use `ON CONFLICT DO NOTHING`. After a force push, the full
  history is rescanned and only new commits are stored.
- **Merge commits** are stored, but with no file changes (the `git log` default), so
  file history isn't counted twice.
- **Ingest runs inside the request** for now. Moving it to a background worker is a
  natural next step once repositories get large.

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
WHY_TEST_DATABASE_URL=postgresql+psycopg://why:why@localhost:5432/why_test uv run pytest
```

Database tests are skipped if `WHY_TEST_DATABASE_URL` isn't reachable.
