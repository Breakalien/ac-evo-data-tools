@echo off
rem Launch the Assetto Corsa EVO data explorer and open the browser.
rem Default root: ..\content  (override with --dir)
cd /d "%~dp0"
python acevo_ui.py %*
pause
