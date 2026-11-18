#!/usr/bin/env bash
# Usage: deploy/gke/up.sh <name> <profile-dir> [extra.env]
#
# Puts an abstract profile on GKE Autopilot behind
# https://<name>.endpoints.<project>.cloud.goog. Safe to rerun: the shared
# pieces (cluster, IP, Gateway, certificate map) are created once, and every
# run rebuilds the profile image and rolls it out. Several names share one
# cluster and one load balancer.
#
# Required in the shell: PROJECT (Google Cloud project id) and OPENAI_API_KEY.
#
# Secrets: OPENAI_API_KEY and optional ANTHROPIC_API_KEY from the shell, the admin password and API key generated once into
# deploy/gke/.state/<name>.env, a Weather API key created once, and your
# gcloud application-default credentials for WeatherNext (allowlisted per
# Google account). extra.env adds more KEY=VALUE lines.
set -euo pipefail

NAME=${1:?usage: up.sh <name> <profile-dir> [extra.env]}
PROFILE_DIR=$(cd "${2:?usage: up.sh <name> <profile-dir> [extra.env]}" && pwd)
EXTRA=${3:-}
: "${PROJECT:?set PROJECT to your Google Cloud project id, e.g. PROJECT=my-project $0 ...}"
: "${OPENAI_API_KEY:?set OPENAI_API_KEY in the shell (export OPENAI_API_KEY=sk-...)}"
REGION=${REGION:-us-east1} # WeatherNext 3 bucket region: reads stay in-region
CLUSTER=${CLUSTER:-abstract}
HARNESS_IMAGE=${HARNESS_IMAGE:-dkatz23238/abstract-business-harness:0.2.1}

HERE=$(cd "$(dirname "$0")" && pwd)
STATE=$HERE/.state
HOST=$NAME.endpoints.$PROJECT.cloud.goog
REPO=$REGION-docker.pkg.dev/$PROJECT/harness
TAG=$(date -u +%Y%m%d%H%M%S)
IMAGE=$REPO/$NAME:$TAG
ADC=${GOOGLE_APPLICATION_CREDENTIALS:-$HOME/.config/gcloud/application_default_credentials.json}

[[ $NAME =~ ^[a-z][a-z0-9-]{0,30}$ ]] || { echo "name must be lowercase letters, digits, dashes" >&2; exit 1; }
[ -f "$PROFILE_DIR/profile.toml" ] || { echo "no profile.toml in $PROFILE_DIR" >&2; exit 1; }
for bin in gcloud kubectl envsubst openssl gke-gcloud-auth-plugin; do
  command -v $bin >/dev/null || { echo "missing $bin (gke-gcloud-auth-plugin: sudo apt-get install google-cloud-cli-gke-gcloud-auth-plugin)" >&2; exit 1; }
done

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
g() { gcloud --project "$PROJECT" --quiet "$@"; }
exists() { g "$@" >/dev/null 2>&1; }

log "APIs on $PROJECT"
g services enable container.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  certificatemanager.googleapis.com servicemanagement.googleapis.com servicecontrol.googleapis.com \
  endpoints.googleapis.com weather.googleapis.com apikeys.googleapis.com compute.googleapis.com

log "Cluster $CLUSTER ($REGION, Autopilot)"
exists container clusters describe "$CLUSTER" --region "$REGION" \
  || g container clusters create-auto "$CLUSTER" --region "$REGION"
g container clusters get-credentials "$CLUSTER" --region "$REGION"

log "Shared ingress: IP, certificate map, Gateway"
exists artifacts repositories describe harness --location "$REGION" \
  || g artifacts repositories create harness --location "$REGION" --repository-format docker
exists compute addresses describe harness-gateway --global \
  || g compute addresses create harness-gateway --global
IP=$(g compute addresses describe harness-gateway --global --format 'value(address)')
exists certificate-manager maps describe harness-certs \
  || g certificate-manager maps create harness-certs
kubectl apply -f "$HERE/k8s/gateway.yaml"

log "DNS and certificate for $HOST -> $IP"
dns=$(mktemp --suffix .yaml)
cat >"$dns" <<EOF
swagger: "2.0"
info: {title: "$NAME harness", version: "1.0.0"}
paths: {}
host: "$HOST"
x-google-endpoints:
  - name: "$HOST"
    target: "$IP"
EOF
# A deleted Endpoints name stays reserved for 30 days; bring it back instead.
g endpoints services undelete "$HOST" >/dev/null 2>&1 || true
g endpoints services deploy "$dns" >/dev/null
rm -f "$dns"
exists certificate-manager certificates describe "$NAME-cert" \
  || g certificate-manager certificates create "$NAME-cert" --domains "$HOST"
