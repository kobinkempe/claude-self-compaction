@echo off
setlocal
where py >nul 2>nul
if errorlevel 1 goto usepython
py -3 "%~dp0install.py" %*
goto finished
:usepython
python "%~dp0install.py" %*
:finished
echo.
pause
