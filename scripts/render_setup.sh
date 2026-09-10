#!/usr/bin/env bash
set -euo pipefail

# render_setup.sh
# Helper script to set environment variables for Render services and trigger deploys.
#
# Usage:
#   export RENDER_API_KEY=your_render_api_key
#   ./scripts/render_setup.sh
#
# Notes:
# - This script uses the Render REST API. It will look up services by name
#   (`storefinder-backend` and `storefinder-frontend`) and set env vars on the
#   backend service. It does not create services or databases; those should be
#   created in the Render dashboard or via `render.yaml` prior to running.
# - You will be prompted for the DATABASE_URL (internal), JWT_SECRET_KEY, and
#   CORS_ALLOWED_ORIGINS. Do NOT paste secrets into public places.

API_URL="https://api.render.com/v1"

if [ -z "${RENDER_API_KEY:-}" ]; then
  echo "Please set RENDER_API_KEY environment variable and re-run."
  echo "Get one from https://dashboard.render.com/account"
  exit 1
fi

auth_header="Authorization: Bearer ${RENDER_API_KEY}"

read -rp "Backend service name (default: storefinder-backend): " BACKEND_NAME
BACKEND_NAME=${BACKEND_NAME:-storefinder-backend}

read -rp "Frontend service name (default: storefinder-frontend): " FRONTEND_NAME
FRONTEND_NAME=${FRONTEND_NAME:-storefinder-frontend}

read -rp "Internal DATABASE_URL (for backend on Render): " DATABASE_URL
read -rp "JWT_SECRET_KEY (paste a long random secret): " JWT_SECRET_KEY
read -rp "CORS_ALLOWED_ORIGINS (comma-separated): " CORS_ALLOWED_ORIGINS

echo "Looking up services..."
services_json=$(curl -s -H "$auth_header" "$API_URL/services")

backend_id=$(echo "$services_json" | jq -r --arg name "$BACKEND_NAME" '.[] | select(.name==$name) | .id')
frontend_id=$(echo "$services_json" | jq -r --arg name "$FRONTEND_NAME" '.[] | select(.name==$name) | .id')

if [ -z "$backend_id" ] || [ "$backend_id" = "null" ]; then
  echo "Backend service '$BACKEND_NAME' not found in your Render account."
  echo "Ensure the service exists (Render dashboard or render.yaml) and try again."
  exit 2
fi

echo "Found backend service id: $backend_id"
if [ -n "$frontend_id" ] && [ "$frontend_id" != "null" ]; then
  echo "Found frontend service id: $frontend_id"
else
  echo "Frontend service '$FRONTEND_NAME' not found; continuing anyway."
fi

set_env_var() {
  local service_id=$1 key=$2 value=$3
  # Check existing env vars
  existing=$(curl -s -H "$auth_header" "$API_URL/services/$service_id/env-vars")
  var_id=$(echo "$existing" | jq -r --arg k "$key" '.[] | select(.key==$k) | .id')
  if [ -n "$var_id" ] && [ "$var_id" != "null" ]; then
    echo "Updating env var $key on service $service_id"
    curl -s -X PATCH -H "$auth_header" -H "Content-Type: application/json" \
      -d "{\"value\": \"$value\"}" \
      "$API_URL/services/$service_id/env-vars/$var_id" > /dev/null
  else
    echo "Creating env var $key on service $service_id"
    curl -s -X POST -H "$auth_header" -H "Content-Type: application/json" \
      -d "{\"key\": \"$key\", \"value\": \"$value\", \"secure\": true}" \
      "$API_URL/services/$service_id/env-vars" > /dev/null
  fi
}

echo "Setting environment variables on backend..."
set_env_var "$backend_id" "DATABASE_URL" "$DATABASE_URL"
set_env_var "$backend_id" "JWT_SECRET_KEY" "$JWT_SECRET_KEY"
set_env_var "$backend_id" "CORS_ALLOWED_ORIGINS" "$CORS_ALLOWED_ORIGINS"

echo "Triggering a manual deploy for backend..."
curl -s -X POST -H "$auth_header" -H "Content-Type: application/json" -d '{}' "$API_URL/services/$backend_id/deploys" >/dev/null

echo "Done. Backend env vars updated and deploy triggered. Monitor the Render dashboard for build logs and the new service URL."

echo "Next steps:"
echo " - Set Streamlit secrets: BACKEND_URL=https://<your-backend-host>, JWT_SECRET_KEY (same value)"
echo " - Run: python backend/smoke_test.py https://<your-backend-host>"