exists certificate-manager maps entries describe "$NAME" --map harness-certs \
  || g certificate-manager maps entries create "$NAME" --map harness-certs \
       --certificates "$NAME-cert" --hostname "$HOST"

log "Secrets"
mkdir -p -m 700 "$STATE"
env_file=$STATE/$NAME.env
if [ ! -f "$env_file" ]; then
  # Read by the harness only on the first start of an empty data volume.
  (umask 077 && printf 'HARNESS_ADMIN_PASSWORD=%s\nHARNESS_ADMIN_API_KEY=bh_%s\n' \
    "$(openssl rand -hex 12)" "$(openssl rand -hex 24)" >"$env_file")
fi
if [ ! -s "$STATE/weather.key" ]; then
  g services api-keys create --display-name harness-weather \
    --api-target service=weather.googleapis.com --format 'value(response.keyString)' 2>/dev/null \
    | (umask 077 && cat >"$STATE/weather.key")
fi
secret_env=$(mktemp)
chmod 600 "$secret_env"
grep -v -E '^(OPENAI_API_KEY|ANTHROPIC_API_KEY|GOOGLE_WEATHER_API_KEY)=' "$env_file" >"$secret_env" || true
echo "OPENAI_API_KEY=$OPENAI_API_KEY" >>"$secret_env"
[ -n "${ANTHROPIC_API_KEY:-}" ] && echo "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" >>"$secret_env"
echo "GOOGLE_WEATHER_API_KEY=$(cat "$STATE/weather.key")" >>"$secret_env"
[ -n "$EXTRA" ] && grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$EXTRA" >>"$secret_env"
kubectl create namespace "$NAME" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NAME" create secret generic harness-env --from-env-file "$secret_env" \
  --dry-run=client -o yaml | kubectl apply -f -
rm -f "$secret_env"
if [ -f "$ADC" ]; then
  kubectl -n "$NAME" create secret generic gcp-adc --from-file adc.json="$ADC" \
    --dry-run=client -o yaml | kubectl apply -f -
else
  echo "warning: no $ADC; run 'gcloud auth application-default login' so the pod can read WeatherNext" >&2
fi

log "Image $IMAGE (Cloud Build)"
stage=$(mktemp -d)
tar -C "$PROFILE_DIR" --exclude .git --exclude ./data --exclude .env --exclude deploy/gke/.state \
  --exclude __pycache__ -cf - . | tar -C "$stage" -xf -
cp "$HERE/Dockerfile" "$stage/Dockerfile"
sed -i "s|^ARG HARNESS_IMAGE=.*|ARG HARNESS_IMAGE=$HARNESS_IMAGE|" "$stage/Dockerfile"
g builds submit "$stage" --region "$REGION" --tag "$IMAGE"
rm -rf "$stage"

log "Rollout"
export NAME HOST IMAGE
envsubst '$NAME $HOST $IMAGE' <"$HERE/k8s/profile.yaml" | kubectl apply -f -
kubectl -n "$NAME" rollout status statefulset/harness --timeout 15m

cert_state=$(g certificate-manager certificates describe "$NAME-cert" --format 'value(managed.state)')
# The load balancer needs a minute or two to see a new pod as healthy.
for _ in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://$HOST/healthz" || true)
  [ "$code" = 200 ] && break
  [ "$cert_state" = ACTIVE ] || break
  sleep 10
done
set -a; . "$env_file"; set +a
cat <<EOF

────────────────────────────────────────────────────────────────────
  $NAME is deployed
────────────────────────────────────────────────────────────────────
  URL            https://$HOST
  status         certificate $cert_state, https /healthz -> ${code:-no answer}
                 (a new certificate needs ~15-60 min after the first
                 deploy; until then the URL fails TLS)
  sign in        admin / $HARNESS_ADMIN_PASSWORD
  API key        $HARNESS_ADMIN_API_KEY
                 (both only apply to a fresh data volume; they live in
                 $env_file)
  image          $IMAGE

  check          curl -s https://$HOST/healthz
                 kubectl -n $NAME get pods,pvc,httproute
                 kubectl -n $NAME logs statefulset/harness -f
                 gcloud certificate-manager certificates describe $NAME-cert --project $PROJECT
  local tunnel   kubectl -n $NAME port-forward svc/harness 8811  -> http://localhost:8811
  tear down      $HERE/down.sh $NAME            (this profile and its disk)
                 $HERE/down.sh $NAME --cluster  (everything, cluster included)
────────────────────────────────────────────────────────────────────
EOF
