#!/bin/bash
set -e

# ==================== 配置 ====================
# 修改为你的 Docker Hub 用户名
DOCKER_USERNAME="your-dockerhub-username"
# 或者使用 GitHub Container Registry
# DOCKER_REGISTRY="ghcr.io"
# DOCKER_USERNAME="your-github-username"

IMAGE_NAME="${DOCKER_USERNAME}/posthog-custom"
TAG="latest"

echo "=========================================="
echo "🚀 PostHog 自定义部署 - 构建和推送"
echo "=========================================="
echo ""
echo "镜像名称: ${IMAGE_NAME}:${TAG}"
echo ""

# ==================== 步骤 1: 构建镜像 ====================
echo "📦 步骤 1/3: 构建 Docker 镜像"
echo "这可能需要 10-30 分钟,取决于网络..."
echo ""

docker build \
    -t ${IMAGE_NAME}:${TAG} \
    -f Dockerfile \
    --target posthog \
    --build-arg COMMIT_HASH=$(git rev-parse HEAD) \
    .

echo ""
echo "✅ 镜像构建完成!"
echo ""

# ==================== 步骤 2: 推送到 Docker Registry ====================
echo "📤 步骤 2/3: 推送镜像到 Docker Registry"
echo ""

# 登录提示
if ! docker info 2>/dev/null | grep -q "Username"; then
    echo "⚠️  请先登录 Docker Hub:"
    echo "   docker login"
    echo ""
    echo "或者使用 GitHub Container Registry:"
    echo "   echo \$GITHUB_TOKEN | docker login ghcr.io -u \$GITHUB_USERNAME --password-stdin"
    echo ""
    read -p "是否继续推送? (y/N): " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        echo "取消推送"
        exit 0
    fi
fi

docker push ${IMAGE_NAME}:${TAG}

echo ""
echo "✅ 镜像推送完成!"
echo ""

# ==================== 步骤 3: 生成部署配置 ====================
echo "📝 步骤 3/3: 生成部署配置"
echo ""

# 创建部署目录
DEPLOY_DIR="deploy-custom"
mkdir -p ${DEPLOY_DIR}

# 复制 docker-compose 文件
cp docker-compose.hobby.yml ${DEPLOY_DIR}/docker-compose.yml

# 更新镜像名称
sed -i.bak "s|posthog/posthog:.*|${IMAGE_NAME}:${TAG}|g" ${DEPLOY_DIR}/docker-compose.yml
rm -f ${DEPLOY_DIR}/docker-compose.yml.bak

# 复制环境变量示例
cp .env.custom.example ${DEPLOY_DIR}/.env

# 创建部署说明
cat > ${DEPLOY_DIR}/README.md << 'EOF'
# PostHog 自定义部署

## 快速开始

1. 编辑 `.env` 文件,配置你的域名和 AI 密钥
2. 启动服务: `docker-compose up -d`
3. 访问: `http://your-domain:8000`

## 已解锁功能

- ✅ 所有企业级功能 (SSO, RBAC, 白标)
- ✅ 国内 AI 提供商支持 (智谱 GLM, 通义千问, 小米 MiMo)
- ✅ 无限用户数
- ✅ 100 年许可证有效期

## AI 配置示例

### 智谱 GLM (推荐)
```env
GLM_API_KEY=your-glm-api-key
GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
```

### 通义千问
```env
QWEN_API_KEY=your-qwen-api-key
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

## 云服务器部署

### Ubuntu 22.04+

```bash
# 1. 安装 Docker
curl -fsSL https://get.docker.com | sh

# 2. 启动 Docker
sudo systemctl enable docker
sudo systemctl start docker

# 3. 上传部署文件
scp -r deploy-custom user@your-server:/opt/posthog

# 4. SSH 到服务器
ssh user@your-server

# 5. 进入部署目录
cd /opt/posthog/deploy-custom

# 6. 编辑 .env 配置
vim .env

# 7. 启动服务
docker-compose up -d

# 8. 查看日志
docker-compose logs -f
```

## 验证部署

```bash
# 检查服务状态
docker-compose ps

# 运行验证脚本
python verify_custom_deployment.py

# 访问 Web UI
# http://your-server-ip:8000
```
EOF

# 复制验证脚本
cp quick_verify.py ${DEPLOY_DIR}/
cp verify_custom_deployment.py ${DEPLOY_DIR}/

echo "✅ 部署文件已生成到 ${DEPLOY_DIR}/ 目录"
echo ""

# ==================== 完成 ====================
echo "=========================================="
echo "🎉 完成!"
echo "=========================================="
echo ""
echo "下一步:"
echo "  1. 进入部署目录: cd ${DEPLOY_DIR}"
echo "  2. 编辑 .env 文件配置 AI 密钥"
echo "  3. 上传到云服务器并运行"
echo ""
echo "云服务器部署命令:"
echo "  scp -r ${DEPLOY_DIR} user@your-server:/opt/posthog"
echo "  ssh user@your-server"
echo "  cd /opt/posthog/${DEPLOY_DIR}"
echo "  docker-compose up -d"
echo ""
