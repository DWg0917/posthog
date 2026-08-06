# PostHog Self-Hosted Production Deployment Plan

## Overview

This document describes all changes needed to create a production-grade, feature-complete self-hosted PostHog deployment.

**Branch**: `feat/self-hosted-production`

**Architecture**: Data layer (high-performance machine) + Application layer (cheaper machines), connected via network.

---

## Hobby Deployment Audit

### Missing Services (in base but not in hobby)

| Service        | Importance   | Description                                                              |
| -------------- | ------------ | ------------------------------------------------------------------------ |
| `capture-ai`   | **Critical** | AI event capture, routes `/i/v0/ai/*`, writes to `ai_events_ingestion`   |
| `capture-logs` | **Critical** | OTLP log/trace/metrics, writes to `logs_ingestion` + `metrics_ingestion` |
| `flower`       | Medium       | Celery monitoring UI                                                     |
| `kafka_ui`     | Medium       | Kafka management UI                                                      |

### Missing Kafka Topics

| Topic                                    | Used By                  | Impact                     |
| ---------------------------------------- | ------------------------ | -------------------------- |
| `ai_events_ingestion`                    | capture-ai               | AI events lost             |
| `logs_ingestion`                         | capture-logs             | Log ingestion fails        |
| `metrics_ingestion`                      | capture-logs             | Metrics ingestion fails    |
| `session_recording_snapshot_item_events` | replay-capture           | Session replay may fail    |
| `events_plugin_ingestion_historical`     | capture (historical)     | Historical re-import fails |
| `ingestion-errortracking-main`           | ingestion-error-tracking | Error tracking lost        |

### Current Limitations

- ClickHouse: single node, no replication
- Redis: single instance, 200MB, no persistence
- Kafka: single broker, 3GB memory
- Zookeeper: single node
- All services on one machine (no data/app separation)

---

## Step 1: Unlock All Features

**File**: `posthog/models/organization.py` (lines 357-387)

**Change**: Modify `update_available_product_features()` — self-hosted always gets Enterprise features. `is_cloud()` check at top ensures Cloud is unaffected.

**Expected result**: Multi-project, advanced paths, group analytics, alerts, RBAC, SAML, SCIM all enabled.

---

## Step 2: ClickHouse Production Config — Memory & Query Limits

**File**: `docker/clickhouse/config.d/production.xml` (new)

**Settings**: `max_server_memory_usage_to_ram_ratio=0.8`, `background_pool_size=16`, `kafka.max_consumers=4`, `mark_cache_size=10GB`, `uncompressed_cache_size=8GB`.

**Expected result**: Better query performance, Kafka concurrency from 1 to 4.

---

## Step 3: ClickHouse Production Config — User/Profile Settings

**File**: `docker/clickhouse/users.d/production.xml` (new)

**Settings**: `max_memory_usage` from `CLICKHOUSE_MAX_MEMORY_PER_QUERY` env, `max_threads` from `CLICKHOUSE_MAX_THREADS` env, `max_concurrent_queries_for_user=100`.

**Expected result**: Query memory and threads configurable via env vars.

---

## Step 4: ClickHouse Cluster Config — Single Node & 2x2

**Files**:

- `docker/clickhouse/config.d/cluster-single.xml` (new) — single node remote_servers
- `docker/clickhouse/config.d/cluster-2x2.xml` (new) — 2 shards x 2 replicas remote_servers
- `docker/clickhouse/macros/ch1.xml` through `ch4.xml` (new) — per-node macros

**How it works**:

- Copy the desired config to `cluster.xml` before starting
- Single node: `cluster-single.xml` → 1 ClickHouse container
- 2x2 cluster: `cluster-2x2.xml` → 4 ClickHouse containers (clickhouse-1 to clickhouse-4)
- Each node gets its own macros file (shard 01/02, replica ch1-ch4)
- `internal_replication=true` on each shard — ClickHouse handles replica sync via Zookeeper

**Expected result**: Switchable between single node and HA cluster via config file swap.

---

## Step 5: Data Layer Compose

**File**: `docker-compose.data.yml` (new)

**Purpose**: All data services on a high-performance machine.

**Services**:

| Service             | Config                                                                    | Memory          |
| ------------------- | ------------------------------------------------------------------------- | --------------- |
| PostgreSQL          | shared_buffers=4GB, work_mem=64MB, max_connections=200, wal_level=replica | 16GB limit      |
| Redis Master        | RDB+AOF, maxmemory=2GB, allkeys-lru                                       | 4GB limit       |
| Redis Replica       | replicaof master, AOF                                                     | 4GB limit       |
| Redis Sentinel x3   | monitor master, auto-failover                                             | -               |
| Zookeeper x3        | 3-node cluster, autopurge                                                 | 2GB each        |
| Kafka (Redpanda) x3 | 3 brokers, 6GB memory, smp=4                                              | 8GB each        |
| Kafka Init          | Creates all 22 topics with replication-factor=3                           | -               |
| ClickHouse x1 or x4 | Single node or 2x2 cluster                                                | 32GB limit each |
| Elasticsearch       | Temporal dependency, single-node                                          | 4GB             |
| Temporal + UI       | Workflow engine                                                           | 2GB             |

