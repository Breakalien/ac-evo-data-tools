@echo off
rem Rebuild the .proto schemas and proto\acevo.desc from the game executable.
rem Drag your AC EVO install folder (or AssettoCorsaEVO.exe) onto this file,
rem or double-click it and type the path when asked.
cd /d "%~dp0\.."

set "TARGET=%~1"
if "%TARGET%"=="" (
    set /p TARGET="Path to AC EVO folder or AssettoCorsaEVO.exe: "
)

if exist "%TARGET%\AssettoCorsaEVO.exe" set "TARGET=%TARGET%\AssettoCorsaEVO.exe"

if not exist "%TARGET%" (
    echo Not found: %TARGET%
    pause
    exit /b 1
)

python tools\extract_protos.py "%TARGET%" -o proto -d proto\acevo.desc
pause
