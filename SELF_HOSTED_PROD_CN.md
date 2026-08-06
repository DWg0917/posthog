# PostHog 自部署生产级部署方案

## 概述

本文档描述了创建生产级、功能完整的自部署 PostHog 所需的所有改动。

**分支**: `feat/self-hosted-production`

**架构**: 数据层（高性能机器）+ 应用层（便宜机器），通过网络连接。

---

## Hobby 部署审计

### 缺失的服务（base 有但 hobby 没有）

| 服务           | 重要性   | 说明                                                                 |
| -------------- | -------- | -------------------------------------------------------------------- |
| `capture-ai`   | **关键** | AI 事件采集，路由 `/i/v0/ai/*`，写入 `ai_events_ingestion`           |
| `capture-logs` | **关键** | OTLP 日志/链路/指标采集，写入 `logs_ingestion` + `metrics_ingestion` |
| `flower`       | 中等     | Celery 监控 UI                                                       |
| `kafka_ui`     | 中等     | Kafka 管理 UI                                                        |

### 缺失的 Kafka Topics

| Topic                                    | 使用者                   | 影响             |
| ---------------------------------------- | ------------------------ | ---------------- |
| `ai_events_ingestion`                    | capture-ai               | AI 事件丢失      |
| `logs_ingestion`                         | capture-logs             | 日志采集失败     |
| `metrics_ingestion`                      | capture-logs             | 指标采集失败     |
| `session_recording_snapshot_item_events` | replay-capture           | 会话回放可能失败 |
| `events_plugin_ingestion_historical`     | capture（历史模式）      | 历史导入失败     |
| `ingestion-errortracking-main`           | ingestion-error-tracking | 错误追踪丢失     |

### 当前限制

- ClickHouse: 单节点，无副本
- Redis: 单实例，200MB，无持久化
- Kafka: 单 broker，3GB 内存
- Zookeeper: 单节点
- 所有服务在同一台机器上（无数据/应用分离）

---

## 步骤 1: 解锁所有功能

**文件**: `posthog/models/organization.py`（第 357-387 行）

**改动**: 修改 `update_available_product_features()` — 自部署始终给 Enterprise 功能。`is_cloud()` 在顶部拦截，Cloud 不受影响。

**预期结果**: 多项目、高级路径、群组分析、告警、RBAC、SAML、SCIM 全部启用。

---

## 步骤 2: ClickHouse 生产配置 — 内存与查询限制

**文件**: `docker/clickhouse/config.d/production.xml`（新建）

**配置**: `max_server_memory_usage_to_ram_ratio=0.8`、`background_pool_size=16`、`kafka.max_consumers=4`、`mark_cache_size=10GB`、`uncompressed_cache_size=8GB`。

**预期结果**: 查询性能提升，Kafka 并发消费者从 1 提升到 4。

---

## 步骤 3: ClickHouse 生产配置 — 用户/Profile 配置

**文件**: `docker/clickhouse/users.d/production.xml`（新建）

**配置**: `max_memory_usage` 从 `CLICKHOUSE_MAX_MEMORY_PER_QUERY` 环境变量读取，`max_threads` 从 `CLICKHOUSE_MAX_THREADS` 读取，`max_concurrent_queries_for_user=100`。

**预期结果**: 查询内存和线程数可通过环境变量配置。

---

## 步骤 4: ClickHouse 集群配置 — 单节点与 2×2

**文件**:

- `docker/clickhouse/config.d/cluster-single.xml`（新建）— 单节点 remote_servers
- `docker/clickhouse/config.d/cluster-2x2.xml`（新建）— 2 分片 × 2 副本 remote_servers
- `docker/clickhouse/macros/ch1.xml` ~ `ch4.xml`（新建）— 每个节点的 macros

**使用方式**:

- 启动前复制对应配置到 `cluster.xml`
- 单节点: `cluster-single.xml` → 1 个 ClickHouse 容器
- 2×2 集群: `cluster-2x2.xml` → 4 个 ClickHouse 容器（clickhouse-1 到 clickhouse-4）
- 每个节点有自己的 macros 文件（shard 01/02, replica ch1-ch4）
- 每个 shard 设置 `internal_replication=true` — ClickHouse 通过 Zookeeper 自动同步副本

**预期结果**: 通过配置文件切换单节点和高可用集群模式。

---

## 步骤 5: 数据层 Compose

