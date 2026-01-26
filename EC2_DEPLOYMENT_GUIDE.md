# Boeing Data Hub - EC2 Deployment Guide

## Server Details
- **EC2 IP**: 54.234.36.109
- **Domain**: api.boeing-data-hub.skynetparts.com
- **SSH Key**: rfq.pem

---

## Services Overview

| Service | Port | Systemd Service | Description |
|---------|------|-----------------|-------------|
| FastAPI Backend | 8000 | boeing-backend | Main API server |
| Celery Worker | - | boeing-celery | Background task processor |
| Redis | 6379 | redis-server | Message broker & cache |
| Nginx | 80/443 | nginx | Reverse proxy & SSL |

---

## Quick Commands

### SSH into Server
```bash
ssh -i "C:\Users\91948\Downloads\rfq.pem" ubuntu@54.234.36.109
```

### Check All Services Status
```bash
sudo systemctl status boeing-backend boeing-celery redis-server nginx
```

---

## Restarting Services After Code Updates

### Method 1: Quick Restart (Recommended)
```bash
# SSH into server
ssh -i "C:\Users\91948\Downloads\rfq.pem" ubuntu@54.234.36.109

# Restart backend and celery
sudo systemctl restart boeing-backend boeing-celery
```

### Method 2: Full Update Script
Run this from your local machine (Windows):

```bash
# Step 1: Upload updated code
scp -i "C:\Users\91948\Downloads\rfq.pem" -r "c:\Users\91948\Desktop\boeing-data-hub\backend" ubuntu@54.234.36.109:~/boeing-data-hub/

# Step 2: SSH and restart services
ssh -i "C:\Users\91948\Downloads\rfq.pem" ubuntu@54.234.36.109 "cd ~/boeing-data-hub/backend && source venv/bin/activate && pip install -r requirements.txt && sudo systemctl restart boeing-backend boeing-celery"
```

### Method 3: One-Liner Deploy
```bash
scp -i "C:\Users\91948\Downloads\rfq.pem" -r "c:\Users\91948\Desktop\boeing-data-hub\backend" ubuntu@54.234.36.109:~/boeing-data-hub/ && ssh -i "C:\Users\91948\Downloads\rfq.pem" ubuntu@54.234.36.109 "sudo systemctl restart boeing-backend boeing-celery"
```

---

## Individual Service Management

### Backend (FastAPI)
```bash
# Status
sudo systemctl status boeing-backend

# Restart
sudo systemctl restart boeing-backend

# View logs (live)
sudo journalctl -u boeing-backend -f

# View last 100 log lines
sudo journalctl -u boeing-backend -n 100 --no-pager
```

### Celery Worker
```bash
# Status
sudo systemctl status boeing-celery

# Restart
sudo systemctl restart boeing-celery

# View logs (live)
sudo journalctl -u boeing-celery -f

# View last 100 log lines
sudo journalctl -u boeing-celery -n 100 --no-pager
```

### Redis
```bash
# Status
sudo systemctl status redis-server

# Restart
sudo systemctl restart redis-server

# Test connection
redis-cli ping

# View Redis info
redis-cli info
```

### Nginx
```bash
# Status
sudo systemctl status nginx

# Restart
sudo systemctl restart nginx

# Test config
sudo nginx -t

# Reload config (no downtime)
sudo systemctl reload nginx
```

---

## Service File Locations

| Service | Config File |
|---------|-------------|
| Backend | `/etc/systemd/system/boeing-backend.service` |
| Celery | `/etc/systemd/system/boeing-celery.service` |
| Nginx | `/etc/nginx/sites-available/boeing-backend` |
| Environment | `/home/ubuntu/boeing-data-hub/backend/.env` |

---

## SSL Certificate Setup

### Prerequisites
1. Add DNS A record: `api.boeing-data-hub.skynetparts.com` -> `54.234.36.109`
2. Open ports 80 and 443 in AWS Security Group

### Obtain Certificate
```bash
sudo certbot --nginx -d api.boeing-data-hub.skynetparts.com
```

### Renew Certificate (auto-renews via cron, but manual if needed)
```bash
sudo certbot renew
```

### Check Certificate Expiry
```bash
sudo certbot certificates
```

---

## AWS Security Group Rules

Ensure these inbound rules are configured:

| Type | Port | Source | Description |
|------|------|--------|-------------|
| SSH | 22 | Your IP | SSH access |
| HTTP | 80 | 0.0.0.0/0 | Web traffic & SSL verification |
| HTTPS | 443 | 0.0.0.0/0 | Secure web traffic |

