@echo off
REM Starts Jarvis against the REAL Outlook mailbox.
REM Outlook must be open and the profile loaded before you run this.
REM
REM The .exe is invoked by its full path ("%~dp0Jarvis.exe") rather than by
REM name. Some machines set NoDefaultCurrentDirectoryInExePath=1, which stops
REM cmd finding an executable in the current folder even after a cd.
cd /d "%~dp0"
echo Starting Jarvis against the real Outlook mailbox...
echo Dashboard: http://localhost:5000
echo Close this window to stop Jarvis.
echo.
"%~dp0Jarvis.exe" --backend com
set EXITCODE=%ERRORLEVEL%
if not "%EXITCODE%"=="0" (
  echo.
  echo Jarvis exited with code %EXITCODE%.
  echo See sync.log in this folder for the reason.
  echo.
  pause
)