**文件**: `docker-compose.data.yml`（新建）

**用途**: 所有数据服务部署在高性能机器上。

**服务列表**:

| 服务                | 配置                                                                      | 内存         |
| ------------------- | ------------------------------------------------------------------------- | ------------ |
| PostgreSQL          | shared_buffers=4GB, work_mem=64MB, max_connections=200, wal_level=replica | 16GB 上限    |
| Redis Master        | RDB+AOF 持久化, maxmemory=2GB, allkeys-lru                                | 4GB 上限     |
| Redis Replica       | replicaof master, AOF                                                     | 4GB 上限     |
| Redis Sentinel ×3   | 监控 master，自动故障转移                                                 | -            |
| Zookeeper ×3        | 3 节点集群，自动清理                                                      | 各 2GB       |
| Kafka (Redpanda) ×3 | 3 broker, 6GB 内存, smp=4                                                 | 各 8GB       |
| Kafka Init          | 创建全部 22 个 topic，replication-factor=3                                | -            |
| ClickHouse ×1 或 ×4 | 单节点或 2×2 集群                                                         | 各 32GB 上限 |
| Elasticsearch       | Temporal 依赖，单节点                                                     | 4GB          |
| Temporal + UI       | 工作流引擎                                                                | 2GB          |

**预期结果**: 所有数据服务隔离在专用机器上，调优完成，集群就绪。

---

## 步骤 6: 应用层 Compose

**文件**: `docker-compose.app.yml`（新建）

**用途**: 所有应用服务部署在便宜机器上，通过 `DATA_LAYER_HOST` 环境变量连接数据层。

**服务列表**:

| 服务                    | 副本数 | 连接                 |
| ----------------------- | ------ | -------------------- |
| web                     | 2      | PG, CH, Redis, Kafka |
| worker                  | 2      | PG, CH, Redis, Kafka |
| plugins                 | 2      | PG, Redis, Kafka     |
| ingestion-general       | 2      | PG, Kafka            |
| capture                 | 2      | Kafka, Redis         |
| capture-ai              | 1      | Kafka, Redis         |
| capture-logs            | 1      | Kafka                |
| feature-flags           | 1      | PG, Redis            |
| personhog-replica       | 1      | PG                   |
| objectstorage/seaweedfs | 各 1   | 本地                 |
| flower                  | 1      | Redis（数据层）      |
| kafka_ui                | 1      | Kafka（数据层）      |

数据层服务（db, redis, clickhouse, zookeeper, kafka, elasticsearch）设置为 `replicas: 0` — 它们在数据机器上运行。

**预期结果**: 应用层独立扩展，数据层保持稳定。

---

## 步骤 7: 补充缺失服务与 Kafka Topics

**文件**: `docker-compose.data.yml`（kafka-init 部分）+ `docker-compose.app.yml`（capture-ai, capture-logs, flower, kafka_ui）

**改动**: kafka-init 添加 6 个缺失 topic，添加 4 个缺失服务。

**预期结果**: AI 采集、日志采集、所有 Kafka topic 正常工作。

---

## 步骤 8: Redis 生产配置

**文件**: `docker-compose.data.yml`（已在步骤 5 中包含）

**配置**: Master + Replica + 3 Sentinel，RDB+AOF 持久化，maxmemory 可配置。

**预期结果**: Redis 高可用，自动故障转移，数据持久化。

---

## 步骤 9: Kafka 3 Broker 集群

**文件**: `docker-compose.data.yml`（已在步骤 5 中包含）

**配置**: 3 个 Redpanda broker，各 6GB 内存，topic replication-factor=3。

**预期结果**: 消息在 3 个 broker 上复制，单 broker 故障不丢数据。

---

## 步骤 10: Zookeeper 3 节点集群

**文件**: `docker-compose.data.yml`（已在步骤 5 中包含）

**配置**: 3 个 ZK 节点，正确配置 MY_ID 和 server 列表。

**预期结果**: Zookeeper 法定人数可承受 1 个节点故障。

---

## 步骤 11: 自定义 AI Provider 支持

**文件**:

- `posthog/settings/web.py` — 添加 `OPENAI_BASE_URL` 和 `ANTHROPIC_BASE_URL` 配置
- `ee/hogai/llm.py` — `MaxChatAnthropic.model_post_init` 读取 `settings.ANTHROPIC_BASE_URL`

