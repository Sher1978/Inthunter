@echo off
chcp 65001 > nul
title LeadRADAR Local Production Engine
color 0A

echo ===================================================================
echo   🚀 LeadRADAR: Запуск локального сервера и фоновых служб
echo ===================================================================
echo   - Веб-панель JTBD:   http://localhost:8000/outreach_dashboard.html
echo   - TMA Маркетплейс:   http://localhost:8000/
echo   - Документация API:  http://localhost:8000/docs
echo ===================================================================

cd /d "c:\Sher_AI_Studio\projects\Outreach"
python -m uvicorn src.api.app:app --host 0.0.0.0 --port 8000

pause
