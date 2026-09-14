# Runs backend (port 8000) and Vite dev server (port 5173, proxies /api) side by side.
Start-Process powershell -ArgumentList "-NoExit","-Command","cd '$PSScriptRoot\backend'; python -m uvicorn app.main:app --reload --port 8000"
Start-Process powershell -ArgumentList "-NoExit","-Command","cd '$PSScriptRoot\frontend'; npm run dev"
