@echo off
title Gerar ControleCasa.exe
cd /d "%~dp0"
python -m pip install --quiet --disable-pip-version-check pyinstaller==6.22.3 -r requirements.txt
python -m PyInstaller --noconfirm --onefile --noconsole --name ControleCasa --icon casa.ico --hidden-import positivo ^
  --add-data "index.html;." --add-data "icone.png;." --add-data "casa.ico;." main.py
echo.
echo Pronto! O programa esta em: %~dp0dist\ControleCasa.exe
echo Pode copiar esse .exe para onde quiser (ex.: Area de Trabalho).
pause
