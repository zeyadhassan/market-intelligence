@echo off
setlocal
cd /d "%~dp0"

set "FI_PRODUCT_PYTHON=.venv\Scripts\python.exe"
if not exist "%FI_PRODUCT_PYTHON%" (
  echo Creating the local Python environment...
  py -3.11 -m venv .venv 2>nul
  if errorlevel 1 py -m venv .venv
  if errorlevel 1 (
    echo Python 3.11 or later is required.
    exit /b 1
  )
  "%FI_PRODUCT_PYTHON%" -m pip install -e ".[dev]"
  if errorlevel 1 exit /b 1
)

"%FI_PRODUCT_PYTHON%" deploy\product.py %*
exit /b %errorlevel%
