#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
docker build -t zwatch:2.0 .
mkdir -p data
chmod 700 data
if docker container inspect zwatch >/dev/null 2>&1; then
  docker stop zwatch >/dev/null
  docker rm zwatch >/dev/null
fi
docker run -d --name zwatch --restart unless-stopped --network host \
  --log-opt max-size=10m --log-opt max-file=3 \
  --cap-drop ALL --security-opt no-new-privileges --read-only \
  --tmpfs /tmp:rw,nosuid,size=128m --pids-limit 256 --memory 2g --cpus 1.5 \
  -v "$PWD:/workspace:ro" -v "$PWD/data:/workspace/data" \
  -e PYTHONDONTWRITEBYTECODE=1 -e ZWATCH_KEY_FILE=/workspace/data/.access-token zwatch:2.0

bash deploy-lab.sh
echo 'ZWatch listens on VM loopback port 38900. Connect through SSH forwarding.'
