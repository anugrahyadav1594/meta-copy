# MetaScale — Member 1: Database Schema & Normalization

This module contains the **canonical relational database** for the MetaScale project.

MetaScale is a social-media database project used to study how a normalized relational design can later be optimized using:

- denormalization
- sharding
- replication
- caching
- feed generation
- search/indexing
- distributed services

## What this module owns

Member 1 owns the source-of-truth relational model:

1. Database tables
2. Primary keys
3. Foreign keys
4. Unique constraints
5. Check constraints
6. Indexes
7. 3NF-oriented design
8. Seed/demo data
9. ER diagram
10. Database documentation

The later modules should treat this schema as the **canonical source of truth**.

---

## Recommended stack

- PostgreSQL
- SQL migrations
- Docker/PostgreSQL later through the main project

No frontend is required for this module.

---

## Folder structure

```text
metascale-member1/
├── README.md
├── .gitignore
├── docs/
│   ├── schema.md
│   ├── normalization.md
│   └── er-diagram.md
└── database/
    ├── 001_schema.sql
    ├── 002_indexes.sql
    └── 003_seed.sql
```

---

## How to run

### 1. Create the database

```sql
CREATE DATABASE metascale;
```

Then connect to it:

```bash
psql -U postgres -d metascale
```

### 2. Run schema

```bash
psql -U postgres -d metascale -f database/001_schema.sql
```

### 3. Run indexes

```bash
psql -U postgres -d metascale -f database/002_indexes.sql
```

### 4. Load demo data

```bash
psql -U postgres -d metascale -f database/003_seed.sql
```

---

## Design principle

The database is intentionally **normalized first**.

For example, a post stores only:

```text
user_id
caption
created_at
```

It does not repeatedly store:

```text
username
email
user_name
profile information
```

because those values belong to the user/profile records.

This reduces update anomalies and keeps one authoritative copy of each fact.

---

## How teammates should build on this

Do not directly redesign these tables inside another module.

Use this database as the source of truth.

Examples:

- Member 2 can optimize queries using indexes and `EXPLAIN ANALYZE`.
- Member 3 can create denormalized read tables/materialized views.
- Member 4 can shard tables using a user-based routing strategy.
- Member 5 can configure PostgreSQL replication.
- Member 6 can cache frequently-read records.
- Member 7 can build feed tables/services.
- Member 8 can store media externally and keep only metadata here.
- Member 9 can create a social-graph access layer.
- Member 10 can index searchable text externally.

---

## Suggested Git workflow

Each member should create a branch:

```bash
git checkout -b feature/member-1-schema
```

After completing work:

```bash
git add .
git commit -m "feat(database): add normalized schema and seed data"
git push -u origin feature/member-1-schema
```

Then create a Pull Request into `main`.

The team should not all push directly to `main`.

---

## Viva statement

> "My responsibility is to build the canonical normalized relational database. I first identify the entities and relationships of the social-media system, then convert them into tables with primary and foreign keys. The design follows normalization up to 3NF so that each fact has a clear owner and redundancy is minimized. This database becomes the source of truth for the rest of the distributed architecture."
