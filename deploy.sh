#!/bin/bash
# deploy.sh - KeywordIQ 部署脚本

set -e

echo "🚀 开始部署KeywordIQ..."

# 1. 环境检查
echo "1️⃣ 检查环境..."
if ! command -v docker &> /dev/null; then
    echo "❌ Docker未安装，请先安装Docker"
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose未安装，请先安装Docker Compose"
    exit 1
fi

# 2. 清理旧容器
echo "2️⃣ 清理旧容器..."
docker-compose down --remove-orphans || true
# 2. 清理旧容器
echo "2️⃣ 清理旧容器..."
docker-compose down --remove-orphans || true

# 3. 创建必要目录
echo "3️⃣ 创建目录..."
mkdir -p uploads/daily uploads/weekly logs static

# 4. 复制环境配置
echo "4️⃣ 配置环境..."
if [ ! -f .env ]; then
    cp .env.production .env
    echo "⚠️  请修改.env中的数据库密码和密钥"
fi

# 5. 构建镜像
echo "5️⃣ 构建应用镜像..."
docker-compose build --no-cache

# 6. 启动服务
echo "6️⃣ 启动服务..."
docker-compose up -d

# 7. 等待服务启动
echo "7️⃣ 等待服务启动..."
sleep 10

# 8. 健康检查
#echo "8️⃣ 健康检查..."
#for i in {1..30}; do
#    if curl -f http://localhost:8000/health > /dev/null 2>&1; then
#        echo "✅ 服务启动成功！"
#        echo "📱 访问地址: http://localhost:8000"
#        echo "👤 默认账号: admin / admin"
#        docker-compose ps
#        exit 0
#    fi
#    echo "等待服务启动... ($i/30)"
#    sleep 2
#done

#echo "❌ 服务启动超时，请检查日志:"
docker-compose logs app
exit 1
