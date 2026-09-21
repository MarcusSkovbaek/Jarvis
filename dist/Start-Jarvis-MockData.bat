@echo off
REM Starts Jarvis against built-in MOCK data. Outlook is never touched.
REM Run this first to confirm the .exe works at all on this machine.
REM
REM The .exe is invoked by its full path ("%~dp0Jarvis.exe") rather than by
REM name. Some machines set NoDefaultCurrentDirectoryInExePath=1, which stops
REM cmd finding an executable in the current folder even after a cd.
cd /d "%~dp0"
echo Starting Jarvis with MOCK data - your real mailbox is NOT accessed.
echo Everything shown will be invented test data.
echo Dashboard: http://localhost:5000
echo Close this window to stop Jarvis.
echo.
"%~dp0Jarvis.exe" --backend mock
set EXITCODE=%ERRORLEVEL%
if not "%EXITCODE%"=="0" (
  echo.
  echo Jarvis exited with code %EXITCODE%.
  echo See sync.log in this folder for the reason.
  echo.
  pause
)
