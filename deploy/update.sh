#!/usr/bin/env bash
# Redeploy the latest origin/main. Run as root on the box (bash deploy/remote.sh deploy does this
# over ssh). /opt/slash-dhan-mcp is disposable: it is reset hard to origin/main; .env is
# git-ignored and survives.
set -euo pipefail
APP=/opt/slash-dhan-mcp
BRANCH=${SLASH_DHAN_MCP_BRANCH:-main}
export UV_PYTHON_INSTALL_DIR=/var/lib/slashdhan/python
UNIT=slash-dhan-mcp
PORT=$(grep '^SLASH_DHAN_MCP_PORT=' "$APP/.env" | cut -d= -f2 || true)
PORT=${PORT:-8020}

git -C "$APP" fetch --quiet origin
git -C "$APP" checkout --quiet -B "$BRANCH" "origin/$BRANCH"
chown -R slashdhan:slashdhan "$APP"
sudo -u slashdhan -H bash -c "export UV_PYTHON_INSTALL_DIR=$UV_PYTHON_INSTALL_DIR; cd $APP && ~/.local/bin/uv sync --no-dev --frozen"
if [ -f "$APP/deploy/$UNIT.service" ]; then
  install -m 644 "$APP/deploy/$UNIT.service" "/etc/systemd/system/$UNIT.service"
  systemctl daemon-reload
fi
systemctl restart "$UNIT"
READY=false
for attempt in {1..30}; do
  if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/health" 2>/dev/null; then
    READY=true
    break
  fi
  sleep 1
done
if [ "$READY" != true ]; then
  systemctl status "$UNIT" --no-pager
  echo "MCP did not become healthy within the startup window" >&2
  exit 1
fi
echo
systemctl is-active "$UNIT"
git -C "$APP" log -1 --format='deployed %h %s'
