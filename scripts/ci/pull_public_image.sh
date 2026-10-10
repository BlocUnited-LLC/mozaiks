#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 PUBLIC_IMAGE LOCAL_TAG" >&2
  exit 2
fi

image=$1
local_tag=$2
for attempt in 1 2 3 4 5 6; do
  if timeout 120s docker pull "$image"; then
    docker tag "$image" "$local_tag"
    exit 0
  fi
  if [[ $attempt -eq 6 ]]; then
    echo "Unable to pull $image after $attempt attempts" >&2
    exit 1
  fi
  delay=$(( (1 << (attempt - 1)) + RANDOM % 7 ))
  echo "Pull of $image failed (attempt $attempt/6); retrying in ${delay}s" >&2
  sleep "$delay"
done
