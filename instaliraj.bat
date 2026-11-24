@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Košnica Nadzor - instalacija
echo Instaliram Košnicu Nadzor (traje 1-3 minuta)...
if not exist data\logs mkdir data\logs
taskkill /im go2rtc.exe /f >nul 2>&1
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv > data\logs\instalacija.txt 2>&1 || python -m venv .venv > data\logs\instalacija.txt 2>&1
)
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements.txt >> data\logs\instalacija.txt 2>&1
if errorlevel 1 (
  echo GRESKA pri instalaciji, detalji u data\logs\instalacija.txt
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0precice.ps1" >> data\logs\instalacija.txt 2>&1
echo Gotovo. Pokrecem...
start "" ".venv\Scripts\pythonw.exe" "%~dp0nadzor.pyw"
timeout /t 3 >nul
