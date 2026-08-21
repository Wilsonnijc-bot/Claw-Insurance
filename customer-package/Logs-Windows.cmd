@echo off
cd /d "%~dp0"
docker compose -f compose.yml logs --tail 200
pause

