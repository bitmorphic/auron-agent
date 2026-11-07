#!/usr/bin/env bash
set -euo pipefail

echo "=== Creating user auron ==="
if id "auron" &>/dev/null; then
    echo "User auron already exists."
else
    useradd -m -s /bin/bash -c "AURON Application Service User" auron
fi

echo "=== Adding auron to docker and sudo groups ==="
usermod -aG docker auron
usermod -aG sudo auron

echo "=== Configuring passwordless sudo for auron ==="
echo "auron ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-auron
chmod 0440 /etc/sudoers.d/90-auron

echo "=== Setting up SSH authorized_keys for auron ==="
mkdir -p /home/auron/.ssh
chmod 700 /home/auron/.ssh
cat /root/.ssh/authorized_keys > /home/auron/.ssh/authorized_keys
chmod 600 /home/auron/.ssh/authorized_keys
chown -R auron:auron /home/auron/.ssh

echo "=== User auron setup complete ==="
id auron

