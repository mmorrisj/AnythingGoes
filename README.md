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
3. ✅ Embeddings + hybrid search (pgvector + Postgres full-text search)
4. ⬜ `/why?path=…&line=…`: trace a line back through blame and renames
5. ⬜ LLM answers that cite their commits
6. ⬜ Pull requests and review comments from the GitHub API

## Quick start

```bash
cp .env.example .env
docker compose up -d db
uv sync
uv run alembic upgrade head

# Import a repository from the command line, embed it, and search it…
uv run why ingest --embed /path/to/some/repo
uv run why search /path/to/some/repo "why do retries stop after three attempts"

# …or run the API
WHY_REPOS_ROOT=/path/to/your/repos uv run uvicorn why.main:app --reload
```

Or run everything in Docker. Repositories under `./repos` are mounted read-only at `/repos`:

```bash
docker compose up --build
curl -X POST localhost:8000/repositories -H 'content-type: application/json' \
     -d '{"path": "/repos/my-project"}'
curl -X POST localhost:8000/repositories/1/ingest
curl -X POST localhost:8000/repositories/1/embed
curl 'localhost:8000/repositories/1/search?q=rate+limiting'
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
| `POST` | `/repositories/{id}/embed` | Embed commits not yet embedded with the current model |
| `GET`  | `/repositories/{id}/search?q=&mode=&limit=` | Search commits; `mode` is `hybrid` (default), `keyword` or `semantic` |
| `GET`  | `/repositories/{id}/commits?path=` | Commits, newest first; optionally only those touching `path` |
| `GET`  | `/repositories/{id}/commits/{sha}` | One commit with its file changes |

## Configuration

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `WHY_DATABASE_URL` | `postgresql+psycopg://why:why@localhost:5432/why` | SQLAlchemy URL |
| `WHY_REPOS_ROOT` | `/repos` | Only repositories under this directory can be registered through the API |
| `WHY_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | [fastembed](https://github.com/qdrant/fastembed) model; must produce 384-dim vectors |
| `WHY_EMBEDDING_CACHE_DIR` | fastembed default (a temp dir) | Where model weights are cached |

## Design notes

- **Git parsing** shells out to `git log --raw --numstat -z` and streams the output,
  so there are no native git bindings and large histories don't have to fit in memory.
  NUL-delimited output handles any file name.
- **Ingest is incremental and idempotent.** Each repository remembers the HEAD it last
  imported, and inserts use `ON CONFLICT DO NOTHING`. After a force push, the full
  history is rescanned and only new commits are stored.
- **Search is hybrid.** Keyword search uses a generated `tsvector` column (English
  stemming, GIN index, `websearch_to_tsquery` so any user input is safe). Semantic search
  uses pgvector cosine distance over an HNSW index. The two ranked lists are merged with
  Reciprocal Rank Fusion, which avoids normalising scores that live on unrelated scales.
  Each result reports its rank in both lists, so you can see why it matched.
- **What gets embedded** is the commit message plus the paths it touched, so a query like
  "auth middleware" can match a commit whose message only says "fix token expiry".
- **Embeddings run locally** (ONNX via fastembed, weights downloaded on first use), so no
  API key is needed. Each row records which model produced it; changing
  `WHY_EMBEDDING_MODEL` makes the next `embed` run re-embed everything. Embedding is a
  separate step from ingest and commits per batch, so it can be interrupted and resumed.
  On CPU it is the slow part: about 33 commits/s on 4 cores (7,730 FastAPI commits in
  ~4 minutes). Texts are embedded in length order to avoid padding costs.
- **pgvector must be available.** Migration `0002` runs `CREATE EXTENSION vector`, which
  needs a role allowed to create extensions (the `pgvector/pgvector` image's default
  user is fine; on managed Postgres, enable the extension first).
- **Merge commits** are stored, but with no file changes (the `git log` default), so
  file history isn't counted twice.
- **Ingest and embed run inside the request** for now. That's fine for small repos, but
  embedding a large history takes minutes, so moving both to a background worker is the
  next step before exposing this beyond local use. The CLI isn't affected.

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
WHY_TEST_DATABASE_URL=postgresql+psycopg://why:why@localhost:5432/why_test uv run pytest
```

Database tests are skipped if `WHY_TEST_DATABASE_URL` isn't reachable.
