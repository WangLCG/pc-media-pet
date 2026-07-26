# PC Media Pet

Phase 0 provides the authenticated FastAPI foundation for the Tailscale/WebRTC design.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# Replace APP_TOKEN in .env with a unique secret of at least 16 characters.
.\scripts\run.ps1
```

Open `http://127.0.0.1:8000`. The token stays only in the page input and is not persisted by the browser.

## Verify

```powershell
pytest
```
