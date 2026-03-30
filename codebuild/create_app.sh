#!/usr/bin/env bash
set -euo pipefail

# ================================
# REQUIRED ENV
# ================================
APP_NAME="${APP_NAME:?APP_NAME not set}"
# APP_SECRET_PATH may be optional for some apps, but we'll default to an empty string if not provided
APP_SECRET_PATH="${APP_SECRET_PATH:-}"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY not set}"

# ================================
# CONSTANTS
# ================================
BASE_DIR="/opt/apps"
APP_DIR="$BASE_DIR/$APP_NAME"
MANIFEST="$APP_DIR/codebuild/app.manifest.json"
PACKAGES_FILE="$APP_DIR/codebuild/packages.json"
DEPLOY_USER="ubuntu"

echo "➡ Creating app: $APP_NAME"

# ================================
# OS DEPENDENCIES (Ensures jq is available)
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

if [ ! -f "$PACKAGES_FILE" ]; then
  echo "❌ Missing codebuild/packages.json"
  exit 1
fi

# Read packages from config
CORE_PKGS=$(jq -r '.core | join(" ")' "$PACKAGES_FILE")
BROWSER_PKGS=$(jq -r '.browser | join(" ")' "$PACKAGES_FILE")
PYTHON_PKGS=$(jq -r '.python | join(" ")' "$PACKAGES_FILE")

# Check if we need to update apt
NEED_UPDATE=false
for pkg in $CORE_PKGS $BROWSER_PKGS $PYTHON_PKGS; do
  if ! dpkg -l "$pkg" &>/dev/null; then
    NEED_UPDATE=true
    break
  fi
done

if [ "$NEED_UPDATE" = true ]; then
  echo "⬆ Updating apt-get"
  sudo apt-get update -y
fi

# Install all dependencies
for pkg in $CORE_PKGS $BROWSER_PKGS $PYTHON_PKGS; do
  install_if_missing "$pkg"
done

cd "$APP_DIR"

# ================================
# READ MANIFEST
# ================================
if [ ! -f "$MANIFEST" ]; then
  echo "❌ Missing codebuild/app.manifest.json"
  exit 1
fi

RUNTIME=$(jq -r '.runtime' "$MANIFEST")
WORKDIR=$(jq -r '.working_dir' "$MANIFEST")
START_CMD=$(jq -r '.start_command' "$MANIFEST")
PORT=$(jq -r '.port' "$MANIFEST")
DOMAIN=$(jq -r '.domain' "$MANIFEST")
TIMEZONE=$(jq -r '.timezone // "Asia/Kolkata"' "$MANIFEST")

echo "🕒 Setting system timezone to $TIMEZONE"
sudo timedatectl set-timezone "$TIMEZONE" || echo "⚠️  Warning: Could not set system timezone"

APP_WORKDIR="$APP_DIR/$WORKDIR"

if [ ! -d "$APP_WORKDIR" ]; then
  echo "❌ working_dir does not exist: $APP_WORKDIR"
  exit 1
fi

cd "$APP_WORKDIR"

# Ensure correct permissions
sudo chown -R "$DEPLOY_USER:$DEPLOY_USER" "$APP_WORKDIR"
sudo chmod -R u+rwX,g+rwX "$APP_WORKDIR"

# ================================
# UNIVERSAL .ENV GENERATION
# ================================
echo "🔐 Generating .env file from manifest"
ENV_FILE="$APP_WORKDIR/.env"

# Use a temporary file for atomic write and better permission handling
TMP_ENV=$(mktemp)
# If env_vars is null or empty, jq returns nothing
ENV_VARS=$(jq -r '.env_vars // [] | .[]' "$MANIFEST")

for VAR in $ENV_VARS; do
    # Use Bash indirect expansion to get value of variable named $VAR
    VAL="${!VAR:-}"
    if [ -n "$VAL" ]; then
        echo "${VAR}=${VAL}" >> "$TMP_ENV"
    else
        echo "⚠️  Warning: $VAR is in manifest but not set in environment"
    fi
done

sudo cp "$TMP_ENV" "$ENV_FILE"
sudo chown "$DEPLOY_USER:$DEPLOY_USER" "$ENV_FILE"
sudo chmod 600 "$ENV_FILE"
rm -f "$TMP_ENV"

echo "  ✅ .env written with $(wc -l < "$ENV_FILE") variable(s)"

# ================================
# SSL PROVISIONING
# ================================
echo "🔐 Checking SSL certificates"
SSL_DIR="/etc/nginx/ssl"
if [ ! -f "$SSL_DIR/self.crt" ]; then
  echo "🎁 Generating self-signed certificate"
  sudo mkdir -p "$SSL_DIR"
  sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout "$SSL_DIR/self.key" -out "$SSL_DIR/self.crt" \
    -subj "/C=US/ST=State/L=City/O=Organization/CN=${DOMAIN}"
fi

