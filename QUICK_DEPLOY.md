# PostHog 自定义部署 - 快速指南

## 📋 完整流程

```
GitHub → 下载代码 → 编译镜像 → 推送镜像 → 启动服务
```

---

## 🚀 在云服务器上部署

### 步骤 1: 下载代码

```bash
# 克隆你的 fork
git clone https://github.com/DWg0917/posthog.git
cd posthog

# 切换到自定义分支
git checkout feat/custom-posthog
```

---

### 步骤 2: 编译镜像

```bash
# 运行一键构建脚本
./build-image.sh 你的DockerHub用户名 latest
```

**示例**:
```bash
./build-image.sh myusername latest
```

**这个过程会**:
1. ✅ 检查 Docker 环境
2. ✅ 构建包含你代码改动的镜像 (10-30分钟)
3. ✅ 自动推送到 Docker Hub
4. ✅ 显示镜像信息

**构建完成后你会看到**:
```
✅ 镜像构建成功!
✅ 镜像推送成功!
镜像地址: myusername/posthog-custom:latest
```

---

### 步骤 3: 启动服务

```bash
# 1. 配置环境变量
cp .env.custom.example .env
vim .env  # 编辑 AI 密钥等配置

# 2. 一键启动
./deploy.sh myusername/posthog-custom:latest
```

**这个过程会**:
1. ✅ 检查 Docker 和 docker-compose
2. ✅ 检查 .env 配置
3. ✅ 拉取你的镜像
4. ✅ 启动所有服务 (PostgreSQL, Redis, ClickHouse, Kafka, Web, Worker 等)
5. ✅ 等待 Web 服务就绪
6. ✅ 显示访问地址

---

## 🎯 完整示例

```bash
# SSH 到你的云服务器
ssh user@your-server

# 1. 下载代码
git clone https://github.com/DWg0917/posthog.git
cd posthog
git checkout feat/custom-posthog

# 2. 编译镜像 (首次需要 10-30 分钟)
./build-image.sh myusername latest

# 3. 配置环境变量
cp .env.custom.example .env
vim .env
# 至少配置一个 AI 提供商:
# GLM_API_KEY=your-key
# GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4

# 4. 启动服务
./deploy.sh myusername/posthog-custom:latest

# 5. 访问 PostHog
# 浏览器打开: http://your-server-ip:8000
```

---

## 🔍 验证部署

```bash
# 检查服务状态
docker-compose -f docker-compose.custom.yml ps

# 查看日志
docker-compose -f docker-compose.custom.yml logs -f

# 运行验证脚本
python quick_verify.py

# 测试 AI 功能
# 访问 Web UI,使用 AI 助手
```

---

## 📊 服务架构

启动后的服务包括:

| 服务 | 用途 | 端口 |
|------|------|------|
| web | Django Web 应用 | 8000 |
| worker | Celery 后台任务 | - |
| postgresql | 关系数据库 | 5432 |
| redis | 缓存/消息队列 | 6379 |
| clickhouse | 分析数据库 | 8123, 9000 |
| kafka | 消息队列 | 9092 |
| zookeeper | Kafka 依赖 | 2181 |
| objectstorage | 对象存储 | 19000 |
| capture | 事件捕获 | 4000 |
| capture-ai | AI 事件捕获 | - |
| temporal | 工作流引擎 | 7233 |
| ... | 其他服务 | ... |

---

## ⚙️ 常用命令

```bash
# 查看服务状态
docker-compose -f docker-compose.custom.yml ps

# 查看实时日志
docker-compose -f docker-compose.custom.yml logs -f

# 查看特定服务日志
docker-compose -f docker-compose.custom.yml logs -f web
docker-compose -f docker-compose.custom.yml logs -f worker

# 停止服务
docker-compose -f docker-compose.custom.yml down

# 重启服务
docker-compose -f docker-compose.custom.yml restart

# 更新镜像后重新部署
./deploy.sh myusername/posthog-custom:latest

# 进入容器调试
docker-compose -f docker-compose.custom.yml exec web bash
```

---

## 🔧 配置说明

### 最小配置 (.env)

```env
# 域名或 IP
DOMAIN=your-server-ip

# AI 提供商 (至少配置一个)

# 智谱 GLM
GLM_API_KEY=your-glm-api-key
GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4

# 或通义千问
QWEN_API_KEY=your-qwen-api-key
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# 或自定义 LLM
CUSTOM_LLM_API_KEY=your-api-key
CUSTOM_LLM_BASE_URL=https://your-llm-endpoint.com/v1
```

### 完整配置

参考 `.env.custom.example` 文件,包含:
- 数据库配置
- Redis 配置
- ClickHouse 配置
- Kafka 配置
- AI 提供商配置
- 对象存储配置
- 性能调优配置

---

## ⚠️ 注意事项

### 服务器要求

- **内存**: 至少 8GB (推荐 16GB+)
- **CPU**: 至少 4 核
- **磁盘**: 至少 50GB
- **系统**: Ubuntu 22.04+ 或其他 Linux

### 端口占用

确保以下端口**未被占用**:
- 8000 (Web)
- 5432 (PostgreSQL)
- 6379 (Redis)
- 8123 (ClickHouse HTTP)
- 9000 (ClickHouse TCP)
- 9092 (Kafka)

### 首次启动

- 首次启动需要 **2-5 分钟** 完成数据库迁移
- 可以通过日志查看进度: `docker-compose -f docker-compose.custom.yml logs -f web`
- 看到 `Django version x.x.x` 和 `Starting development server at http://0.0.0.0:8000` 表示就绪

---

## 🆘 故障排除

### 问题 1: 构建镜像失败

```bash
# 检查 Docker
docker info

# 检查磁盘空间
df -h

# 检查内存
free -h

# 清理旧镜像
docker system prune -a
```

### 问题 2: 服务启动失败

```bash
# 查看错误日志
docker-compose -f docker-compose.custom.yml logs web
docker-compose -f docker-compose.custom.yml logs worker

# 常见原因:
# - 端口冲突: 修改 docker-compose.custom.yml 中的端口映射
# - 内存不足: 至少需要 8GB
# - .env 配置错误: 检查环境变量格式
```

### 问题 3: AI 功能不工作

```bash
# 检查 AI 环境变量
docker-compose -f docker-compose.custom.yml exec web env | grep -E "GLM|QWEN|MIMO"

# 查看 worker 日志
docker-compose -f docker-compose.custom.yml logs worker | grep -i "llm\|api"

# 测试 AI API
curl -H "Authorization: Bearer $GLM_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"model": "glm-4", "messages": [{"role": "user", "content": "Hello"}]}' \
     $GLM_BASE_URL/chat/completions
```

---

## 📚 相关文档

- [CODE_REVIEW_CHECKLIST.md](./CODE_REVIEW_CHECKLIST.md) - 代码审查清单
- [DEPLOYMENT_STEPS.md](./DEPLOYMENT_STEPS.md) - 详细部署指南
- [CUSTOM_DEPLOYMENT_GUIDE.md](./CUSTOM_DEPLOYMENT_GUIDE.md) - 配置指南
- [CHANGES_SUMMARY.md](./CHANGES_SUMMARY.md) - 改动总结

---

**部署完成后可以访问**:
- ✅ 所有企业级功能 (SSO, RBAC, 白标)
- ✅ 国内 AI 提供商支持 (智谱 GLM, 通义千问, 小米 MiMo)
- ✅ 无限用户数
- ✅ 100 年许可证有效期
