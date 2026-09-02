@echo off
setlocal
cd /d "%~dp0"

set "FI_PRODUCT_PYTHON=.venv\Scripts\python.exe"
if not exist "%FI_PRODUCT_PYTHON%" (
  py -3.11 --version >nul 2>&1
  if errorlevel 1 (
    py deploy\bootstrap.py
  ) else (
    py -3.11 deploy\bootstrap.py
  )
  if errorlevel 1 exit /b 1
)

"%FI_PRODUCT_PYTHON%" deploy\product.py %*
exit /b %errorlevel%
