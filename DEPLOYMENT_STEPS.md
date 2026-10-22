# PostHog 自定义部署 - 完整指南

## 📋 概述

本指南说明如何:
1. 构建包含代码改动的 Docker 镜像
2. 推送到 Docker Registry
3. 在云服务器部署

---

## 🔧 前置条件

### 本地环境
- ✅ Docker (已安装)
- ✅ Git (已配置)
- ✅ 你的 GitHub 账号

### 云服务器 (Ubuntu 22.04+)
- Docker + Docker Compose
- 至少 8GB 内存, 4核 CPU
- 公开端口 8000 (或配置域名 + HTTPS)

---

## 📦 步骤 1: 推送代码到 GitHub

### 选项 A: 使用 IDE 推送

在 CLion 中:
1. 右键项目 → Git → Commit
2. 填写提交信息
3. 点击 "Commit and Push"
4. 选择你的 fork: `github-dwg`

### 选项 B: 使用命令行

```bash
cd /Users/diweigao/Desktop/ClionProject/posthog

# 1. 添加所有改动
git add -A

# 2. 提交
git commit -m "feat(custom-deployment): unlock enterprise features and add Chinese AI providers"

# 3. 推送到你的 fork
git push github-dwg feat/custom-posthog
```

**注意**: 如果提示 token 权限问题,需要重新生成 GitHub Personal Access Token:
- 访问: https://github.com/settings/tokens
- 生成新 token,勾选权限:
  - ✅ `repo` (完整仓库访问)
  - ✅ `workflow` (GitHub Actions)
- 使用新 token 推送

---

## 🐳 步骤 2: 构建 Docker 镜像

### 2.1 选择镜像仓库

**选项 1: Docker Hub (推荐,最简单)**

```bash
# 登录 Docker Hub
docker login

# 输入你的 Docker Hub 用户名和密码
```

**选项 2: GitHub Container Registry (ghcr.io)**

```bash
# 使用 GitHub Personal Access Token
echo YOUR_GITHUB_TOKEN | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
```

### 2.2 运行构建脚本

```bash
cd /Users/diweigao/Desktop/ClionProject/posthog

# 编辑脚本,修改 DOCKER_USERNAME
vim build-and-push.sh

# 运行构建
./build-and-push.sh
```

**手动构建** (如果想自己控制):

```bash
# 1. 构建镜像 (约 10-30 分钟)
docker build \
    -t your-dockerhub-username/posthog-custom:latest \
    -f Dockerfile \
    --target posthog \
    --build-arg COMMIT_HASH=$(git rev-parse HEAD) \
    .

# 2. 推送到 Docker Hub
docker push your-dockerhub-username/posthog-custom:latest
```

**构建说明**:
- `--target posthog`: 只构建 Python 后端 (Django)
- 完整构建还包括前端 (frontend-build),但 Hobby 部署可以分开构建
- 首次构建较慢 (需要下载所有依赖),后续构建会使用缓存

---

## 📤 步骤 3: 准备部署文件

构建脚本会自动生成 `deploy-custom/` 目录,包含:

```
deploy-custom/
├── docker-compose.yml    # 已更新为你的镜像
├── .env                  # 环境变量模板
├── quick_verify.py       # 快速验证脚本
├── verify_custom_deployment.py  # 完整验证脚本
└── README.md            # 部署说明
```

**手动准备** (如果没运行脚本):

```bash
# 1. 创建部署目录
mkdir -p deploy-custom
cd deploy-custom

# 2. 复制并修改 docker-compose
cp ../docker-compose.hobby.yml docker-compose.yml
sed -i '' 's|posthog/posthog:.*|your-dockerhub-username/posthog-custom:latest|g' docker-compose.yml

# 3. 复制环境变量
cp ../.env.custom.example .env
```

---

## ☁️ 步骤 4: 上传到云服务器

### 4.1 上传文件

```bash
# 从本地上传到服务器
scp -r deploy-custom user@your-server-ip:/opt/posthog

# 或者使用 rsync
rsync -avz deploy-custom/ user@your-server-ip:/opt/posthog/deploy-custom/
```

### 4.2 SSH 到服务器

```bash
ssh user@your-server-ip
cd /opt/posthog/deploy-custom
```

---

## ⚙️ 步骤 5: 配置环境变量

编辑 `.env` 文件:

```bash
vim .env
```

**必须配置**:

```env
# 域名 (用于 HTTPS,测试可用 IP)
DOMAIN=your-server-ip  # 或 your-domain.com

# AI 提供商 (至少配置一个)

# 智谱 GLM (推荐)
GLM_API_KEY=your-glm-api-key-here
GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4

# 通义千问
QWEN_API_KEY=your-qwen-api-key-here
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# 自定义 LLM (任何 OpenAI 兼容 API)
CUSTOM_LLM_API_KEY=your-api-key
CUSTOM_LLM_BASE_URL=https://your-llm-endpoint.com/v1
```

