#!/usr/bin/env bash
# One-time setup on the uat box (run as root): service user, clone, venv, state dir, systemd
# unit, .env with fresh secrets (Resend settings copied from the kite service), nginx site +
# certificate. Re-runnable. Deploy files are read from the repo checkout when present, else from
# the directory this script was copied to.
set -euo pipefail
APP=/opt/slash-dhan-mcp
REPO=https://github.com/MADHAN-CJ/slash-dhan-mcp.git
BRANCH=${SLASH_DHAN_MCP_BRANCH:-main}
USER_NAME=slashdhan
STATE=/var/lib/slashdhan
export UV_PYTHON_INSTALL_DIR=$STATE/python
HOSTNAME_PUBLIC=${SLASH_DHAN_MCP_HOSTNAME:-dhan-mcp-test1.slash.markets}
PUBLIC_URL=https://$HOSTNAME_PUBLIC
PORT=8020
SITE=/etc/nginx/sites-available/dhan-mcp-test1
HERE=$(cd "$(dirname "$0")" && pwd)
KITE_ENV=/opt/zap-kite-mcp/.env

id -u "$USER_NAME" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$USER_NAME"
mkdir -p "$STATE" && chown "$USER_NAME:$USER_NAME" "$STATE" && chmod 750 "$STATE"

if [ ! -d "$APP/.git" ]; then
  git clone --quiet --branch "$BRANCH" "$REPO" "$APP"
fi
git config --global --add safe.directory "$APP"
chown -R "$USER_NAME:$USER_NAME" "$APP"

sudo -u "$USER_NAME" -H bash -c 'command -v ~/.local/bin/uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh'
mkdir -p "$UV_PYTHON_INSTALL_DIR" && chown "$USER_NAME:$USER_NAME" "$UV_PYTHON_INSTALL_DIR"
sudo -u "$USER_NAME" -H bash -c "export UV_PYTHON_INSTALL_DIR=$UV_PYTHON_INSTALL_DIR; cd $APP && ~/.local/bin/uv python install 3.12 && ~/.local/bin/uv sync --no-dev --frozen"

if [ ! -f "$APP/.env" ]; then
  RESEND=$(grep '^RESEND_API_KEY=' "$KITE_ENV" 2>/dev/null | cut -d= -f2- || true)
  FROM=$(grep '^EMAIL_FROM=' "$KITE_ENV" 2>/dev/null | cut -d= -f2- || true)
  cat > "$APP/.env" <<ENV
SLASH_DHAN_MCP_PUBLIC_URL=$PUBLIC_URL
SLASH_DHAN_MCP_HOST=127.0.0.1
SLASH_DHAN_MCP_PORT=$PORT
SLASH_DHAN_MCP_ADMIN_SECRET=$(openssl rand -hex 32)
CREDS_ENCRYPTION_KEY=$(openssl rand -hex 32)
SLASH_DHAN_MCP_STATE_DIR=$STATE
SLASH_DHAN_MCP_OPERATOR_EMAILS=anil@revise.network,madhan@revise.network
SWEEP_SECRET=$(openssl rand -hex 24)
RESEND_API_KEY=$RESEND
EMAIL_FROM=${FROM:-/markets <no-reply@example.com>}
ENV
  chown "$USER_NAME:$USER_NAME" "$APP/.env" && chmod 600 "$APP/.env"
  echo "wrote $APP/.env (secrets generated; Resend settings copied from $KITE_ENV)"
fi

UNIT_SRC=$APP/deploy/slash-dhan-mcp.service
[ -f "$UNIT_SRC" ] || UNIT_SRC=$HERE/slash-dhan-mcp.service
install -m 644 "$UNIT_SRC" /etc/systemd/system/slash-dhan-mcp.service
systemctl daemon-reload
systemctl enable --now slash-dhan-mcp

NGINX_SRC=$APP/deploy/nginx.conf
[ -f "$NGINX_SRC" ] || NGINX_SRC=$HERE/nginx.conf
if [ ! -f "$SITE" ]; then
  install -m 644 "$NGINX_SRC" "$SITE"
  ln -sf "$SITE" /etc/nginx/sites-enabled/dhan-mcp-test1
  nginx -t && systemctl reload nginx
fi
if [ ! -d "/etc/letsencrypt/live/$HOSTNAME_PUBLIC" ]; then
  certbot --nginx -d "$HOSTNAME_PUBLIC" --non-interactive --agree-tos --redirect --keep-until-expiring || \
    echo "certbot failed: run 'certbot --nginx -d $HOSTNAME_PUBLIC' by hand once DNS resolves"
fi

for attempt in {1..30}; do
  if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/health" 2>/dev/null; then echo; break; fi
  sleep 1
done
systemctl is-active slash-dhan-mcp
echo "MCP URL:      $PUBLIC_URL/"
echo "Watchers URL: $PUBLIC_URL/watchers"
echo "Push a token: bash deploy/remote.sh token <dhan_client_id>   (token on stdin)"