**工作原理**:

- `ChatOpenAI` 自动读取 `OPENAI_BASE_URL` 环境变量 — 无需改代码
- `ChatAnthropic` 需要显式传 `base_url` — 我们在 `model_post_init` 中添加
- 不设置 = 直连 API（当前行为）

**预期结果**: 通过环境变量配置 AI provider（如中国用小米 MiMo）。

---

## 步骤 12: 环境变量模板

**文件**: `.env.prod`（新建）

**章节**: 域名、密钥、Registry、PG 调优、CH 内存、Redis、Kafka、AI provider、副本数、DATA_LAYER_HOST。

---

## 步骤 13: 构建脚本

**文件**: `build-images.sh`（新建）

**为什么必须自编译镜像**: 步骤 1 和 11 修改了 Python 源码（`organization.py`、`web.py`、`llm.py`）。PostHog 官方镜像不包含这些修改，所以必须自己构建。

### 需要构建的镜像

| 镜像                    | Dockerfile        | 必须自编译？ | 原因                                |
| ----------------------- | ----------------- | ------------ | ----------------------------------- |
| `posthog:tag`           | `Dockerfile`      | **必须**     | 包含步骤 1 和 11 的 Django 代码修改 |
| `posthog-node:tag`      | `Dockerfile.node` | 建议         | 版本一致性 + 国内网络               |
| `capture:tag`           | `rust/Dockerfile` | 可选         | 无代码改动，但国内拉取困难          |
| `capture-logs:tag`      | `rust/Dockerfile` | 可选         | 同上                                |
| `replay-capture:tag`    | `rust/Dockerfile` | 可选         | 同上                                |
| `property-defs-rs:tag`  | `rust/Dockerfile` | 可选         | 同上                                |
| `personhog-replica:tag` | `rust/Dockerfile` | 可选         | 同上                                |
| `personhog-router:tag`  | `rust/Dockerfile` | 可选         | 同上                                |
| `feature-flags:tag`     | `rust/Dockerfile` | 可选         | 同上                                |
| `hypercache-server:tag` | `rust/Dockerfile` | 可选         | 同上                                |
| `cyclotron-janitor:tag` | `rust/Dockerfile` | 可选         | 同上                                |
| `cymbal:tag`            | `rust/Dockerfile` | 可选         | 同上                                |

**最低要求**: 只有 Django app 镜像（`Dockerfile`）必须自编译。其他可以用官方镜像（如果网络允许）。

### 构建依赖

构建过程中需要下载：

- **Python 包**（pypi）— 约 500MB
- **Node.js 包**（npm）— 约 1GB
- **Rust crates**（crates.io）— 每个服务约 200MB
- **基础镜像**（Debian bookworm、Python、Node）— 约 2GB

### 国内构建技巧

在中国大陆构建时，建议配置镜像源：

```bash
# pip 镜像（添加到 Dockerfile 或 docker build --build-arg）
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# npm 镜像
npm config set registry https://registry.npmmirror.com

# cargo 镜像（添加到 rust/.cargo/config.toml）
[registries.crates-io]
replace-with = 'ustc'

[source.ustc]
registry = "sparse+https://mirrors.ustc.edu.cn/crates.io-index/"

# Docker 基础镜像加速
# 使用阿里云镜像: registry.cn-hangzhou.aliyuncs.com
```

**替代方案**: 在海外机器上构建，推送到国内可访问的 registry（阿里云 ACR、腾讯云 TCR、Harbor）。

### 构建脚本内容

```bash
#!/bin/bash
set -euo pipefail

REGISTRY="${REGISTRY_URL:-your-registry.com/posthog}"
TAG="${BUILD_TAG:-latest}"

# 1. Django App（必须 — 包含代码修改）
docker build -t "${REGISTRY}:${TAG}" -f Dockerfile .

# 2. Node.js Ingestion
docker build -t "${REGISTRY}-node:${TAG}" -f Dockerfile.node .

# 3. Rust 服务（可选 — 可用官方镜像）
for svc in capture capture-logs replay-capture property-defs-rs \
           personhog-replica personhog-router feature-flags \
           hypercache-server cyclotron-janitor cymbal; do
    docker build -t "${REGISTRY}/${svc}:${TAG}" \
        -f rust/Dockerfile --build-arg BIN="${svc}" rust/
done
```

---

