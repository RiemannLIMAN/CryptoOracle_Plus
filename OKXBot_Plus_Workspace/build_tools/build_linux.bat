@echo off
echo ===================================================
echo   CryptoOracle Linux Binary Builder (via Docker)
echo ===================================================
echo.

echo 1. Checking Docker...
docker --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Docker is not installed or not in PATH.
    echo Please install Docker Desktop for Windows to build Linux binaries.
    pause
    exit /b
)

echo.
echo 2. Building Builder Image...
cd ..
docker build -t crypto_oracle_builder -f build_tools/Dockerfile .
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Docker build failed.
    pause
    exit /b
)

echo.
echo 3. Extracting Binary...
if not exist dist mkdir dist
docker run --rm -v "%cd%/dist:/dist" crypto_oracle_builder

echo.
echo 4. Creating Release Package...
if not exist release mkdir release
copy dist\CryptoOracle release\
copy config.example.json release\config.json
copy .env.example release\.env
copy build_tools\start.sh release\

echo.
echo ===================================================
echo   Build Complete!
echo   Deployment package is in: release/
echo ===================================================
echo.
pause
