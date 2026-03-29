#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${APP_NAME:?APP_NAME not set}"
APP_SECRET_PATH="${APP_SECRET_PATH:?APP_SECRET_PATH not set}"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY not set}"

BASE_DIR="/opt/apps"
APP_DIR="$BASE_DIR/$APP_NAME"
MANIFEST="$APP_DIR/codebuild/app.manifest.json"
DEPLOY_USER="ubuntu"

echo "➡ Creating app: $APP_NAME"

cd "$APP_DIR"

if [ ! -f "$MANIFEST" ]; then
  echo "❌ Missing codebuild/app.manifest.json"
  exit 1
fi

RUNTIME=$(jq -r '.runtime' "$MANIFEST")
WORKDIR=$(jq -r '.working_dir' "$MANIFEST")
START_CMD=$(jq -r '.start_command' "$MANIFEST")
PORT=$(jq -r '.port' "$MANIFEST")
DOMAIN=$(jq -r '.domain' "$MANIFEST")

APP_WORKDIR="$APP_DIR/$WORKDIR"

if [ ! -d "$APP_WORKDIR" ]; then
  echo "❌ working_dir does not exist: $APP_WORKDIR"
  exit 1
fi

cd "$APP_WORKDIR"

sudo chown -R "$DEPLOY_USER:$DEPLOY_USER" "$APP_WORKDIR"
sudo chmod -R u+rwX,g+rwX "$APP_WORKDIR"

# ================================
# PYTHON TOOLING
# ================================
echo "🔧 Installing Python tooling"

sudo apt-get update -y
sudo apt-get install -y jq python3-pip python3-venv python3-dev \
                         curl ca-certificates \
                         chromium-browser chromium-chromedriver xvfb

# Install or repair Poetry (Official Installer)
# Note: official script installs into /home/ubuntu/.local/bin/poetry by default
POETRY_BIN="/home/$DEPLOY_USER/.local/bin/poetry"
PYTHON_BIN="/opt/python3.14/bin/python"

if [ ! -f "$PYTHON_BIN" ]; then
  echo "⚠️ $PYTHON_BIN not found, falling back to system python3"
  PYTHON_BIN="python3"
fi

if ! sudo -u "$DEPLOY_USER" [ -x "$POETRY_BIN" ] || ! sudo -u "$DEPLOY_USER" "$POETRY_BIN" --version &>/dev/null; then
  echo "📥 Installing/Repairing Poetry using $PYTHON_BIN"
  curl -sSL https://install.python-poetry.org | sudo -u "$DEPLOY_USER" "$PYTHON_BIN" -
fi

export PATH="/home/$DEPLOY_USER/.local/bin:$PATH"

# ================================
# RUNTIME SETUP
# ================================
if [ "$RUNTIME" = "python" ]; then
  echo "🐍 Python setup with Poetry ($PYTHON_BIN)"

  sudo -u "$DEPLOY_USER" "$POETRY_BIN" config virtualenvs.in-project true

  if [ -d ".venv" ]; then
    if ! .venv/bin/python --version | grep -q "3.14"; then
      echo "🗑️ Removing incompatible .venv"
      sudo rm -rf .venv
    fi
  fi

  if [ ! -d ".venv" ]; then
    echo "📦 Creating .venv with $PYTHON_BIN"
    sudo -u "$DEPLOY_USER" "$PYTHON_BIN" -m venv .venv
  fi

  echo "📦 Installing dependencies"
  sudo -u "$DEPLOY_USER" "$POETRY_BIN" install --no-root --no-interaction

  if [ ! -d ".venv" ]; then
    echo "❌ .venv not created"
    exit 1
  fi
fi

# ================================
# SYSTEMD
# ================================
sudo tee "/etc/systemd/system/${APP_NAME}.service" > /dev/null <<EOF
[Unit]
Description=${APP_NAME}
After=network.target

[Service]
User=ubuntu
WorkingDirectory=${APP_WORKDIR}
UMask=0002

Environment=APP_SECRET_JSON=${APP_SECRET_PATH}
Environment=PYTHONPATH=${APP_WORKDIR}
Environment=PATH=/home/ubuntu/.local/bin:/usr/bin:/bin

ExecStart=${APP_WORKDIR}/${START_CMD}

Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "${APP_NAME}"
sudo systemctl restart "${APP_NAME}"

# ================================
# NGINX
# ================================
echo "🌐 Generating nginx config"

sudo tee "/etc/nginx/sites-available/${DOMAIN}" > /dev/null <<EOF
server {
  listen 80;
  server_name ${DOMAIN};

  location / {
    proxy_pass http://127.0.0.1:${PORT};
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
  }
}
EOF

sudo ln -sf "/etc/nginx/sites-available/${DOMAIN}" "/etc/nginx/sites-enabled/${DOMAIN}"

sudo nginx -t
sudo systemctl reload nginx

echo "✅ App created successfully"