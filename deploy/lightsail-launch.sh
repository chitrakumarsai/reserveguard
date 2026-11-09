#!/bin/bash
# First-boot script for the Lightsail server (Amazon Linux 2023): Docker and the ledger's folder.
set -euo pipefail
dnf install -y docker
systemctl enable --now docker
usermod -aG docker ec2-user
# The container runs as uid 10001; only it may read or write the ledger.
install -d -o 10001 -g 10001 -m 700 /var/lib/reserveguard
# 2 GB of swap so the Next.js build in lightsail-deploy.sh fits on a 2 GB server.
dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
echo "/swapfile swap swap defaults 0 0" >> /etc/fstab