## 步骤 14: 部署脚本

**文件**: `deploy.sh`（新建）

**流程**: 验证环境变量 → 启动数据层 → 等待健康检查 → 运行迁移 → 启动应用层 → 健康检查。

### 部署流程

```text
1. 验证 .env 存在且必需变量已设置
2. 启动数据层（docker-compose.data.yml）
   ├─ PostgreSQL, Redis (master+replica+sentinel), Zookeeper ×3
   ├─ Kafka ×3, ClickHouse (单节点或 2×2)
   └─ Elasticsearch, Temporal
3. 等待所有健康检查通过
4. 运行迁移
   ├─ python manage.py migrate（PostgreSQL）
   └─ python manage.py migrate_clickhouse（ClickHouse）
5. 启动应用层（docker-compose.app.yml）
   ├─ web ×2, worker ×2, plugins ×2
   ├─ capture ×2, capture-ai, capture-logs
   └─ ingestion-*, feature-flags, personhog 等
6. 健康检查验证
```

### 首次部署 vs 升级

| 操作     | 首次部署 | 升级               |
| -------- | -------- | ------------------ |
| 数据层   | 启动全部 | 保持运行           |
| 迁移     | 全量运行 | 增量运行           |
| 应用层   | 启动全部 | 滚动更新           |
| 停机时间 | 迁移期间 | 接近零（滚动更新） |

---

## 文件总览

| 文件                                            | 操作 | 步骤           |
| ----------------------------------------------- | ---- | -------------- |
| `posthog/models/organization.py`                | 修改 | 1              |
| `docker/clickhouse/config.d/production.xml`     | 新建 | 2              |
| `docker/clickhouse/users.d/production.xml`      | 新建 | 3              |
| `docker/clickhouse/config.d/cluster-single.xml` | 新建 | 4              |
| `docker/clickhouse/config.d/cluster-2x2.xml`    | 新建 | 4              |
| `docker/clickhouse/macros/ch1.xml` ~ `ch4.xml`  | 新建 | 4              |
| `docker-compose.data.yml`                       | 新建 | 5, 7, 8, 9, 10 |
| `docker-compose.app.yml`                        | 新建 | 6, 7           |
| `posthog/settings/web.py`                       | 修改 | 11             |
| `ee/hogai/llm.py`                               | 修改 | 11             |
| `.env.prod`                                     | 新建 | 12             |
| `build-images.sh`                               | 新建 | 13             |
| `deploy.sh`                                     | 新建 | 14             |

---

## 部署命令

```bash
# 数据层（高性能机器）
cp docker/clickhouse/config.d/cluster-single.xml docker/clickhouse/config.d/cluster.xml
# 或 2×2: cp docker/clickhouse/config.d/cluster-2x2.xml docker/clickhouse/config.d/cluster.xml
docker compose -f docker-compose.hobby.yml -f docker-compose.data.yml up -d

# 应用层（便宜机器）
export DATA_LAYER_HOST=192.168.1.100
docker compose -f docker-compose.hobby.yml -f docker-compose.app.yml up -d
```

---

## 附录: Hobby vs 生产部署对比

| 维度         | Hobby 部署                   | 生产部署                                     |
| ------------ | ---------------------------- | -------------------------------------------- |
| 功能         | 需要 License，否则无高级功能 | 全部 Enterprise 功能解锁                     |
| 架构         | 所有服务单机                 | 数据层 + 应用层分离                          |
| ClickHouse   | 单节点，默认配置             | 可选单节点或 2×2 集群，调优内存              |
| PostgreSQL   | 默认配置                     | 调优 shared_buffers/work_mem/max_connections |
| Redis        | 200MB，无持久化              | Master+Replica+Sentinel，RDB+AOF             |
| Kafka        | 单 broker，3GB               | 3 broker，各 6GB，消息 3 副本                |
| Zookeeper    | 单节点                       | 3 节点集群                                   |
| AI Provider  | 硬编码 Anthropic/OpenAI      | 可配置任意兼容 provider                      |
| capture-ai   | 缺失                         | 已补充                                       |
| capture-logs | 缺失                         | 已补充                                       |
| Kafka Topics | 缺少 6 个                    | 全部补齐，replication-factor=3               |
| 应用层       | 全部单实例                   | 可配多副本                                   |
| 资源限制     | 无                           | 所有容器有内存上限                           |
