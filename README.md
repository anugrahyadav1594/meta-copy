# MetaScale — Member 2: Query Optimization Engine

This module contains the query performance analyzer and index optimization suite for the MetaScale project.

MetaScale is a social-media database project used to study how a normalized relational design can later be optimized using:

* denormalization
* sharding
* replication
* caching
* feed generation
* search/indexing
* distributed services

## What this module owns

Member 2 owns the performance analysis and optimization layer:

1. Query Performance Analyzer dashboard (Streamlit web interface)
2. EXPLAIN / EXPLAIN FORMAT=JSON diagnostic workflows
3. B-Tree index creation and breakdown testing
4. Execution plan cost profiling (Full Table Scan vs Index Lookup)

## Core Features Implemented

* **Live Query Benchmarking:** Evaluates 6 critical social media queries including Feed Generation, Post + Author details, Likes/Comments aggregation, Follower lookup, and Notification retrieval.
* **On-the-Fly Index Toggling:** Features a frontend switch to instantly inject or drop foreign key indexes to measure system behavioral changes.
* **Funnel Diagnostics Pipeline:** Visually streams raw SQL logic directly into isolated metric cards tracking Scan Strategy (`ALL` vs `REF`), Rows Examined, Optimizer Query Cost, and Latency (ms).

## Project Setup & Execution

### 1. Requirements
Ensure you have MySQL running locally and Python environment ready, then install dependencies:
```bash
pip install -r requirements.txt
```

### 2. Database Seeding
Run the standalone data generator to populate the relational tables with thousands of heavy mock profiles, follows, and interactions:
```bash
python seed.py
```

### 3. Launch Dashboard
Boot the analytical system locally:
```bash
streamlit run app.py
```
