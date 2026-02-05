#!/usr/bin/env bash
set -euo pipefail

# Targeted cleanup for Hue Gateway-related Docker artifacts.
# - Removes containers/images for this repo's compose project.
# - Removes known legacy compose project artifacts.
# - Does NOT remove volumes (keeps /data sqlite).
#
# Usage:
#   bash scripts/docker-clean-hue-gateway.sh
#   DRY_RUN=1 bash scripts/docker-clean-hue-gateway.sh

DRY_RUN="${DRY_RUN:-0}"

run() {
  echo "+ $*" >&2
  if [[ "$DRY_RUN" == "1" ]]; then
    return 0
  fi
  "$@"
}

remove_containers_by_filter() {
  local filter="$1"
  local ids
  ids="$(docker ps -a -q --filter "$filter" || true)"
  if [[ -n "$ids" ]]; then
    # shellcheck disable=SC2086
    run docker rm -f $ids
  fi
}

remove_images_by_filter() {
  local filter="$1"
  local ids
  ids="$(docker images -q --filter "$filter" || true)"
  if [[ -n "$ids" ]]; then
    # shellcheck disable=SC2086
    run docker image rm -f $ids
  fi
}

remove_images_by_ref() {
  local ref="$1"
  local ids
  ids="$(docker images -q "$ref" 2>/dev/null || true)"
  if [[ -n "$ids" ]]; then
    # shellcheck disable=SC2086
    run docker image rm -f $ids
  fi
}

echo "==> Hue Gateway Docker cleanup (no volumes)" >&2

# 1) Stop/remove containers for the *current repo* compose stack (if any).
if [[ -f "docker-compose.yml" ]]; then
  run docker compose down --remove-orphans
fi

# 2) Remove containers from known legacy compose project(s).
remove_containers_by_filter "label=com.docker.compose.project=2026-01-26-hue-api"

# 3) Remove any remaining containers running legacy hue-gateway images (covers renamed/random containers).
remove_containers_by_filter "ancestor=2026-01-26-hue-api-hue-gateway"

# 4) Remove images built for this project (labelled in Dockerfile/compose).
remove_images_by_filter "label=com.rrk.project=hue-gateway"

# 5) Remove known legacy images.
remove_images_by_ref "2026-01-26-hue-api-hue-gateway"

# 6) Remove any remaining images from the legacy compose project (incl. dangling api-docs builds).
remove_images_by_filter "label=com.docker.compose.project=2026-01-26-hue-api"

echo "==> Done." >&2
