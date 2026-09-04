# Hobby 部署完整性分析与改进计划

## 📊 当前状态分析

你的观察非常准确!虽然我之前解决了**许可证和权限**问题,但 Hobby 部署在**容器配置、环境变量、Kafka 队列**等方面确实还有大量缺失。

---

## 🔍 缺失内容详细分析

### 1️⃣ **Kafka Topic 不完整**

当前 `kafka-init` 只创建了基础 Topic,缺少很多 Cloud 才有的 Topic:

#### ✅ 已创建的 Topic (16个):
```bash
clickhouse_events_json
clickhouse_ai_events_json
clickhouse_heatmap_events
clickhouse_flag_evaluations
clickhouse_ingestion_warnings
events_plugin_ingestion_ai
events_plugin_ingestion_dlq
events_plugin_ingestion_overflow
events_plugin_ingestion_async
ingestion-clientwarnings-main-1
heatmaps_ingestion
clickhouse_groups
clickhouse_person
clickhouse_person_distinct_id
clickhouse_app_metrics2
log_entries
clickhouse_tophog
```

#### ❌ 缺失的 Topic (Cloud 有但 Hobby 没有):
```bash
# Session Recording 相关
session_recording_snapshot_item_events
session_recording_events

# CDP/Destinations 相关
cdp_events
cdp_ingestion

# Batch Exports
batch_exports
batch_exports_dead_letter_queue

# Data Warehouse 相关
external_data_source_jobs
data_warehouse_jobs

# Experiments
experiment_metrics
experiment_exposures

# Alerts & Workflows
alerts_queue
workflows_email_queue
workflows_batch_queue

# Surveys
survey_responses

# Feature Flags
feature_flag_requests

# Usage & Billing
usage_reports
usage_reports_v2
billing_events

# Error Tracking
error_tracking_symbol_sets
error_tracking_issue_groups

# Logs & Traces
logs_ingestion
metrics_ingestion
traces_ingestion

# AI 相关 (额外)
ai_task_runs
ai_feedback_events

# Data Modeling
data_modeling_jobs
data_modeling_runs

# Messenger/Conversations
conversations_messages
conversations_signals
```

---

### 2️⃣ **缺失的容器服务**

对比 `docker-compose.base.yml`,Hobby 缺少以下服务:

#### ❌ 完全缺失的服务:

| 服务 | 用途 | 影响 |
|------|------|------|
| **capture-ai** | AI 事件捕获 | AI 事件无法直接采集 |
| **kafka_ui** | Kafka 管理界面 | 无法可视化管理 Topic |
| **flower** | Celery 监控 | 无法监控 Worker 任务 |
| **maildev** | 邮件测试 | 开发测试邮件困难 |
| **opensearch** | LLM 搜索 | AI 日志无法检索 |
| **opensearch-dashboards** | OpenSearch UI | 无可视化界面 |
| **otel-collector** | OpenTelemetry 收集 | 无可观测性数据 |
| **jaeger** | 分布式追踪 | 无法追踪调用链 |
| **dynamodb** | Session 加密 | 录制加密失败 |
| **duckgres** | 数据仓库 | Data Warehouse 不可用 |
| **usage-ingestion** | 用量统计 | 无法统计使用量 |

---

### 3️⃣ **环境变量不完整**

#### Worker 容器缺失的关键环境变量:

```yaml
# 当前 Hobby 的 worker 环境变量 (仅部分)
environment:
  SITE_URL: https://$DOMAIN
  SECRET_KEY: $POSTHOG_SECRET
  # ... 存储配置 ...
  ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
  OPENAI_API_KEY: ${OPENAI_API_KEY:-}

# ❌ 缺失的重要环境变量:
```

**AI 相关 (新增的国内提供商)**:
```bash
GLM_API_KEY=${GLM_API_KEY:-}
GLM_BASE_URL=${GLM_BASE_URL:-}
QWEN_API_KEY=${QWEN_API_KEY:-}
QWEN_BASE_URL=${QWEN_BASE_URL:-}
MIMO_API_KEY=${MIMO_API_KEY:-}
MIMO_BASE_URL=${MIMO_BASE_URL:-}
CUSTOM_LLM_API_KEY=${CUSTOM_LLM_API_KEY:-}
CUSTOM_LLM_BASE_URL=${CUSTOM_LLM_BASE_URL:-}
```

**LLM Gateway 相关**:
```bash
LLM_GATEWAY_URL=http://llm-gateway:3308
LLM_GATEWAY_API_KEY=${LLM_GATEWAY_API_KEY:-}
```