**Expected result**: All data services isolated on dedicated machine, properly tuned and clustered.

---

## Step 6: Application Layer Compose

**File**: `docker-compose.app.yml` (new)

**Purpose**: All app services on cheaper machines, connecting to data layer via `DATA_LAYER_HOST` env var.

**Services**:

| Service                 | Replicas | Connects to          |
| ----------------------- | -------- | -------------------- |
| web                     | 2        | PG, CH, Redis, Kafka |
| worker                  | 2        | PG, CH, Redis, Kafka |
| plugins                 | 2        | PG, Redis, Kafka     |
| ingestion-general       | 2        | PG, Kafka            |
| capture                 | 2        | Kafka, Redis         |
| capture-ai              | 1        | Kafka, Redis         |
| capture-logs            | 1        | Kafka                |
| feature-flags           | 1        | PG, Redis            |
| personhog-replica       | 1        | PG                   |
| objectstorage/seaweedfs | 1 each   | local                |
| flower                  | 1        | Redis (data layer)   |
| kafka_ui                | 1        | Kafka (data layer)   |

Data-layer services (db, redis, clickhouse, zookeeper, kafka, elasticsearch) are set to `replicas: 0` — they run on the data machine.

**Expected result**: App layer scales independently, data layer stays stable.

---

## Step 7: Add Missing Services & Kafka Topics

**File**: `docker-compose.data.yml` (kafka-init section) + `docker-compose.app.yml` (capture-ai, capture-logs, flower, kafka_ui)

**Change**: Add 6 missing Kafka topics to kafka-init, add 4 missing services.

**Expected result**: AI capture, log capture, all Kafka topics working.

---

## Step 8: Redis Production Config

**File**: `docker-compose.data.yml` (already included in Step 5)

**Settings**: Master + Replica + 3 Sentinels, RDB+AOF persistence, maxmemory configurable.

**Expected result**: Redis HA with automatic failover, data persistence.

---

## Step 9: Kafka 3-Broker Cluster

**File**: `docker-compose.data.yml` (already included in Step 5)

**Settings**: 3 Redpanda brokers, 6GB memory each, topic replication-factor=3.

**Expected result**: Messages replicated across 3 brokers, single broker failure doesn't lose data.

---

## Step 10: Zookeeper 3-Node Cluster

**File**: `docker-compose.data.yml` (already included in Step 5)

**Settings**: 3 ZK nodes with proper MY_ID and server list.

**Expected result**: Zookeeper quorum survives 1 node failure.

---

## Step 11: Custom AI Provider Support

**Files**:

- `posthog/settings/web.py` — add `OPENAI_BASE_URL` and `ANTHROPIC_BASE_URL` settings
- `ee/hogai/llm.py` — `MaxChatAnthropic.model_post_init` reads `settings.ANTHROPIC_BASE_URL`

**How it works**:

- `ChatOpenAI` auto-reads `OPENAI_BASE_URL` env — no code change needed
- `ChatAnthropic` needs `base_url` passed explicitly — we add it in `model_post_init`
- Unset = direct API calls (current behavior)

**Expected result**: Configurable AI providers via env vars (e.g., Xiaomi MiMo for China).

---

## Step 12: Environment Variable Template

**File**: `.env.prod` (new)

**Sections**: Domain, secrets, registry, PG tuning, CH memory, Redis, Kafka, AI providers, scaling replicas, DATA_LAYER_HOST.

---

## Step 13: Build Script

**File**: `build-images.sh` (new)

**Why custom images are required**: Steps 1 and 11 modify Python source code (`organization.py`, `web.py`, `llm.py`). Official PostHog images don't include these changes, so custom images must be built.

### Images to Build

| Image                   | Dockerfile        | Must Custom Build? | Reason                                 |
| ----------------------- | ----------------- | ------------------ | -------------------------------------- |
| `posthog:tag`           | `Dockerfile`      | **Yes**            | Contains Django code from Steps 1 & 11 |
| `posthog-node:tag`      | `Dockerfile.node` | Recommended        | Version consistency, China network     |
| `capture:tag`           | `rust/Dockerfile` | Optional           | No code changes, but China network     |
| `capture-logs:tag`      | `rust/Dockerfile` | Optional           | Same                                   |
| `replay-capture:tag`    | `rust/Dockerfile` | Optional           | Same                                   |
| `property-defs-rs:tag`  | `rust/Dockerfile` | Optional           | Same                                   |
| `personhog-replica:tag` | `rust/Dockerfile` | Optional           | Same                                   |
| `personhog-router:tag`  | `rust/Dockerfile` | Optional           | Same                                   |
| `feature-flags:tag`     | `rust/Dockerfile` | Optional           | Same                                   |
| `hypercache-server:tag` | `rust/Dockerfile` | Optional           | Same                                   |
| `cyclotron-janitor:tag` | `rust/Dockerfile` | Optional           | Same                                   |
| `cymbal:tag`            | `rust/Dockerfile` | Optional           | Same                                   |

