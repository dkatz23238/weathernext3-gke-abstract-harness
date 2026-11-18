#!/usr/bin/env bash
# Usage: deploy/gke/down.sh <name> [--cluster]
#
# Removes one profile: its namespace (pod, secrets, route, and the data disk:
# standard-rwo deletes the volume with its claim) and its certificate. The
# Endpoints DNS name is kept; it is free, and up.sh reuses it.
# --cluster also removes everything shared: Gateway, IP, certificate map,
# image repository and the Autopilot cluster.
set -euo pipefail

NAME=${1:?usage: down.sh <name> [--cluster]}
ALL=${2:-}
: "${PROJECT:?set PROJECT to your Google Cloud project id, e.g. PROJECT=my-project $0 ...}"
REGION=${REGION:-us-east1}
CLUSTER=${CLUSTER:-abstract}
HERE=$(cd "$(dirname "$0")" && pwd)

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
g() { gcloud --project "$PROJECT" --quiet "$@"; }

if g container clusters describe "$CLUSTER" --region "$REGION" >/dev/null 2>&1; then
  g container clusters get-credentials "$CLUSTER" --region "$REGION"
  log "Namespace $NAME (pod, secrets, route, data disk)"
  kubectl delete namespace "$NAME" --ignore-not-found --wait --timeout 10m
fi

log "Certificate for $NAME"
g certificate-manager maps entries delete "$NAME" --map harness-certs 2>/dev/null || true
g certificate-manager certificates delete "$NAME-cert" 2>/dev/null || true
# Admin password and key only matched the deleted volume.
rm -f "$HERE/.state/$NAME.env"

if [ "$ALL" = "--cluster" ]; then
  log "Shared ingress and cluster"
  kubectl delete namespace gateway --ignore-not-found --wait --timeout 10m 2>/dev/null || true
  g certificate-manager maps delete harness-certs 2>/dev/null || true
  g compute addresses delete harness-gateway --global 2>/dev/null || true
  g artifacts repositories delete harness --location "$REGION" 2>/dev/null || true
  g container clusters delete "$CLUSTER" --region "$REGION" 2>/dev/null || true
  echo "Other profiles' certificates (if any) remain: gcloud certificate-manager certificates list --project $PROJECT"
fi

log "Left in $PROJECT"
g compute disks list --filter "name~^pvc-" --format 'table(name,zone.basename(),sizeGb)' 2>/dev/null || true
g container clusters list --format 'table(name,location,status)' 2>/dev/null || true
echo "done"
