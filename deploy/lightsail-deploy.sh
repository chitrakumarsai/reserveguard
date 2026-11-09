#!/usr/bin/env bash
# Ships the working tree to the Lightsail server, builds the image there and restarts the app.
# Needs no AWS credentials, only the server's SSH key. Run after lightsail-provision.sh.
# Usage: deploy/lightsail-deploy.sh <server-ip> <public-https-url>
#   e.g. deploy/lightsail-deploy.sh 3.215.52.157 https://d448lcq306zwg.cloudfront.net
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${1:?server IP}"
SITE_URL="${2:?public https URL}"
KEY="${SSH_KEY:-$HOME/.ssh/reserveguard-lightsail.pem}"
on_server() { ssh -q -i "$KEY" -o StrictHostKeyChecking=accept-new "ec2-user@$HOST" "$@"; }

echo "Copying source"
# sandbox_sellers.json is git-ignored but baked into the image (see Dockerfile).
{ git ls-files; echo data/sandbox_sellers.json; } | COPYFILE_DISABLE=1 tar -czf - --no-xattrs -T - \
  | on_server 'rm -rf ~/app && mkdir ~/app && tar -xzf - -C ~/app 2>/dev/null'

echo "Copying .env to /etc/reserveguard.env (root only)"
on_server 'sudo install -m 600 -o root -g root /dev/stdin /etc/reserveguard.env' < .env

echo "Building image (a few minutes)"
on_server "cd ~/app && sudo DOCKER_BUILDKIT=1 docker build -q --build-arg SITE_URL=$SITE_URL -t reserveguard:latest ."

echo "Restarting"
on_server 'sudo docker rm -f reserveguard >/dev/null 2>&1 || true
  sudo docker run -d --name reserveguard --restart unless-stopped -p 80:8000 \
    --env-file /etc/reserveguard.env -v /var/lib/reserveguard:/var/lib/reserveguard \
    --memory 1200m --log-opt max-size=10m --log-opt max-file=3 reserveguard:latest >/dev/null
  for _ in $(seq 1 30); do curl -fsS -o /dev/null http://127.0.0.1/health 2>/dev/null && exit 0; sleep 2; done
  sudo docker logs --tail 30 reserveguard; exit 1'
echo "Healthy. Public check: $(curl -sS -o /dev/null -w '%{http_code}' "$SITE_URL/health") from $SITE_URL/health"
