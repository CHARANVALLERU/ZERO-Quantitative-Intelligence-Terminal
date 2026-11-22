@echo off
cd /d "%~dp0"

:: --- Runtime env defaults (no secrets required) ---
:: REA 6.10.1 needs Node >=24.22.1. Prefer the verified portable runtime when
:: the machine-wide install is older (see setup_node_runtime.ps1).
set "ZERO_NODE_DIR=%LOCALAPPDATA%\Programs\node-v24.22.3\node-v24.22.3-win-x64"
if exist "%ZERO_NODE_DIR%\node.exe" (
    set "PATH=%ZERO_NODE_DIR%;%PATH%"
) else (
    set "ZERO_NODE_DIR="
)
set "TOKENIZERS_PARALLELISM=false"
set "HF_HUB_DISABLE_TELEMETRY=1"
set "STREAMLIT_SERVER_FILE_WATCHER_TYPE=poll"
:: Optional: uncomment and set your Hugging Face token for higher Hub rate limits
:: set "HF_TOKEN=hf_your_token_here"
:: If HF_TOKEN is already in your user/system environment, it is passed through automatically.

if exist .venv goto ACTIVATED
echo Creating Python Virtual Environment (.venv)...
python -m venv .venv
echo Installing dependencies from requirements.txt...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
goto LAUNCH

:ACTIVATED
call .venv\Scripts\activate.bat

:LAUNCH
echo Launching ZERO Prediction Engine...
if exist "setup_scheduler.bat" (
    start "" "setup_scheduler.bat"
)
:: Optional once-per-install MiniLM warmup (do NOT uncomment for every launch —
:: that would re-check / potentially re-download ~80 MB on each start):
:: python scripts\warmup_okf_embeddings.py
streamlit run app.py
pause