---

## Troubleshooting

### Backend won't start
```bash
# Check logs
sudo journalctl -u boeing-backend -n 50 --no-pager

# Check if port is in use
sudo lsof -i :8000

# Test manually
cd ~/boeing-data-hub/backend
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Celery won't connect to Redis
```bash
# Check Redis is running
sudo systemctl status redis-server
redis-cli ping

# Check env variables
grep REDIS ~/boeing-data-hub/backend/.env
```

### Nginx 502 Bad Gateway
```bash
# Check backend is running
curl http://localhost:8000/health

# Check Nginx logs
sudo tail -f /var/log/nginx/error.log
```

### View All Logs
```bash
# Combined view
sudo journalctl -u boeing-backend -u boeing-celery -u nginx -f
```

---

## Directory Structure on Server

```
/home/ubuntu/boeing-data-hub/
└── backend/
    ├── app/
    │   ├── main.py
    │   ├── clients/
    │   ├── core/
    │   ├── db/
    │   ├── routes/
    │   ├── schemas/
    │   ├── services/
    │   └── utils/
    ├── celery_app/
    │   └── tasks/
    ├── venv/
    ├── .env
    └── requirements.txt
```

---

## Health Check Endpoints

```bash
# From local machine (after DNS setup)
curl https://api.boeing-data-hub.skynetparts.com/health

# From within server
curl http://localhost:8000/health

# Expected response
{"status":"healthy"}
```

---

## Updating Environment Variables

```bash
# Edit .env file
nano ~/boeing-data-hub/backend/.env

# Restart services to pick up changes
sudo systemctl restart boeing-backend boeing-celery
```

---

## Backup Commands

### Backup .env file
```bash
cp ~/boeing-data-hub/backend/.env ~/boeing-data-hub/backend/.env.backup
```

### Download logs
```bash
# From local machine
scp -i "C:\Users\91948\Downloads\rfq.pem" ubuntu@54.234.36.109:~/logs.txt .
```

---

## Emergency: Stop All Services
```bash
sudo systemctl stop boeing-backend boeing-celery
```

## Emergency: Start All Services
```bash
sudo systemctl start redis-server nginx boeing-backend boeing-celery
```

---

## CI/CD with GitHub Actions

### Overview
The repository has automatic deployment configured via GitHub Actions. When you push changes to the `main` branch that affect the `backend/` folder, it will automatically deploy to EC2.

**Workflow file**: `.github/workflows/deploy-backend.yml`

### Setting Up GitHub Secrets

You need to add one secret to your GitHub repository:

1. Go to: https://github.com/PulseInsights-Org/boeing-data-hub/settings/secrets/actions
2. Click **"New repository secret"**
3. Add the following secret:

| Secret Name | Value |
|-------------|-------|
| `EC2_SSH_KEY` | Contents of your `rfq.pem` file |

**To get the PEM file contents:**
```powershell
# Windows PowerShell
Get-Content "C:\Users\91948\Downloads\rfq.pem" | Set-Clipboard
```
Then paste into the GitHub secret value field.

### How CI/CD Works

1. **Trigger**: Push to `main` branch with changes in `backend/` folder
2. **Action**: GitHub Actions runner connects to EC2 via SSH
3. **Deploy**: Syncs code using rsync (excludes `.env`, `venv`, `__pycache__`)
4. **Install**: Runs `pip install -r requirements.txt`
5. **Restart**: Restarts `boeing-backend` and `boeing-celery` services
6. **Verify**: Runs health check to confirm deployment success

### Manual Deployment Trigger

You can also trigger deployment manually:
1. Go to: https://github.com/PulseInsights-Org/boeing-data-hub/actions
2. Select "Deploy Backend to EC2" workflow
3. Click "Run workflow"
4. Select `main` branch and click "Run workflow"

### Viewing Deployment Logs

1. Go to: https://github.com/PulseInsights-Org/boeing-data-hub/actions
2. Click on the latest workflow run
3. Click on "Deploy to EC2" job to see detailed logs

### Important Notes

- The `.env` file is **NOT** synced during deployment (for security)
- If you need to update `.env`, SSH into the server and edit manually
- The `venv/` folder is preserved on the server (not overwritten)
- Failed deployments will show error logs from the service journals

### Workflow File Location
```
.github/workflows/deploy-backend.yml
```
