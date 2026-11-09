#!/usr/bin/env bash
# Creates the AWS side once: a Lightsail server, its static IP, its firewall and an HTTPS
# distribution in front of it. Safe to run again: anything that exists is left as it is.
# Usage: AWS_PROFILE=<profile allowed to use Lightsail> deploy/lightsail-provision.sh
set -euo pipefail
cd "$(dirname "$0")/.."

export AWS_REGION="${AWS_REGION:-us-east-1}"
NAME=reserveguard
IP_NAME=reserveguard-ip
DIST_NAME=reserveguard-cdn   # Lightsail names are unique across resource types
BUNDLE=small_3_0            # 2 GB RAM, 60 GB disk, x86_64
DIST_BUNDLE=small_1_0       # 50 GB/month transfer
ls_() { aws lightsail "$@"; }

if ! ls_ get-instance --instance-name "$NAME" >/dev/null 2>&1; then
  echo "Creating server $NAME ($BUNDLE)"
  ls_ create-instances --instance-names "$NAME" --availability-zone "${AWS_REGION}a" \
    --blueprint-id amazon_linux_2023 --bundle-id "$BUNDLE" \
    --user-data "file://deploy/lightsail-launch.sh" --tags key=project,value=reserveguard >/dev/null
fi
echo "Waiting for $NAME to run"
until [ "$(ls_ get-instance --instance-name "$NAME" --query instance.state.name --output text)" = running ]; do sleep 5; done

if ! ls_ get-static-ip --static-ip-name "$IP_NAME" >/dev/null 2>&1; then
  ls_ allocate-static-ip --static-ip-name "$IP_NAME" >/dev/null
fi
if [ "$(ls_ get-static-ip --static-ip-name "$IP_NAME" --query staticIp.isAttached --output text)" != True ]; then
  ls_ attach-static-ip --static-ip-name "$IP_NAME" --instance-name "$NAME" >/dev/null
fi

# Web traffic on 80 for the distribution; SSH only from this machine's current address.
MY_IP="$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]')"
ls_ put-instance-public-ports --instance-name "$NAME" --port-infos \
  "fromPort=80,toPort=80,protocol=tcp,cidrs=0.0.0.0/0" \
  "fromPort=22,toPort=22,protocol=tcp,cidrs=${MY_IP}/32" >/dev/null

if ! ls_ get-distributions --distribution-name "$DIST_NAME" >/dev/null 2>&1; then
  echo "Creating HTTPS distribution $DIST_NAME"
  # Nothing is cached: the console and API are live. The API rejects forwardedHeaders "all"
  # (though documented), so the headers the app needs, Authorization above all, are listed.
  ls_ create-distribution --distribution-name "$DIST_NAME" --bundle-id "$DIST_BUNDLE" --ip-address-type ipv4 \
    --origin "name=$NAME,regionName=$AWS_REGION,protocolPolicy=http-only" \
    --default-cache-behavior behavior=dont-cache \
    --cache-behavior-settings '{"allowedHTTPMethods":"GET,HEAD,OPTIONS,PUT,PATCH,POST,DELETE","cachedHTTPMethods":"GET,HEAD","forwardedHeaders":{"option":"allow-list","headersAllowList":["Authorization","Accept","Accept-Encoding","Origin","Referer","CloudFront-Forwarded-Proto"]},"forwardedCookies":{"option":"all"},"forwardedQueryStrings":{"option":true}}' \
    --tags key=project,value=reserveguard >/dev/null
fi

echo "Server IP:   $(ls_ get-static-ip --static-ip-name "$IP_NAME" --query staticIp.ipAddress --output text)"
echo "Public site: https://$(ls_ get-distributions --distribution-name "$DIST_NAME" --query 'distributions[0].domainName' --output text)"
