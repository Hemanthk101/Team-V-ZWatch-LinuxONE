#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Use the same reviewed Python runtime. Each service has an independent process,
# resource limit, read-only application mount, and loopback-only listener.
mkdir -p data/transaction-lab
chmod 700 data/transaction-lab
docker build -t zwatch:2.0 .
for service in zwatch-auth zwatch-lab; do
  if docker container inspect "$service" >/dev/null 2>&1; then
    docker stop "$service" >/dev/null
    docker rm "$service" >/dev/null
  fi
done
docker run -d --name zwatch-auth --restart unless-stopped --network host \
  --log-opt max-size=10m --log-opt max-file=3 \
  --cap-drop ALL --security-opt no-new-privileges --read-only \
  --tmpfs /tmp:rw,nosuid,size=32m --pids-limit 64 --memory 256m --cpus 0.3 \
  -v "$PWD:/workspace:ro" -v "$PWD/data/transaction-lab:/workspace/data/transaction-lab" \
  -e PYTHONDONTWRITEBYTECODE=1 zwatch:2.0 \
  python -m uvicorn transaction_lab.authapp:app --host 127.0.0.1 --port 38902 --workers 1 --no-server-header
docker run -d --name zwatch-lab --restart unless-stopped --network host \
  --log-opt max-size=10m --log-opt max-file=3 \
  --cap-drop ALL --security-opt no-new-privileges --read-only \
  --tmpfs /tmp:rw,nosuid,size=32m --pids-limit 96 --memory 384m --cpus 0.8 \
  -v "$PWD:/workspace:ro" -v "$PWD/data/transaction-lab:/workspace/data/transaction-lab" \
  -e PYTHONDONTWRITEBYTECODE=1 zwatch:2.0 \
  python -m uvicorn transaction_lab.labapp:app --host 127.0.0.1 --port 38901 --workers 1 --no-server-header
echo 'Laboratory listeners: 127.0.0.1:38901 and 127.0.0.1:38902. Open /transactions through the existing dashboard tunnel.'
