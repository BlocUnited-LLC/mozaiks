#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ( $1 != pull && $1 != start ) ]]; then
  echo "usage: $0 pull|start" >&2
  exit 2
fi

bash "$(dirname "$0")/pull_public_image.sh" public.ecr.aws/docker/library/mongo:7 mongo:7
if [[ $1 == pull ]]; then
  exit 0
fi

docker run --detach --rm --name mozaiks-ci-mongodb --publish 27017:27017 \
  --health-cmd="mongosh --quiet --eval 'db.runCommand({ ping: 1 })'" \
  --health-interval=10s --health-timeout=5s --health-retries=5 \
  mongo:7

for ((attempt = 1; attempt <= 30; attempt++)); do
  if ! status=$(docker inspect --format '{{.State.Health.Status}}' mozaiks-ci-mongodb 2>/dev/null); then
    docker logs mozaiks-ci-mongodb >&2 || true
    echo "MongoDB container exited before readiness" >&2
    exit 1
  fi
  if [[ $status == healthy ]]; then
    exit 0
  fi
  if [[ $status == unhealthy ]]; then
    docker logs mozaiks-ci-mongodb >&2 || true
    echo "MongoDB failed its health check" >&2
    exit 1
  fi
  sleep 3
done

docker logs mozaiks-ci-mongodb >&2 || true
echo "MongoDB did not become healthy within 90 seconds" >&2
exit 1
