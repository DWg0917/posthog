#!/bin/bash
# ==================== 一键构建 Docker 镜像 ====================
# 用法: ./build-image.sh [用户名] [标签]
# 示例: ./build-image.sh myusername latest

set -e

# 配置
DOCKER_USERNAME="${1:-your-dockerhub-username}"
TAG="${2:-latest}"
IMAGE_NAME="${DOCKER_USERNAME}/posthog-custom"

echo "=========================================="
echo "🔨 构建 PostHog 自定义镜像"
echo "=========================================="
echo ""
echo "镜像: ${IMAGE_NAME}:${TAG}"
echo "分支: $(git branch --show-current)"
echo "提交: $(git log --oneline -1)"
echo ""

# 检查 Docker
if ! command -v docker &> /dev/null; then
    echo "❌ 错误: 未找到 Docker"
    echo ""
    echo "安装 Docker:"
    echo "  curl -fsSL https://get.docker.com | sh"
    echo "  sudo systemctl enable docker"
    echo "  sudo systemctl start docker"
    exit 1
fi

# 检查 Docker 登录
if ! docker info 2>/dev/null | grep -q "Username"; then
    echo "⚠️  未登录 Docker Hub"
    echo ""
    echo "请先登录:"
    echo "  docker login"
    echo ""
    read -p "是否继续? (y/N): " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        exit 0
    fi
fi

echo "📦 开始构建镜像..."
echo "这可能需要 10-30 分钟,取决于网络和机器性能..."
echo ""

# 构建镜像
docker build \
    -t ${IMAGE_NAME}:${TAG} \
    -f Dockerfile \
    --target posthog \
    --build-arg COMMIT_HASH=$(git rev-parse HEAD) \
    --progress=plain \
    .

echo ""
echo "✅ 镜像构建成功!"
echo ""

# 显示镜像信息
echo "📊 镜像信息:"
docker images | grep ${IMAGE_NAME}
echo ""

# 推送镜像
echo "📤 推送镜像到 Docker Hub..."
docker push ${IMAGE_NAME}:${TAG}

echo ""
echo "✅ 镜像推送成功!"
echo ""
echo "=========================================="
echo "🎉 完成!"
echo "=========================================="
echo ""
echo "镜像地址: ${IMAGE_NAME}:${TAG}"
echo ""
echo "下一步: 运行 deploy.sh 启动服务"
echo "  ./deploy.sh ${IMAGE_NAME}:${TAG}"
echo ""
