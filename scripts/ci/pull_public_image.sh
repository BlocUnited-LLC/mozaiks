#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 PUBLIC_IMAGE LOCAL_TAG" >&2
  exit 2
fi

image=$1
local_tag=$2

# ECR Public rate-limits anonymous pulls per source IP, and hosted runners share
# IPs, so a mirror of a Docker official image also tries Docker Hub's copy. The
# two registries have independent limits; either one serves the same image.
sources=("$image")
ecr_library=public.ecr.aws/docker/library/
if [[ $image == "$ecr_library"* ]]; then
  sources+=("docker.io/library/${image#"$ecr_library"}")
fi

for attempt in 1 2 3 4 5 6; do
  for source in "${sources[@]}"; do
    if timeout 120s docker pull "$source"; then
      docker tag "$source" "$local_tag"
      # Build steps reference the mirror name as a docker-image:// context.
      if [[ $source != "$image" ]]; then
        docker tag "$source" "$image"
      fi
      echo "Pulled $local_tag from $source" >&2
      exit 0
    fi
    echo "Pull of $source failed (attempt $attempt/6)" >&2
  done
  if [[ $attempt -eq 6 ]]; then
    echo "Unable to pull $image after $attempt attempts from: ${sources[*]}" >&2
    exit 1
  fi
  delay=$(( (1 << (attempt - 1)) + RANDOM % 7 ))
  echo "Retrying in ${delay}s" >&2
  sleep "$delay"
done