**Minimum**: Only the Django app image (`Dockerfile`) MUST be custom built. Others can use official images if network allows.

### Build Dependencies

The build process downloads:

- **Python packages** (pypi) — ~500MB
- **Node.js packages** (npm) — ~1GB
- **Rust crates** (crates.io) — ~200MB per service
- **Base images** (Debian bookworm, Python, Node) — ~2GB

### China Build Tips

For building in mainland China, configure mirror sources before building:

```bash
# pip mirror (add to Dockerfile or docker build --build-arg)
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# npm mirror
npm config set registry https://registry.npmmirror.com

# cargo mirror (add to rust/.cargo/config.toml)
[registries.crates-io]
replace-with = 'ustc'

[source.ustc]
registry = "sparse+https://mirrors.ustc.edu.cn/crates.io-index/"

# Docker base image mirror
# Use Alibaba Cloud mirror: registry.cn-hangzhou.aliyuncs.com
```

**Alternative**: Build on an overseas machine, push to a China-accessible registry (Alibaba Cloud ACR, Tencent Cloud TCR, Harbor).

### Build Script Contents

```bash
#!/bin/bash
set -euo pipefail

REGISTRY="${REGISTRY_URL:-your-registry.com/posthog}"
TAG="${BUILD_TAG:-latest}"

# 1. Django App (REQUIRED - contains code changes)
docker build -t "${REGISTRY}:${TAG}" -f Dockerfile .

# 2. Node.js Ingestion
docker build -t "${REGISTRY}-node:${TAG}" -f Dockerfile.node .

# 3. Rust services (optional - can use official images)
for svc in capture capture-logs replay-capture property-defs-rs \
           personhog-replica personhog-router feature-flags \
           hypercache-server cyclotron-janitor cymbal; do
    docker build -t "${REGISTRY}/${svc}:${TAG}" \
        -f rust/Dockerfile --build-arg BIN="${svc}" rust/
done
```

---

## Step 14: Deployment Script

**File**: `deploy.sh` (new)

**Operations**: Validate env → start data layer → wait health → run migrations → start app layer → health check.

### Deployment Flow

```text
1. Validate .env exists and required vars are set
2. Start data layer (docker-compose.data.yml)
   ├─ PostgreSQL, Redis (master+replica+sentinel), Zookeeper x3
   ├─ Kafka x3, ClickHouse (single or 2x2)
   └─ Elasticsearch, Temporal
3. Wait for all health checks to pass
4. Run migrations
   ├─ python manage.py migrate (PostgreSQL)
   └─ python manage.py migrate_clickhouse (ClickHouse)
5. Start app layer (docker-compose.app.yml)
   ├─ web x2, worker x2, plugins x2
   ├─ capture x2, capture-ai, capture-logs
   └─ ingestion-*, feature-flags, personhog, etc.
6. Health check verification
```

### First Time vs Upgrade

| Operation  | First Time       | Upgrade             |
| ---------- | ---------------- | ------------------- |
| Data layer | Start all        | Keep running        |
| Migrations | Full run         | Incremental         |
| App layer  | Start all        | Rolling update      |
| Downtime   | During migration | Near zero (rolling) |

---

## Summary of All Files

| File                                            | Action | Step           |
| ----------------------------------------------- | ------ | -------------- |
| `posthog/models/organization.py`                | Modify | 1              |
| `docker/clickhouse/config.d/production.xml`     | Create | 2              |
| `docker/clickhouse/users.d/production.xml`      | Create | 3              |
| `docker/clickhouse/config.d/cluster-single.xml` | Create | 4              |
| `docker/clickhouse/config.d/cluster-2x2.xml`    | Create | 4              |
| `docker/clickhouse/macros/ch1.xml` ~ `ch4.xml`  | Create | 4              |
| `docker-compose.data.yml`                       | Create | 5, 7, 8, 9, 10 |
| `docker-compose.app.yml`                        | Create | 6, 7           |
| `posthog/settings/web.py`                       | Modify | 11             |
| `ee/hogai/llm.py`                               | Modify | 11             |
| `.env.prod`                                     | Create | 12             |
| `build-images.sh`                               | Create | 13             |
| `deploy.sh`                                     | Create | 14             |
| `SELF_HOSTED_PROD_PLAN.md`                      | Update | -              |
| `SELF_HOSTED_PROD_CN.md`                        | Update | -              |

---

## Deployment Commands

```bash
# Data layer (high-performance machine)
cp docker/clickhouse/config.d/cluster-single.xml docker/clickhouse/config.d/cluster.xml
# OR for 2x2: cp docker/clickhouse/config.d/cluster-2x2.xml docker/clickhouse/config.d/cluster.xml
docker compose -f docker-compose.hobby.yml -f docker-compose.data.yml up -d

# App layer (cheaper machines)
export DATA_LAYER_HOST=192.168.1.100
docker compose -f docker-compose.hobby.yml -f docker-compose.app.yml up -d
```
