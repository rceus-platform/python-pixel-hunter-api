
@echo off

set REPO_NAME=python-pixel-hunter-api

rem Activate virtual environment
call "%github-local-directory%\%REPO_NAME%\.venv\Scripts\activate.bat"

rem Run Python script with command-line argument
python "%github-local-directory%\%REPO_NAME%\src\app\main.py" %1

rem Deactivate virtual environment (optional)
deactivate
