# Repository Guidelines

## Project Structure & Module Organization

This is a FastAPI + PostgreSQL application for Amazon search term analysis and large CSV imports. `main.py` creates the app and mounts routers. Core application code lives in `app/`: `auth/` handles JWT and login flows, `user/` handles user management, and `table/` contains `analysis/`, `search/`, and `upload/` features. Feature modules generally follow `*_model.py`, `*_crud.py`, `*_service.py`, `*_api.py`, and `*_schemas.py` layers. Shared configuration and database setup are in `config.py` and `database.py`. Tests are in `test/`. Static CSS/images are in `static/`, SQL and architecture notes are in `docs/`, and deployment assets are in `deploy.sh`, `start.sh`, and `etc/`.

## Build, Test, and Development Commands

This project is managed with [uv](https://docs.astral.sh/uv/). Install dependencies (creates `.venv` automatically, pinned to the version in `.python-version`):

```bash
uv sync
```

Run the development server:

```bash
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Run the test suite:

```bash
uv run pytest test/
```

Add or update a dependency:

```bash
uv add <package>          # runtime dependency
uv add --dev <package>    # dev-only dependency (test/, tooling)
```

Always commit `uv.lock` alongside `pyproject.toml` changes.

For production-style startup, use `./start.sh` (host uvicorn, 4 workers). Use `./deploy.sh` to publish a release (uv sync → alembic migrate → build SPA → rsync to nginx root → reload nginx). Deployment is host-based nginx + uvicorn — nginx serves the built SPA from `frontend/dist/` and proxies `/api` to the local backend; see `etc/keywordiq-nginx.conf`. There is no container/compose deployment.

## Coding Style & Naming Conventions

Use Python 3.11+ idioms, 4-space indentation, and type hints where they clarify API or service boundaries. Keep API modules thin; put business logic in services and data access in CRUD modules. Use SQLAlchemy 2.0 `select()` style, not legacy `.query()`. Pydantic schemas should use v2 patterns such as `ConfigDict` and `field_validator`. Match existing file naming patterns: `analysis_api.py`, `upload_service.py`, `user_schemas.py`.

## Technical Constraints

New code must follow SQLAlchemy 2.0 and Pydantic 2.0 conventions. For SQLAlchemy, use `DeclarativeBase`, `Mapped`, `mapped_column`, explicit `select()` statements, and `Session.execute(...).scalars()` patterns; do not introduce `declarative_base()`, `Column(...)` model fields, or `.query()` APIs. For Pydantic, use `ConfigDict`, `field_validator`, `model_validate()`, and `model_dump()`; do not add v1-style `class Config`, `@validator`, `@root_validator`, `.dict()`, or `.parse_obj()` usage. Keep ORM models, request schemas, and response schemas typed precisely.

Schema changes must be handled through Alembic migrations, not ad hoc SQL against application databases. When changing ORM models, add or update the matching migration and verify it targets the configured `analysis` schema correctly. Keep transaction ownership clear: CRUD helpers should avoid surprising commits unless that is the established behavior for the specific module; service/API layers should define the commit/rollback boundary. Large imports must avoid long transactions and must not reuse parent-process database connections inside worker processes.

Keep sync and async boundaries explicit. Do not run long CPU-bound CSV parsing, blocking database work, or full-file reads directly on the FastAPI event loop. Use the existing chunked processing, process pool, or background workflow patterns for large imports, and keep request handlers thin.

For data-heavy paths, design for bounded memory and bounded result sets. Do not load entire GB-scale CSV files into memory, do not return unpaginated table-sized query results, and consider indexes and query plans when adding filters or joins used by search/analysis screens.

## Testing Guidelines

Tests currently use `unittest` classes and are run with `pytest`. Name files `test/test_*.py` and test methods `test_*`. Database tests expect valid PostgreSQL settings, including sync and async URLs, so prepare `.env` from `.env.example` before running integration tests. Keep tests focused on observable behavior: configuration loading, database connectivity, auth middleware, and import processing.

Separate unit tests from integration tests in practice: unit tests should not require a live PostgreSQL instance, while database/import tests may require `.env` and should use small fixtures rather than real production CSV files. New tests must be collectable by `pytest` and must not reference deleted or experimental module paths. Bug fixes should include a focused regression test when the behavior can be reproduced locally.

## Commit & Pull Request Guidelines

Recent history uses short conventional prefixes, for example `fix: ...`, `chore: ...`, and `merge: ...`. Keep commit subjects imperative and specific, and use Chinese or English consistently within a change set. Pull requests should describe the problem, summarize the fix, list test commands run, and call out database, import-performance, or configuration changes. Include screenshots only for visible admin/UI changes.

## Security & Configuration Tips

Never commit real secrets or production database URLs. Copy `.env.example` to `.env` locally and change `ADMIN_SECRET_KEY`. Keep PostgreSQL connection pool settings below the RDS connection limit, especially when increasing Uvicorn workers or CSV multiprocessing settings. Keep `.env.example` in sync when adding new settings, including units and safe local defaults. Do not commit `uploads/`, `logs/`, temporary split files, or real customer/search-report CSV data.
