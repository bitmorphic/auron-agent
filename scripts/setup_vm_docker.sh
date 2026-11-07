#!/usr/bin/env bash
set -euo pipefail

echo "=== Updating apt packages ==="
apt-get update
apt-get install -y ca-certificates curl gnupg lsb-release

echo "=== Setting up Docker Apt repository ==="
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "=== Enabling and starting Docker ==="
systemctl enable --now docker

echo "=== Docker version check ==="
docker --version
docker info --format 'Server Version: {{.ServerVersion}}, Storage Driver: {{.Driver}}'

