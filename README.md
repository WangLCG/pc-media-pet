# PC Media Pet

Windows USB-camera monitoring over a private Tailscale network. Notifications and media remain in memory; the application does not write video, snapshots, or event history to disk.

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

## Tailscale deployment

Keep the service bound to `127.0.0.1` and expose it only through Tailscale Serve:

```powershell
tailscale serve --bg 8000
tailscale serve status
```

Open the Tailnet HTTPS hostname reported by `tailscale serve status`, enter the application token, and verify `/health`, notification delivery, and an on-demand video session from a second Tailnet device. Do not enable Tailscale Funnel and do not configure router port forwarding.

Alternatively, set `APP_HOST` to the laptop's intended `100.x.y.z` Tailscale address and run `scripts\run.ps1 -HostAddress <tailscale-ip>`. Restrict Tailnet ACLs to the specific users/devices that need access; the application token remains a second authorization layer.

## Windows startup

Install a per-user logon task after creating `.venv` and `.env`:

```powershell
.\scripts\install_service.ps1
Get-ScheduledTask -TaskName "PC Media Pet"
```

The task starts the existing local-only runner and restarts up to three times on failure. Remove it with:

```powershell
.\scripts\uninstall_service.ps1
```

## Token rotation

Generate a new random `APP_TOKEN`, update `.env`, then restart the scheduled task (or restart `scripts\run.ps1`). Existing browser notification and media sessions close and must reconnect with the new token. Do not place tokens in source control, browser local storage, logs, or task arguments.

## Verify

```powershell
pytest
```
