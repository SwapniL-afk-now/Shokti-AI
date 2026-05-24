# Shokti Production Deployment (systemd + Netlify)

## 1) Server setup

```bash
cd /home/gpuserver3/workspace/swapnil/Shokti-AI
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

## 2) Production env file (outside repo)

```bash
sudo mkdir -p /etc/shokti
sudo cp deploy/shokti-api.env.example /etc/shokti/shokti-api.env
sudo chmod 600 /etc/shokti/shokti-api.env
sudo nano /etc/shokti/shokti-api.env
```

Set `JWT_SECRET_KEY`, `GEMINI_API_KEY`, and production `CORS_ORIGINS`.

## 3) systemd service

```bash
sudo cp deploy/shokti-api.service /etc/systemd/system/shokti-api.service
sudo systemctl daemon-reload
sudo systemctl enable shokti-api
sudo systemctl start shokti-api
sudo systemctl status shokti-api --no-pager
```

## 4) Nginx reverse proxy + TLS

- Put `deploy/nginx-api.conf` at `/etc/nginx/sites-available/shokti-api`.
- Replace `api.your-domain.com` with your real API domain.
- Enable site and reload nginx.
- Issue cert with certbot for that domain.

## 5) Netlify frontend

- Connect repo to Netlify.
- Publish directory: `static` (already in `netlify.toml`).
- Add env var in Netlify:
  - `API_BASE_URL=https://api.your-domain.com`

## 6) Persistence checks

```bash
curl -fsS https://api.your-domain.com/health
sudo systemctl restart shokti-api
sudo systemctl status shokti-api --no-pager
sudo journalctl -u shokti-api -n 100 --no-pager
```

- Log out SSH and check `/health` again from local machine.
- Reboot server and confirm service auto-starts.