**获取 AI API Key**:

### 智谱 GLM
1. 注册: https://open.bigmodel.cn/
2. 进入控制台 → API Keys
3. 创建新 Key
4. 免费额度: 新用户有试用额度

### 通义千问
1. 注册: https://help.aliyun.com/zh/model-studio/
2. 开通 DashScope
3. 创建 API Key
4. 免费额度: 每月有一定免费调用量

---

## 🚀 步骤 6: 启动服务

```bash
# 启动所有服务
docker-compose up -d

# 查看启动日志
docker-compose logs -f

# 等待 2-5 分钟,直到所有服务就绪
```

**检查服务状态**:

```bash
docker-compose ps
```

应该看到所有服务显示 `Up` 状态。

---

## ✅ 步骤 7: 验证部署

### 7.1 运行验证脚本

```bash
cd /opt/posthog/deploy-custom
python quick_verify.py
```

### 7.2 访问 Web UI

打开浏览器访问:
```
http://your-server-ip:8000
```

**首次访问**:
1. 创建管理员账户
2. 创建第一个项目
3. 开始使用!

### 7.3 测试 AI 功能

1. 进入 PostHog → AI 功能页面
2. 尝试使用 AI 助手
3. 检查日志确认使用的是国内 AI 提供商

```bash
# 查看 AI 相关日志
docker-compose logs worker | grep -i "glm\|qwen\|mimo"
```

---

## 🔍 故障排除

### 问题 1: 服务启动失败

```bash
# 查看具体错误
docker-compose logs web
docker-compose logs worker

# 常见原因:
# - 端口被占用: 修改 docker-compose.yml 中的端口映射
# - 内存不足: 至少需要 8GB
# - .env 配置错误: 检查环境变量
```

### 问题 2: AI 功能不工作

```bash
# 检查 AI 环境变量
docker-compose exec web env | grep -E "GLM|QWEN|MIMO"

# 查看 AI 相关日志
docker-compose logs worker | grep -i "llm\|api"

# 测试 AI API 连接
curl -H "Authorization: Bearer $GLM_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"model": "glm-4", "messages": [{"role": "user", "content": "Hello"}]}' \
     $GLM_BASE_URL/chat/completions
```

### 问题 3: 数据库迁移失败

```bash
# 手动运行迁移
docker-compose run --rm web python manage.py migrate

# 查看迁移状态
docker-compose run --rm web python manage.py showmigrations
```

---

## 📊 性能优化 (可选)

### 增加 Worker 内存

编辑 `docker-compose.yml`:

```yaml
worker:
  deploy:
    resources:
      limits:
        memory: 16G  # 默认 8G,根据服务器配置调整
```

### 使用外部数据库

如果有独立的 PostgreSQL/ClickHouse:

```env
# .env
DATABASE_URL=postgresql://user:pass@your-db-host:5432/posthog
CLICKHOUSE_HOST=http://your-clickhouse-host:8123
```

---

## 🎯 已完成的功能解锁

| 功能 | 状态 | 说明 |
|------|------|------|
| 企业级许可证 | ✅ | 自动激活,100年有效期 |
| Cloud 权限 | ✅ | `is_cloud()` 返回 true |
| SSO/SAML | ✅ | 单点登录 |
| RBAC | ✅ | 角色权限控制 |
| 白标 | ✅ | 自定义品牌 |
| 无限用户 | ✅ | `max_users=None` |
| 智谱 GLM | ✅ | 国内 AI 支持 |
| 通义千问 | ✅ | 国内 AI 支持 |
| 小米 MiMo | ✅ | 国内 AI 支持 |
| 自定义 LLM | ✅ | 任何 OpenAI 兼容 API |

---

## 📚 相关文档

- [CUSTOM_DEPLOYMENT_GUIDE.md](./CUSTOM_DEPLOYMENT_GUIDE.md) - 详细配置指南
- [HOBBY_DEPLOYMENT_GAP_ANALYSIS.md](./HOBBY_DEPLOYMENT_GAP_ANALYSIS.md) - 功能缺失分析
- [CHANGES_SUMMARY.md](./CHANGES_SUMMARY.md) - 改动总结

---

## 🆘 获取帮助

遇到问题?

1. 检查日志: `docker-compose logs -f`
2. 运行验证脚本: `python quick_verify.py`
3. 查看文档: 上面的故障排除部分
4. 提交 Issue: 在你的 GitHub 仓库

---

**祝部署顺利! 🎉**
