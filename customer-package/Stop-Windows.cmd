@echo off
cd /d "%~dp0"
docker compose -f compose.yml down
if errorlevel 1 pause

