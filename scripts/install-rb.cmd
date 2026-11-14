@echo off
rem Installs the `rb` command on Windows (see install-rb.ps1). Double-click this file, or run:
rem   scripts\install-rb.cmd              install, or update after moving this folder
rem   scripts\install-rb.cmd -Uninstall   remove it
rem find.exe is named in full: a Unix `find` earlier on the PATH (Git's tools) would do something else.
rem No setlocal on purpose: run from a Command Prompt, this file shares that window's
rem environment, so the PATH change below makes `rb` work there right away.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-rb.ps1" %*
set "RB_INSTALL_RESULT=%ERRORLEVEL%"
if not "%RB_INSTALL_RESULT%"=="0" echo Installing rb failed; see the message above.
if "%RB_INSTALL_RESULT%"=="0" if /i not "%~1"=="-Uninstall" call :use_now
rem Keep the window open when started by double-click, so the messages can be read.
echo %CMDCMDLINE% | "%SystemRoot%\System32\find.exe" /i "/c" >nul && pause
set "RB_INSTALL_RESULT=" & exit /b %RB_INSTALL_RESULT%

:use_now
set "RB_INSTALL_DIR=%RB_BIN_DIR%"
if not defined RB_INSTALL_DIR set "RB_INSTALL_DIR=%LOCALAPPDATA%\resume-builder\bin"
echo ;%PATH%; | "%SystemRoot%\System32\find.exe" /i ";%RB_INSTALL_DIR%;" >nul || set "PATH=%PATH%;%RB_INSTALL_DIR%"
rem Only an interactive Command Prompt (not one started with /c) keeps this PATH afterwards.
echo %CMDCMDLINE% | "%SystemRoot%\System32\find.exe" /i "/c" >nul || echo This Command Prompt window can use rb now.
set "RB_INSTALL_DIR="
exit /b 0
