@echo off
rem Sets up resume-builder on Windows (see setup.ps1): builds and starts the app, then installs
rem the `rb` command. Double-click this file, or run it from a Command Prompt in this folder.
rem find.exe is named in full: a Unix `find` earlier on the PATH (Git's tools) would do something else.
rem No setlocal on purpose: run from a Command Prompt, this file shares that window's
rem environment, so the PATH change below makes `rb` work there right away.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
set "RB_SETUP_RESULT=%ERRORLEVEL%"
if "%RB_SETUP_RESULT%"=="0" call :use_now
rem Keep the window open when started by double-click, so the messages can be read.
echo %CMDCMDLINE% | "%SystemRoot%\System32\find.exe" /i "/c" >nul && pause
set "RB_SETUP_RESULT=" & exit /b %RB_SETUP_RESULT%

:use_now
set "RB_INSTALL_DIR=%RB_BIN_DIR%"
if not defined RB_INSTALL_DIR set "RB_INSTALL_DIR=%LOCALAPPDATA%\resume-builder\bin"
echo ;%PATH%; | "%SystemRoot%\System32\find.exe" /i ";%RB_INSTALL_DIR%;" >nul || set "PATH=%PATH%;%RB_INSTALL_DIR%"
echo %CMDCMDLINE% | "%SystemRoot%\System32\find.exe" /i "/c" >nul || echo This Command Prompt window can use rb now.
set "RB_INSTALL_DIR="
exit /b 0
