#!/bin/bash
# Deploy corn-exporter-dashboard to the JSA Droplet.
# Run from the repo root: bash deploy.sh
set -e

DROPLET="root@137.184.195.51"
REMOTE_DIR="/root/corn-exporter-dashboard"

echo "==> Syncing files..."
rsync -av --exclude='.git' --exclude='__pycache__' --exclude='.env' \
  ./ "$DROPLET:$REMOTE_DIR/"

echo "==> Setting up venv + deps..."
ssh "$DROPLET" "
  cd $REMOTE_DIR
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
"

echo "==> Registering PM2 processes..."
ssh "$DROPLET" "
  cd $REMOTE_DIR
  # ETL (runs via cron — just register for manual runs)
  pm2 describe corn-export-etl > /dev/null 2>&1 || \
    pm2 start .venv/bin/python --name corn-export-etl -- etl.py

  # Streamlit app on port 8545
  pm2 describe corn-exporter-app > /dev/null 2>&1 || \
    pm2 start .venv/bin/streamlit --name corn-exporter-app -- run app.py \
      --server.port 8545 --server.headless true --server.address 0.0.0.0

  pm2 save
"

echo "==> Adding cron for ETL (7:05am daily)..."
ssh "$DROPLET" "(crontab -l 2>/dev/null | grep -v corn-export-etl; \
  echo '5 7 * * * cd /root/corn-exporter-dashboard && .venv/bin/python etl.py >> /var/log/corn-export-etl.log 2>&1') | crontab -"

echo ""
echo "Done. App running at http://137.184.195.51:8545"
echo "To check: ssh root@137.184.195.51 'pm2 status'"
