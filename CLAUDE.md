# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Amazon Search Analysis System - A professional Amazon search term data analysis platform supporting GB-level file processing, real-time data analysis, and visualization. Built with FastAPI + PostgreSQL, serving ~200 concurrent users for read-heavy operations with periodic batch data imports.

**Import data volumes (critical for performance decisions):**
- Daily report: ~300K rows, ~150MB CSV, imported once per day
- Weekly report: ~3M rows, ~1.5GB CSV, imported once per week
- The 10× size difference means daily and weekly imports have fundamentally different performance characteristics and must be treated as separate problem domains

## Development Commands

```bash
# Local development
source .venv/bin/activate
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Run tests
python -m pytest test/

# Docker deployment
docker-compose up -d
docker-compose logs -f app | grep upload  # Monitor upload logs
docker-compose restart app  # Restart app container

# Database operations
# Uses PostgreSQL with schema "analysis"
# Connection: sync (psycopg2) + async (asyncpg) engines both configured
```

## Architecture

### Module Structure

```
app/
├── auth/              # JWT authentication + middleware (simple_auth.py for user validation)
├── table/
│   ├── analysis/      # Core data analysis (model, crud, service, api layers)
│   ├── search/        # Search schemas and components
│   └── upload/        # CSV file upload and processing (csv_processor.py handles GB files)
└── user/              # User management (model, crud, api, admin)

main.py                # FastAPI app entry, lifespan management, health check
config.py              # Pydantic settings (env-based config)
database.py            # SQLAlchemy 2.0 sync + async engines, session factories
```

### Layered Architecture Pattern

**All table modules follow strict layering:**

1. **Model** (`*_model.py`): SQLAlchemy 2.0 ORM models using `DeclarativeBase`, schema-qualified
2. **CRUD** (`*_crud.py`): Database access layer, no business logic
3. **Service** (`*_service.py`): Business logic, calls CRUD layer
4. **API** (`*_api.py`): FastAPI routers, thin controllers calling services
5. **Schemas** (`*_schemas.py`): Pydantic v2 models for request/response DTOs

**Example**: `app/table/analysis/` - `analysis_api.py` → `analysis_service.py` → `analysis_crud.py` → `analysis_model.py`

### Data Import Flow

**CSV Upload Processing** (in `app/table/upload/upload_api.py` + `csv_processor.py`):
- Validates CSV structure (metadata + header + data rows)
- Chunks files using pandas (configurable `BATCH_SIZE`, default 6000)
- Uses PostgreSQL UPSERT (`INSERT ... ON CONFLICT DO UPDATE`) for deduplication
- Implements connection retry and mini-batch commits (`MINIBATCH_SIZE=500`)
- Supports both daily and weekly data types with different field mappings
- Ranking trend stored as JSONB array (last 7 days)

### Authentication & Authorization

- JWT-based authentication via `fastapi-user-auth` + custom `simple_auth.py`
- Middleware: `AdminAuthMiddleware` validates tokens from Cookie/Authorization header
- Default admin: `admin / pwd123` (change `ADMIN_SECRET_KEY` in production)

### Database Design

**Schema**: `analysis`

**Main Table**: `amazon_origin_search_data`
- Unique constraint on `keyword`
- Stores daily + weekly ranking data in same row
- JSONB field `ranking_trend_day` for 7-day history
- Indexed on: `report_date_day`, `current_rangking_day`, `top_brand`, `top_category`

**View**: `my_category_stats` - Category statistics for dropdown options

### Performance Optimizations

1. **Connection Pooling**: `DB_POOL_SIZE=8`, `DB_MAX_OVERFLOW=12` per worker (RDS max_connections=400 is the hard ceiling — total connections across all workers must stay well below it)
2. **Query Optimization**:
   - Uses PG statistics estimates (`pg_class.reltuples`) for total count on large tables
   - `LIMIT 10000` count for filtered queries on pagination
   - Default filter excludes keywords containing brand name and rank=0 records
   - Blacklist categories filtered out (Books, Grocery, Video Games, etc.)
