@echo off
rem Arranca el bot y el panel web, cada uno en su propia ventana.
rem Si alguno se corta (por ejemplo, al prender la PC todavia no hay internet), vuelve a arrancar solo.
rem Para apagarlos, cerra sus ventanas.
chcp 65001 >nul
cd /d "%~dp0"

if "%~1"=="bot" goto bot
if "%~1"=="panel" goto panel

start "Bot de recorridas" cmd /c ""%~f0" bot"
start "Panel web" cmd /c ""%~f0" panel"
exit /b

:bot
title Bot de recorridas
echo Arrancando el bot... (no cierres esta ventana)
.venv\Scripts\python.exe -m bot.bot
echo.
echo El bot se detuvo. Vuelve a arrancar en 15 segundos. Para apagarlo, cerra esta ventana.
timeout /t 15 /nobreak >nul
goto bot

:panel
title Panel web
echo Arrancando el panel web... (no cierres esta ventana)
.venv\Scripts\python.exe -m streamlit run panel.py
echo.
echo El panel se detuvo. Vuelve a arrancar en 15 segundos. Para apagarlo, cerra esta ventana.
timeout /t 15 /nobreak >nul
goto panel
