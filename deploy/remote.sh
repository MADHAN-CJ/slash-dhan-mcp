#!/usr/bin/env bash
# Operate the MCP box over SSH. The host is not committed: set DEPLOY_HOST (for example
# root@203.0.113.10) in your shell or in deploy/.host (git-ignored).
#
#   bash deploy/remote.sh setup              one-time box setup (clone, venv, unit, nginx, cert)
#   bash deploy/remote.sh deploy             redeploy origin/main
#   bash deploy/remote.sh token <client_id>  push today's Dhan token (read from stdin)
#   bash deploy/remote.sh feed               feed status
#   bash deploy/remote.sh status | logs | urls
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
HOST=${DEPLOY_HOST:-}
if [ -z "$HOST" ] && [ -f "$HERE/.host" ]; then
  HOST=$(tr -d '[:space:]' < "$HERE/.host")
fi
if [ -z "$HOST" ]; then
  echo "Set DEPLOY_HOST=user@host or write it to deploy/.host" >&2
  exit 2
fi

REMOTE_DIR=/root/slash-dhan-mcp-deploy
APP=/opt/slash-dhan-mcp
# Evaluated on the box: the admin secret and port never leave it.
ENV_VARS="A=\$(grep '^SLASH_DHAN_MCP_ADMIN_SECRET=' $APP/.env | cut -d= -f2); P=\$(grep '^SLASH_DHAN_MCP_PORT=' $APP/.env | cut -d= -f2)"
case "${1:-}" in
  setup)
    ssh "$HOST" "mkdir -p $REMOTE_DIR"
    scp "$HERE/setup-uat.sh" "$HERE/update.sh" "$HERE/slash-dhan-mcp.service" "$HERE/nginx.conf" "$HOST:$REMOTE_DIR/"
    ssh "$HOST" "bash $REMOTE_DIR/setup-uat.sh"
    ;;
  deploy)
    scp -q "$HERE/update.sh" "$HOST:$REMOTE_DIR/update.sh"
    ssh "$HOST" "bash $REMOTE_DIR/update.sh"
    ;;
  token)
    CLIENT_ID=${2:?usage: token <dhan_client_id>  (access token on stdin)}
    TOKEN=$(cat)
    printf '{"dhan_client_id":"%s","access_token":"%s"}' "$CLIENT_ID" "$TOKEN" | \
      ssh "$HOST" "$ENV_VARS; curl -sS -X PUT http://127.0.0.1:\$P/admin/feed/token -H \"Authorization: Bearer \$A\" -H 'Content-Type: application/json' --data-binary @-"
    echo
    ;;
  feed)
    ssh "$HOST" "$ENV_VARS; curl -sS http://127.0.0.1:\$P/admin/feed/status -H \"Authorization: Bearer \$A\""
    echo
    ;;
  urls)
    ssh "$HOST" "U=\$(grep '^SLASH_DHAN_MCP_PUBLIC_URL=' $APP/.env | cut -d= -f2); echo \"MCP:      \$U/\"; echo \"Watchers: \$U/watchers\""
    ;;
  logs)
    ssh "$HOST" 'journalctl -u slash-dhan-mcp -n 100 --no-pager -o cat'
    ;;
  status)
    ssh "$HOST" "$ENV_VARS; systemctl status slash-dhan-mcp --no-pager | head -5; curl -fsS http://127.0.0.1:\$P/health; echo"
    ;;
  *)
    echo "usage: $0 setup|deploy|token <client_id>|feed|urls|status|logs" >&2
    exit 2
    ;;
esac
