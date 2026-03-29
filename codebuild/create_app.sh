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
# OS DEPENDENCIES
# ================================
echo "🔧 Installing OS dependencies"

install_if_missing() {
  local PKG=$1
  if ! dpkg -l "$PKG" &>/dev/null; then
    echo "📦 Installing $PKG..."
    sudo apt-get install -y "$PKG"
  else
    echo "✅ $PKG is already installed"
  fi
}

# Only update apt if we need to install something new
NEED_UPDATE=false
for pkg in jq python3-pip python3-venv python3-dev build-essential clang curl ca-certificates chromium-browser chromium-chromedriver xvfb; do
  if ! dpkg -l "$pkg" &>/dev/null; then
    NEED_UPDATE=true
    break
  fi
done

if [ "$NEED_UPDATE" = true ]; then
  echo "⬆ Updating apt-get"
  sudo apt-get update -y
fi

for pkg in jq python3-pip python3-venv python3-dev build-essential clang curl ca-certificates chromium-browser chromium-chromedriver xvfb; do
  install_if_missing "$pkg"
done

# ================================
# PYTHON TOOLING
# ================================
echo "🔧 Setting up Python tooling"

# Use standard python3 (default on Ubuntu 24.04 is 3.12)
PYTHON_BIN=$(which python3)
POETRY_BIN="/home/$DEPLOY_USER/.local/bin/poetry"

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
    # Remove incompatible .venv if needed
    CUR_V=$( .venv/bin/python --version | awk '{print $2}' | cut -d. -f1,2 )
    SYS_V=$( "$PYTHON_BIN" --version | awk '{print $2}' | cut -d. -f1,2 )
    if [ "$CUR_V" != "$SYS_V" ]; then
      echo "🗑️ Removing incompatible .venv ($CUR_V vs $SYS_V)"
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