# ================================
# RUNTIME SETUP (Python)
# ================================
if [ "$RUNTIME" = "python" ]; then
  echo "🐍 Python setup with Poetry"

  PYTHON_BIN=$(which python3)
  POETRY_BIN="/home/$DEPLOY_USER/.local/bin/poetry"

  # Install/Repair Poetry
  if ! sudo -u "$DEPLOY_USER" [ -x "$POETRY_BIN" ] || ! sudo -u "$DEPLOY_USER" "$POETRY_BIN" --version &>/dev/null; then
    echo "📥 Installing/Repairing Poetry using $PYTHON_BIN"
    curl -sSL https://install.python-poetry.org | sudo -u "$DEPLOY_USER" "$PYTHON_BIN" -
  fi

  export PATH="/home/$DEPLOY_USER/.local/bin:$PATH"

  # Configure Poetry
  sudo -u "$DEPLOY_USER" "$POETRY_BIN" config virtualenvs.in-project true

  # Handle .venv compatibility
  if [ -d ".venv" ]; then
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

  echo "📦 Installing Python dependencies"
  sudo -u "$DEPLOY_USER" "$POETRY_BIN" install --no-root --no-interaction

  if [ ! -d ".venv" ]; then
    echo "❌ .venv not created"
    exit 1
  fi

  if [ -f "manage.py" ]; then
    echo "🗄️ Django app detected. Running migrations..."
    sudo -u "$DEPLOY_USER" .venv/bin/python manage.py migrate --noinput

    echo "🎨 Collecting static files..."
    sudo -u "$DEPLOY_USER" .venv/bin/python manage.py collectstatic --noinput
  else
    echo "ℹ️ No manage.py found. Skipping Django-specific steps."
  fi
fi

# ================================
# RUNTIME SETUP (React)
# ================================
if [ "$RUNTIME" = "react" ]; then
  # If dist/ exists, we assume the app was pre-built (e.g. via GitHub Actions)
  if [ -d "dist" ] && [ "$(ls -A dist)" ]; then
    echo "✅ Found pre-built dist/ folder. Skipping build steps to save resources."
  elif [ -f "package.json" ]; then
    echo "📦 Installing NPM dependencies"
    sudo -u "$DEPLOY_USER" npm install

    echo "🏗️ Building React application"
    sudo -u "$DEPLOY_USER" npm run build
  else
    echo "⚠️ Warning: Runtime is react but no package.json or dist/ found."
  fi
fi

# ================================
# SYSTEMD (Only for persistent services)
# ================================
if [ "$RUNTIME" = "python" ]; then
  echo "🔧 Creating systemd service"
  sudo tee "/etc/systemd/system/${APP_NAME}.service" > /dev/null <<EOF
[Unit]
Description=${APP_NAME}
After=network.target

[Service]
User=ubuntu
WorkingDirectory=${APP_WORKDIR}
UMask=0002

$(if [ -n "$APP_SECRET_PATH" ]; then echo "Environment=APP_SECRET_JSON=${APP_SECRET_PATH}"; fi)
EnvironmentFile=-${APP_WORKDIR}/.env
Environment=TZ=${TIMEZONE}
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
fi

# ================================
# NGINX CONFIGURATION
# ================================
echo "🌐 Generating nginx config for $DOMAIN"

if [ "$RUNTIME" = "react" ]; then
  # React: Serve static files from /dist
  NGINX_LOCATIONS="
  location / {
    root ${APP_WORKDIR}/dist;
    index index.html;
    try_files \$uri \$uri/ /index.html;
  }"
else
  # Python: Proxy to the local port + static/media
  NGINX_LOCATIONS="
  location /static/ {
    alias ${APP_WORKDIR}/staticfiles/;
    expires 30d;
    add_header Cache-Control \"public, max-age=2592000\";
  }

  location /media/ {
    alias ${APP_WORKDIR}/media/;
    expires 30d;
    add_header Cache-Control \"public, max-age=2592000\";
  }

  location / {
    proxy_pass http://127.0.0.1:${PORT};
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
  }"
fi

sudo tee "/etc/nginx/sites-available/${DOMAIN}" > /dev/null <<EOF
server {
  listen 80;
  server_name ${DOMAIN};

  ${NGINX_LOCATIONS}
}

server {
  listen 443 ssl;
  server_name ${DOMAIN};

  ssl_certificate /etc/nginx/ssl/self.crt;
  ssl_certificate_key /etc/nginx/ssl/self.key;

  ssl_protocols TLSv1.2 TLSv1.3;
  ssl_ciphers HIGH:!aNULL:!MD5;

  ${NGINX_LOCATIONS}
}
EOF

sudo ln -sf "/etc/nginx/sites-available/${DOMAIN}" "/etc/nginx/sites-enabled/${DOMAIN}"

sudo nginx -t
sudo systemctl reload nginx

echo "✅ App created/updated successfully"
echo "🌐 URL: http://${DOMAIN}"
