#!/bin/bash
# ==================== 一键启动 PostHog 服务 ====================
# 用法: ./deploy.sh [镜像名称]
# 示例: ./deploy.sh myusername/posthog-custom:latest

set -e

# 配置
IMAGE_NAME="${1:-}"

echo "=========================================="
echo "🚀 部署 PostHog 自定义服务"
echo "=========================================="
echo ""

# 检查 Docker
if ! command -v docker &> /dev/null; then
    echo "❌ 错误: 未找到 Docker"
    echo ""
    echo "安装 Docker:"
    echo "  curl -fsSL https://get.docker.com | sh"
    exit 1
fi

# 检查 docker-compose
if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null 2>&1; then
    echo "❌ 错误: 未找到 docker-compose"
    echo ""
    echo "安装 docker-compose:"
    echo "  sudo apt-get install docker-compose-plugin"
    exit 1
fi

# 检查 .env 文件
if [ ! -f ".env" ]; then
    echo "⚠️  未找到 .env 文件"
    echo ""
    echo "从模板创建: cp .env.custom.example .env"
    echo "然后编辑 .env 配置 AI 密钥"
    echo ""
    read -p "是否使用默认配置? (y/N): " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        exit 1
    fi
    cp .env.custom.example .env
    echo "已创建 .env,请编辑后重新运行此脚本"
    exit 0
fi

# 准备 docker-compose
if [ ! -f "docker-compose.custom.yml" ]; then
    echo "📝 生成 docker-compose.custom.yml..."
    cp docker-compose.hobby.yml docker-compose.custom.yml
    
    # 如果有自定义镜像,替换默认镜像
    if [ -n "$IMAGE_NAME" ]; then
        echo "使用镜像: ${IMAGE_NAME}"
        sed -i.bak "s|posthog/posthog:.*|${IMAGE_NAME}|g" docker-compose.custom.yml
        rm -f docker-compose.custom.yml.bak
    fi
fi

echo ""
echo "📊 当前配置:"
echo "  镜像: ${IMAGE_NAME:-posthog/posthog:latest}"
echo "  环境: .env"
echo "  编排: docker-compose.custom.yml"
echo ""

# 检查是否已在运行
if docker-compose -f docker-compose.custom.yml ps 2>/dev/null | grep -q "Up"; then
    echo "⚠️  检测到服务已在运行"
    echo ""
    read -p "是否停止旧服务? (y/N): " confirm
    if [[ "$confirm" == "y" || "$confirm" == "Y" ]]; then
        echo "🛑 停止旧服务..."
        docker-compose -f docker-compose.custom.yml down
    fi
fi

# 拉取镜像 (如果使用远程镜像)
if [ -n "$IMAGE_NAME" ]; then
    echo "📥 拉取镜像..."
    docker pull ${IMAGE_NAME} || echo "⚠️  拉取失败,尝试使用本地镜像"
fi

echo ""
echo "🚀 启动服务..."
docker-compose -f docker-compose.custom.yml up -d

echo ""
echo "⏳ 等待服务就绪..."
sleep 10

# 检查服务状态
echo ""
echo "📊 服务状态:"
docker-compose -f docker-compose.custom.yml ps

echo ""
echo "📝 查看日志:"
echo "  docker-compose -f docker-compose.custom.yml logs -f"
echo ""
echo "🔍 验证部署:"
echo "  python quick_verify.py"
echo ""
echo "🌐 访问 PostHog:"
echo "  http://your-server-ip:8000"
echo ""

# 等待 Web 服务就绪
echo "⏳ 等待 Web 服务就绪..."
for i in {1..30}; do
    if curl -s http://localhost:8000 > /dev/null 2>&1; then
        echo "✅ Web 服务已就绪!"
        echo ""
        echo "访问: http://localhost:8000"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "⚠️  Web 服务启动较慢,请查看日志:"
        echo "  docker-compose -f docker-compose.custom.yml logs web"
    fi
    sleep 5
done

echo ""
echo "=========================================="
echo "🎉 部署完成!"
echo "=========================================="
echo ""
echo "常用命令:"
echo "  查看状态:   docker-compose -f docker-compose.custom.yml ps"
echo "  查看日志:   docker-compose -f docker-compose.custom.yml logs -f"
echo "  停止服务:   docker-compose -f docker-compose.custom.yml down"
echo "  重启服务:   docker-compose -f docker-compose.custom.yml restart"
echo "  更新服务:   ./deploy.sh ${IMAGE_NAME}"
echo ""