3. **Batch Processing**: `BATCH_SIZE=10000` for pandas chunks, `MINIBATCH_SIZE=500` for DB commits
4. **Multi-processing**: `MAX_WORKERS=2`, threshold `MULTIPROCESSING_THRESHOLD_MB=100MB`, `FILE_SPLIT_LINES=100000` lines per shard
   - Daily (300K rows, ~150MB, ~3 shards): multiprocessing overhead may not be worthwhile
   - Weekly (3M rows, ~1.5GB, ~30 shards): multiprocessing is essential but still takes 30–60 min on RDS 2C4G

## Key Configuration (.env)

```bash
# Database (sync + async URLs both required)
DATABASE_URL=postgresql://user:pass@host/db
DATABASE_URL_ASYNC=postgresql+asyncpg://user:pass@host/db
DATABASE_SCHEMA=analysis

# Upload
MAX_FILE_SIZE=3221225472  # 3GB
BATCH_SIZE=6000
MINIBATCH_SIZE=500

# Performance
DB_POOL_SIZE=100
DB_MAX_OVERFLOW=50
```

## Production Environment

| Category | Spec |
|----------|------|
| App Server | Alibaba Cloud ECS · Alibaba Cloud Linux 3.2104 LTS 64-bit · 4 vCPU / 8 GiB RAM |
| Database | Alibaba Cloud RDS PostgreSQL 15.0 · 2C4G · max_connections = 400 |

**Capacity implications for code changes:**
- RDS max 400 connections shared across all app processes; total `(DB_POOL_SIZE + DB_MAX_OVERFLOW) × workers` must stay well under 400
- 4 vCPU constrains `MAX_WORKERS` for multiprocessing CSV imports — use ≤ 3 to leave headroom for concurrent API traffic
- 8 GiB RAM limits in-memory pandas chunks; keep `BATCH_SIZE` ≤ 6000 rows per chunk

## Important Constraints

1. **SQLAlchemy 2.0 Only**: Use `DeclarativeBase`, `Mapped`, `mapped_column`, explicit `select()` statements, and `Session.execute(...).scalars()` patterns. Do not introduce `declarative_base()`, `Column(...)` model fields, or `.query()` APIs.
2. **Dual Engines**: Both sync (`engine`) and async (`async_engine`) available - choose appropriately
3. **Schema Qualification**: All models use `schema=settings.DATABASE_SCHEMA`
4. **UPSERT Logic**: CSV imports use raw SQL with `ON CONFLICT (keyword) DO UPDATE` for complex ranking trend JSONB manipulation
5. **Transaction Boundaries**: Service layer controls commits, CRUD layer never commits
6. **Pydantic 2.0 Only**: Use `ConfigDict`, `field_validator`, `model_validate()`, and `model_dump()`. Do not introduce v1-style `class Config`, `@validator`, `@root_validator`, `.dict()`, or `.parse_obj()` usage. Keep ORM models, request schemas, and response schemas typed precisely.

## Development Guardrails

- **Database migrations**: Schema changes must go through Alembic migrations, not ad hoc SQL against application databases. When changing ORM models, add or update the matching migration and verify it targets the configured `analysis` schema correctly.
- **Session/process safety**: Large imports must avoid long transactions and must not reuse parent-process database connections inside worker processes. Worker processes should create and dispose their own database engine/session scope.
- **Async boundaries**: Do not run long CPU-bound CSV parsing, blocking database work, or full-file reads directly on the FastAPI event loop. Use the existing chunked processing, process pool, or background workflow patterns for large imports, and keep request handlers thin.
- **Data-heavy paths**: Design for bounded memory and bounded result sets. Do not load entire GB-scale CSV files into memory, do not return unpaginated table-sized query results, and consider indexes and query plans when adding filters or joins used by search/analysis screens.
- **Tests**: Unit tests should not require a live PostgreSQL instance; database/import tests may require `.env` and should use small fixtures rather than real production CSV files. New tests must be collectable by `pytest` and must not reference deleted or experimental module paths. Bug fixes should include a focused regression test when behavior can be reproduced locally.
- **Configuration and local data**: Keep `.env.example` in sync when adding new settings, including units and safe local defaults. Do not commit `uploads/`, `logs/`, temporary split files, or real customer/search-report CSV data.
