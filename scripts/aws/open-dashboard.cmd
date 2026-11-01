@echo off
rem =============================================================================
rem open-dashboard.cmd - double-click to open the live dashboard as the Group 3 operator (Windows)
rem -----------------------------------------------------------------------------
rem Runs scripts\aws\dashboard-signin.py with the project Python. If the AWS
rem session has expired, it first re-runs "aws login" for the is459-g3 profile.
rem Changes nothing in AWS and stores no credentials. Needs the project venv
rem (.venv) and the profiles created once by scripts\aws\configure-aws.ps1.
rem macOS equivalent: scripts/aws/open-dashboard.command
rem =============================================================================
setlocal
set "ROOT=%~dp0..\.."
set "PROFILE=is459-g3"
set "TOOLS=is459-g3-tools"
set "REGION=ap-southeast-1"

rem Project Python: the README's .venv first, then the older output\ml-venv.
set "PY=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%ROOT%\output\ml-venv\Scripts\python.exe"
if not exist "%PY%" (
    echo Project Python not found. From the repository folder run:
    echo     python -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -e ".[cloud]"
    goto fail
)
"%PY%" -c "import boto3" >nul 2>nul
if errorlevel 1 (
    echo The project Python is missing boto3. From the repository folder run:
    echo     "%PY%" -m pip install -e ".[cloud]"
    goto fail
)

rem AWS CLI: PATH first, else the default MSI install location (as configure-aws.ps1 does).
rem Its folder goes on PATH so the tools profile's credential_process can find aws.exe.
set "AWS=aws"
where aws >nul 2>nul || set "AWS=C:\Program Files\Amazon\AWSCLIV2\aws.exe"
if not "%AWS%"=="aws" (
    if not exist "%AWS%" (
        echo AWS CLI v2 not found. Install it, then run scripts\aws\configure-aws.ps1 once.
        goto fail
    )
    set "PATH=C:\Program Files\Amazon\AWSCLIV2;%PATH%"
)

rem Renew the session only when the tools profile can no longer reach AWS.
"%AWS%" sts get-caller-identity --profile %TOOLS% --region %REGION% --no-cli-pager >nul 2>nul
if errorlevel 1 (
    echo Your AWS session has expired or is not set up. Opening AWS sign-in...
    "%AWS%" login --profile %PROFILE% --region %REGION%
    if errorlevel 1 goto fail
    "%AWS%" sts get-caller-identity --profile %TOOLS% --region %REGION% --no-cli-pager >nul 2>nul
    if errorlevel 1 (
        echo Still not signed in. On a new laptop, run scripts\aws\configure-aws.ps1 once first.
        goto fail
    )
)

"%PY%" "%ROOT%\scripts\aws\dashboard-signin.py" --profile %TOOLS%
if errorlevel 1 goto fail
rem Leave the message on screen briefly before the window closes.
timeout /t 5 >nul
exit /b 0

:fail
echo.
pause
exit /b 1
