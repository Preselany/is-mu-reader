@echo off
setlocal
set "ISMU_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%ISMU_PYTHON%" (
  echo Runtime missing. Install this package as described in README.md. 1>&2
  exit /b 1
)
"%ISMU_PYTHON%" -m ismu %*
exit /b %errorlevel%
