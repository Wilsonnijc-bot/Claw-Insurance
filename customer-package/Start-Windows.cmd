@echo off
cd /d "%~dp0"
docker compose -f compose.yml up -d
if errorlevel 1 pause

