@echo off
rem Opens the Stage Helper page as soon as Live has loaded Stage Helper.
rem Double-click it before or after starting Live: it waits for Live.
set PORT=8517
title Stage Helper
echo Waiting for Ableton Live to load Stage Helper...
echo (You can start Live before or after this. Loading can take a minute.)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$end = (Get-Date).AddMinutes(10); while ((Get-Date) -lt $end) { $c = New-Object Net.Sockets.TcpClient; try { $c.Connect('127.0.0.1', %PORT%); $c.Close(); exit 0 } catch { $c.Close(); Start-Sleep -Milliseconds 500 } }; exit 1"

if errorlevel 1 (
  echo.
  echo Stage Helper did not start within 10 minutes. Check that Live is open
  echo and that StageHelper is chosen as a Control Surface in
  echo Options - Preferences - Link, Tempo ^& MIDI.
  pause
  exit /b 1
)

echo Stage Helper is ready. Opening the page...
start "" http://localhost:%PORT%
