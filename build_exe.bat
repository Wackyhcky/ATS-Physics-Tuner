@echo off
rem Builds dist\ATS Physics Tuner\ATS Physics Tuner.exe
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py -3 build_exe.py) else (python build_exe.py)
echo.
pause
