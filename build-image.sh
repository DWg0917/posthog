#!/bin/bash
# ==================== 构建 Docker 镜像 (本地) ====================
# 用法: ./build-image.sh [镜像名] [标签] [--push]
# 示例: ./build-image.sh posthog-custom latest          # 只构建,不推送
#        ./build-image.sh myusername/posthog-custom latest --push  # 构建并推送

set -e

# 配置
IMAGE_NAME="${1:-posthog-custom}"
TAG="${2:-latest}"
PUSH="${3:-}"

echo "=========================================="
echo "🔨 构建 PostHog 自定义镜像"
echo "=========================================="
echo ""
echo "镜像: ${IMAGE_NAME}:${TAG}"
echo "分支: $(git branch --show-current)"
echo "提交: $(git log --oneline -1)"
echo "操作: $([ "$PUSH" = "--push" ] && echo '构建并推送' || echo '只构建,不推送')"
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

# 检查 Docker 登录 (只在 --push 时需要)
if [ "$PUSH" = "--push" ]; then
    if ! docker info 2>/dev/null | grep -q "Username"; then
        echo "❌ 错误: 未登录 Docker Hub"
        echo ""
        echo "请先登录:"
        echo "  docker login"
        echo ""
        exit 1
    fi
fi

echo "📦 开始构建镜像..."
echo "这可能需要 10-30 分钟,取决于网络和机器性能..."
echo ""

# 构建镜像 (不指定 target,构建完整的最终镜像)
docker build \
    --no-cache \
    -t ${IMAGE_NAME}:${TAG} \
    -f Dockerfile \
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
echo "💡 提示:"
echo "  - 先在本地测试,确认功能正常后再推送"
echo "  - 运行服务: ./deploy.sh ${IMAGE_NAME}:${TAG}"
echo "  - 推送到云端: ./build-image.sh ${IMAGE_NAME} ${TAG} --push"
echo ""

# 可选: 推送镜像
if [ "$PUSH" = "--push" ]; then
    echo "📤 推送镜像到云端..."
    
    # 检查 Docker 登录
    if ! docker info 2>/dev/null | grep -q "Username"; then
        echo "❌ 错误: 未登录 Docker Hub"
        echo ""
        echo "请先登录:"
        echo "  docker login"
        echo ""
        exit 1
    fi
    
    docker push ${IMAGE_NAME}:${TAG}
    
    echo ""
    echo "✅ 镜像推送成功!"
    echo ""
    echo "镜像地址: ${IMAGE_NAME}:${TAG}"
else
    echo "⏭️  跳过推送 (镜像已保存在本地)"
    echo ""
    echo "如需推送,运行:"
    echo "  ./build-image.sh ${IMAGE_NAME} ${TAG} --push"
fi
