# Boeing Data Hub - EC2 Deployment Guide

**Prerequisites:** Ubuntu 22.04 EC2 instance, domain pointing to your EC2 IP, Supabase project ready.

---

## Step 1: System Setup

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.11 python3.11-venv python3.11-dev python3-pip build-essential git curl
```

## Step 2: Install Node.js 20

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

## Step 3: Install Redis

```bash
sudo apt install -y redis-server
sudo systemctl enable redis-server
sudo systemctl start redis-server
```

## Step 4: Install Nginx

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
```

## Step 5: Upload Code

```bash
sudo mkdir -p /opt/boeing-data-hub
sudo chown -R ubuntu:ubuntu /opt/boeing-data-hub
```

From your local machine:
```bash
scp -i "your-key.pem" -r ./backend ./frontend ./database ubuntu@YOUR_EC2_IP:/opt/boeing-data-hub/
```

## Step 6: Setup Backend

```bash
cd /opt/boeing-data-hub/backend
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Create `.env` file:
```bash
nano /opt/boeing-data-hub/backend/.env
```

Paste this (replace values):
```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
SUPABASE_STORAGE_BUCKET=product-images

SHOPIFY_STORE_DOMAIN=your-store.myshopify.com
SHOPIFY_ADMIN_API_TOKEN=shpat_xxxxx
SHOPIFY_API_VERSION=2024-10
SHOPIFY_LOCATION_MAP={}

BOEING_CLIENT_ID=your-client-id
BOEING_CLIENT_SECRET=your-client-secret
BOEING_USERNAME=your-username
BOEING_PASSWORD=your-password
BOEING_SCOPE=api://helixapis.com/.default
BOEING_OAUTH_TOKEN_URL=https://api.developer.boeingservices.com/oauth2/v2.0/token
BOEING_PNA_OAUTH_URL=https://api.developer.boeingservices.com/boeing-part-price-availability/token/v1/oauth
BOEING_PNA_PRICE_URL=https://api.developer.boeingservices.com/boeing-part-price-availability/price-availability/v1/wtoken

REDIS_URL=redis://127.0.0.1:6379/0
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0

BOEING_BATCH_SIZE=10
MAX_BULK_SEARCH_SIZE=50000
MAX_BULK_PUBLISH_SIZE=10000
```

## Step 7: Setup Frontend

```bash
cd /opt/boeing-data-hub/frontend
npm install
```

Create `.env.production`:
```bash
nano /opt/boeing-data-hub/frontend/.env.production
```

Paste this (replace YOUR_DOMAIN):
```env
VITE_API_BASE_URL=https://YOUR_DOMAIN.com
VITE_SUPABASE_URL=https://your-project.supabase.co
VITE_SUPABASE_ANON_KEY=your-anon-key
```

Build frontend:
```bash
npm run build
sudo mkdir -p /var/www/boeing-frontend
sudo cp -r dist/* /var/www/boeing-frontend/
sudo chown -R www-data:www-data /var/www/boeing-frontend
```

## Step 8: Create Backend Service

```bash
sudo nano /etc/systemd/system/boeing-backend.service
```

Paste this:
```ini
[Unit]
Description=Boeing Backend
After=network.target redis-server.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/boeing-data-hub/backend
Environment="PATH=/opt/boeing-data-hub/backend/venv/bin"
EnvironmentFile=/opt/boeing-data-hub/backend/.env
ExecStart=/opt/boeing-data-hub/backend/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## Step 9: Create Celery Service

```bash
sudo nano /etc/systemd/system/boeing-celery.service
```

Paste this:
```ini
[Unit]
Description=Boeing Celery Worker
After=network.target redis-server.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/boeing-data-hub/backend
Environment="PATH=/opt/boeing-data-hub/backend/venv/bin"
EnvironmentFile=/opt/boeing-data-hub/backend/.env
ExecStart=/opt/boeing-data-hub/backend/venv/bin/celery -A celery_app worker --loglevel=info -Q extraction,normalization,publishing,default --concurrency=4
Restart=always
RestartSec=10
TimeoutStopSec=300

[Install]
WantedBy=multi-user.target
```

## Step 10: Configure Nginx

```bash
sudo nano /etc/nginx/sites-available/boeing-data-hub
```

Paste this (replace YOUR_DOMAIN.com):
```nginx
server {
    listen 80;
    server_name YOUR_DOMAIN.com www.YOUR_DOMAIN.com;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://$server_name$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name YOUR_DOMAIN.com www.YOUR_DOMAIN.com;

    ssl_certificate /etc/letsencrypt/live/YOUR_DOMAIN.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/YOUR_DOMAIN.com/privkey.pem;

    root /var/www/boeing-frontend;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
    }

    location /health {
        proxy_pass http://127.0.0.1:8000;
    }
}
```

Enable site:
```bash
sudo rm /etc/nginx/sites-enabled/default
sudo ln -s /etc/nginx/sites-available/boeing-data-hub /etc/nginx/sites-enabled/
```

## Step 11: Get SSL Certificate

Create certbot directory and temporary config:
```bash
sudo mkdir -p /var/www/certbot
```

Get certificate (replace YOUR_DOMAIN.com and YOUR_EMAIL):
```bash
sudo certbot certonly --webroot -w /var/www/certbot -d YOUR_DOMAIN.com --email YOUR_EMAIL --agree-tos --non-interactive
```

If certbot fails, run nginx temporarily first:
```bash
sudo nginx -t
sudo systemctl start nginx
sudo certbot --nginx -d YOUR_DOMAIN.com
```

## Step 12: Start Everything

```bash
sudo systemctl daemon-reload
sudo systemctl enable boeing-backend boeing-celery nginx
sudo systemctl start boeing-backend boeing-celery
sudo systemctl reload nginx
```

## Step 13: Verify

```bash
# Check services
sudo systemctl status boeing-backend
sudo systemctl status boeing-celery
sudo systemctl status nginx

# Test API
curl http://localhost:8000/health
```

---

## Useful Commands

**View logs:**
```bash
sudo journalctl -u boeing-backend -f
sudo journalctl -u boeing-celery -f
```

**Restart services:**
```bash
sudo systemctl restart boeing-backend boeing-celery
```

**Update deployment:**
```bash
cd /opt/boeing-data-hub/backend && source venv/bin/activate && pip install -r requirements.txt
cd /opt/boeing-data-hub/frontend && npm install && npm run build && sudo cp -r dist/* /var/www/boeing-frontend/
sudo systemctl restart boeing-backend boeing-celery && sudo systemctl reload nginx
```

---

## EC2 Security Group Rules

| Port | Source | Description |
|------|--------|-------------|
| 22 | Your IP | SSH |
| 80 | 0.0.0.0/0 | HTTP |
| 443 | 0.0.0.0/0 | HTTPS |