**AI Observability 相关**:
```bash
AI_OBSERVABILITY_OPENSEARCH_HOST=http://opensearch:9200
AI_OBSERVABILITY_OPENSEARCH_INDEX=posthog-ai-traces
```

**Data Warehouse 相关**:
```bash
DUCKGRES_API_URL=http://duckgres:8000
DUCKGRES_INTERNAL_SECRET=${DUCKGRES_SECRET:-}
MANAGED_WAREHOUSE_ENABLED=true
```

**CDP/Destinations 相关**:
```bash
CDP_REDIS_HOST=redis7
CDP_REDIS_PORT=6379
CDP_VALKEY_HOST=valkey
CDP_VALKEY_PORT=6379
DESTINATION_WEBHOOK_TIMEOUT=30000
```

**Batch Exports 相关**:
```bash
BATCH_EXPORT_S3_ENDPOINT=http://objectstorage:19000
BATCH_EXPORT_S3_ACCESS_KEY_ID=object_storage_root_user
BATCH_EXPORT_S3_SECRET_ACCESS_KEY=object_storage_root_password
BATCH_EXPORT_REDSHIFT_ENABLED=true
BATCH_EXPORT_BIGQUERY_ENABLED=true
BATCH_EXPORT_SNOWFLAKE_ENABLED=true
```

**Alerts & Workflows 相关**:
```bash
WORKFLOWS_EMAIL_ENABLED=true
WORKFLOWS_EMAIL_FROM=noreply@${DOMAIN}
WORKFLOWS_EMAIL_SMTP_HOST=maildev
WORKFLOWS_EMAIL_SMTP_PORT=1025
ALERTS_CHECK_INTERVAL_SECONDS=60
```

**Feature Flags 高级配置**:
```bash
FLAGS_REDIS_ENABLED=true
FLAGS_REDIS_URL=redis://redis7:6379/1
COOKIELESS_REDIS_HOST=redis7
COOKIELESS_REDIS_PORT=6379
```

**Sessions/Replay 高级配置**:
```bash
SESSION_RECORDING_V2_ENABLED=true
SESSION_RECORDING_V2_S3_BUCKET=posthog
SESSION_RECORDING_ENCRYPTION_ENABLED=true
SESSION_RECORDING_DYNAMODB_TABLE=session-recording-keys
```

**Error Tracking 相关**:
```bash
ERROR_TRACKING_SYMBOL_UPLOAD_ENABLED=true
ERROR_TRACKING_SOURCE_MAPS_ENABLED=true
```

---

### 4️⃣ **Celery 队列配置不完整**

当前 Hobby 的 Worker 只监听默认队列,缺少:

```python
# ❌ 缺失的 Celery 队列
CELERY_QUEUES = [
    'default',
    'celery',
    
    # AI 相关
    'ai_task_queue',
    'ai_feedback_queue',
    'ai_evaluation_queue',
    
    # Data Warehouse
    'data_warehouse_queue',
    'external_data_queue',
    
    # Batch Exports
    'batch_exports_queue',
    
    # Alerts & Workflows
    'alerts_queue',
    'workflows_email_queue',
    'workflows_batch_queue',
    
    # CDP
    'cdp_queue',
    'cdp_function_queue',
    
    # Experiments
    'experiments_queue',
    
    # Surveys
    'surveys_queue',
    
    # Error Tracking
    'error_tracking_queue',
    
    # Feature Flags
    'feature_flags_queue',
    
    # Usage & Billing
    'usage_reports_queue',
    'billing_queue',
    
    # Data Modeling
    'data_modeling_queue',
    
    # Conversations
    'conversations_queue',
]
```

---

### 5️⃣ **缺少的环境变量 (.env.services)**

Hobby 部署没有正确加载 `.env.services` 文件中的配置:

```bash
# 应该在 docker-compose.hobby.yml 中添加
env_file: .env.services
```

---

## 🛠️ 改进方案

我将分步骤补充这些缺失的配置:

### 第一步:补充 Kafka Topics
### 第二步:添加缺失的容器服务
### 第三步:完善环境变量
### 第四步:配置 Celery 队列
### 第五步:更新 .env 配置

---

## 📝 下一步行动

你希望我先从哪个方面开始补充?我建议按以下优先级:

1. **高优先级**: AI 相关 (capture-ai, 环境变量, LLM Gateway)
2. **高优先级**: 补充 Kafka Topics (影响数据流)
3. **中优先级**: Celery 队列配置 (影响任务处理)
4. **中优先级**: Data Warehouse (duckgres, external data)
5. **低优先级**: 监控和可观测性 (opensearch, otel, jaeger)
6. **低优先级**: 开发工具 (kafka_ui, flower, maildev)

请告诉我你想先从哪些开始,或者我可以全部一起补充!
