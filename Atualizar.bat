@echo off
title Atualizar Controle Casa
cd /d "%~dp0"
echo Atualizando o Controle Casa... (suas configuracoes sao mantidas)
echo Fechando a versao que esta aberta...
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Method Post -ContentType 'application/json' -Body '{}' -Headers @{'X-Controle-Casa'='1'} http://127.0.0.1:8765/api/quit | Out-Null } catch {}"
timeout /t 2 /nobreak >nul
python -m pip install --quiet --disable-pip-version-check --upgrade -r requirements.txt
start "" pythonw main.py
exit
