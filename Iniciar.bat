@echo off
title Controle Casa
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python nao encontrado. Instale em https://www.python.org/downloads/ marcando "Add Python to PATH".
  pause
  exit /b
)
echo Preparando o Controle Casa (so demora na primeira vez)...
python -m pip install --quiet --disable-pip-version-check -r requirements.txt
start "" pythonw main.py
exit
