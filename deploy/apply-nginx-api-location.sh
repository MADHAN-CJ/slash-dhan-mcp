#!/usr/bin/env bash
# Run ON THE BOX as root: adds the /api/v1/ nginx location that forwards the Origin header
# (the root location strips it to protect the MCP transport). Idempotent; reload is zero-downtime.
set -euo pipefail
F=/etc/nginx/sites-enabled/dhan-mcp-test1
grep -q 'location /api/v1/' "$F" && { echo "already present"; exit 0; }
cp "$F" "/root/dhan-mcp-test1.nginx.bak-$(date +%s)"
python3 - "$F" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); s = p.read_text()
block = """    # The HTTP API serves browsers: pass Origin through so CORS preflights reach the app.
    location /api/v1/ {
        proxy_pass http://127.0.0.1:8020;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Origin $http_origin;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        client_max_body_size 1m;
    }
    location / {"""
assert s.count("    location / {") == 1
p.write_text(s.replace("    location / {", block, 1))
PY
nginx -t && systemctl reload nginx && echo "nginx reloaded"
