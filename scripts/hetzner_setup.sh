#!/bin/bash
set -e

echo "=========================================================="
echo "🚀 LeadRADAR: Автоматическая установка на Hetzner Cloud"
echo "=========================================================="

# 1. Обновление пакетов
echo "📦 Обновление системы..."
apt-get update && apt-get upgrade -y
apt-get install -y curl git ufw

# 2. Установка Docker и Docker Compose
if ! command -v docker &> /dev/null; then
    echo "🐳 Установка Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm -f get-docker.sh
fi

# 3. Настройка рабочей папки проекта
PROJECT_DIR="/opt/leadradar"
if [ -d "$PROJECT_DIR" ]; then
    echo "🔄 Проект уже существует, обновляем репозиторий..."
    cd "$PROJECT_DIR"
    git pull origin main
else
    echo "📥 Клонирование репозитория..."
    git clone https://github.com/Sher1978/Inthunter.git "$PROJECT_DIR"
    cd "$PROJECT_DIR"
fi

# 4. Настройка файрвола (UFW)
echo "🛡️ Настройка портов файрвола..."
ufw allow 22/tcp || true
ufw allow 80/tcp || true
ufw allow 443/tcp || true
ufw allow 8000/tcp || true
ufw --force enable || true

# 5. Проверка наличия .env
if [ ! -f .env ]; then
    echo "⚠️ Файл .env не найден! Пожалуйста, создайте .env файл в $PROJECT_DIR перед запуском."
fi

# 6. Сборка и запуск контейнеров
echo "🏗️ Сборка и запуск контейнеров через Docker Compose..."
docker compose down || true
docker compose up -d --build

echo "=========================================================="
echo "✅ LeadRADAR успешно развернут и запущен!"
echo "👉 Веб-панель доступна по адресу: http://$(curl -s ifconfig.me):8000/outreach_dashboard.html"
echo "=========================================================="
