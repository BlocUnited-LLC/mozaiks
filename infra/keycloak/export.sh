#!/usr/bin/env bash
# infra/keycloak/export.sh
#
# Export the current Keycloak realm configuration to
# factory_app/app/brand/realm-export.json for version control.
#
# Requires a running Keycloak instance reachable at KEYCLOAK_URL.
# Credentials default to the local dev values from infra/compose/.env or
# environment variables.
#
# Usage:
#   ./infra/keycloak/export.sh
#   KEYCLOAK_URL=http://localhost:8080 ./infra/keycloak/export.sh
#
# Environment variables:
#   KEYCLOAK_URL      Base URL of Keycloak (default: http://localhost:8080)
#   KEYCLOAK_REALM    Realm to export (default: mozaiks)
#   KEYCLOAK_ADMIN    Admin username (default: admin)
#   KEYCLOAK_ADMIN_PASSWORD  Admin password (default: admin)
#   EXPORT_DEST       Output file path (default: factory_app/app/brand/realm-export.json)

set -euo pipefail

KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:8080}"
REALM="${KEYCLOAK_REALM:-mozaiks}"
ADMIN="${KEYCLOAK_ADMIN:-admin}"
ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD:-admin}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
EXPORT_DEST="${EXPORT_DEST:-${REPO_ROOT}/factory_app/app/brand/realm-export.json}"

echo "[keycloak-export] Authenticating with Keycloak at ${KEYCLOAK_URL} ..."

TOKEN_RESPONSE=$(curl -sf \
  -X POST "${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=password" \
  -d "client_id=admin-cli" \
  -d "username=${ADMIN}" \
  -d "password=${ADMIN_PASSWORD}")

ACCESS_TOKEN=$(echo "${TOKEN_RESPONSE}" | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")

echo "[keycloak-export] Exporting realm '${REALM}' ..."

curl -sf \
  -X GET "${KEYCLOAK_URL}/admin/realms/${REALM}" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Accept: application/json" \
  | python3 -m json.tool --sort-keys \
  > "${EXPORT_DEST}"

echo "[keycloak-export] Realm config written to: ${EXPORT_DEST}"
echo "[keycloak-export] Commit this file to keep the realm config under version control."
