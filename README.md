# Metero: WeatherNext 3 hazard agent

An [abstract](https://github.com/dkatz23238/abstract-business-harness) profile
(engine 0.2.1). It tells agro and oil & gas managers which Argentine
departamentos face frost, heat, heavy rain or wind over the next 1-15 days,
and delivers HTML reports with interactive maps.

## What it shows

1. **abstract as an internal business harness**: the whole domain is in a
   profile directory: a prompt (`instructions.md`), one skill, six tools, UI
   strings. The engine provides the rest unchanged: users and logins, threads,
   plans, memory, a sandbox for tool calls, full Python for analysis, and
   reports.
2. **Next-generation forecasting**: WeatherNext 3 (Google DeepMind, Aug 2026)
   is a 64-member AI ensemble initialized hourly from live satellite data, on
   a 0.1° grid out to 15 days. The tools read its p10/p90 percentiles, so
   reports can say how likely a hazard is, not just whether it is forecast.
   The Google Weather API adds a 6-hour minute-by-minute precipitation
   nowcast.
3. **GKE in two scripts**: `deploy/gke/up.sh <name> <profile-dir>` puts any
   profile on GKE Autopilot at `https://<name>.endpoints.<project>.cloud.goog`
   with a Google-managed certificate. Profiles share one cluster and one load
   balancer, and each gets its own disk. `deploy/gke/down.sh` removes it.

## Layout

```
profile.toml, instructions.md, ui.json, memory_guidance.md
skills/hazard-reports/SKILL.md   hazard thresholds, sector scores, report structure
tools/weather_tools.py           the six tools (register(ctx))
tools/hazards.py                 daily severities per departamento, agro / oil & gas scores
tools/wn3_client.py              WeatherNext 3 Zarr reads, per-hour cache
tools/nowcast_client.py          Google Weather API (minutes, hours)
tools/map_render.py              inline Leaflet choropleths
assets/                          geoBoundaries ARG ADM2 polygons + 0.1° cell lookup
scripts/build_assets.py          rebuilds assets/ (one-off)
deploy/gke/                      Dockerfile, k8s templates, up.sh, down.sh
```

## Prerequisites

- A Google Cloud project with billing enabled. `up.sh` enables the APIs it
  needs (GKE, Artifact Registry, Cloud Build, Certificate Manager, Endpoints,
  Weather, API Keys, Compute).
- WeatherNext 3 access for your own Google account, requested through the
  WeatherNext data request form. Access is granted per account, not per
  project.
- An OpenAI API key.
- A checkout of [abstract](https://github.com/dkatz23238/abstract-business-harness)
  and [uv](https://docs.astral.sh/uv/) for local runs; `gcloud`, `kubectl`
  and `envsubst` for GKE.

Copy `.env.example` to `.env` and fill it in. `.env` is gitignored.

## Run locally

From your abstract checkout, with `PROFILE_DIR` pointing at this repository:

```bash
set -a; . "$PROFILE_DIR/.env"; set +a
uv run --with-requirements "$PROFILE_DIR/requirements.txt" \
  abstract serve --profile "$PROFILE_DIR" \
  --data-root "$PROFILE_DIR/data" --port 8811
```

Locally the WeatherNext tools use your application-default credentials, or
fall back to the `gcloud` user. Set `GOOGLE_WEATHER_API_KEY` for the nowcast
tools.

Each Zarr chunk is one global field for one hour (~20 MB). A 7-day run reads
about 330 of them, and the hours are cached under
`data/metero/wn3_cache/`. Off Google Cloud this is slow (minutes per
forecast day), so keep local tests to `days=1`. Inside us-east1 the reads
are in-region.

## Deploy to GKE

Once per machine:

```bash
sudo apt-get install google-cloud-cli-gke-gcloud-auth-plugin
gcloud auth login
gcloud auth application-default login     # the account allowlisted for WeatherNext
```

Then, from this repository:

```bash
export PROJECT=<your-project-id> OPENAI_API_KEY=sk-...

deploy/gke/up.sh metero .                          # first run ~15 min (cluster)
deploy/gke/up.sh example <abstract-checkout>/profiles/example   # a second profile, same cluster
deploy/gke/down.sh example                         # remove one profile and its disk
deploy/gke/down.sh metero --cluster                # remove everything
```

Both scripts stop before touching anything unless `PROJECT` is set
(explicitly, never the active `gcloud config` project, so a teardown cannot
hit the wrong project); `up.sh` also requires `OPENAI_API_KEY`. They default
to region `us-east1` (same region as
the WeatherNext bucket) and cluster `abstract`; override them with `REGION`
and `CLUSTER`. `up.sh` ends by printing the URL, the admin login, the API key
and the commands to check on the deployment. A new certificate needs 15-60
minutes before HTTPS works. Until then, use the printed `kubectl
port-forward` line.

Approximate cost while running: 0.10-0.20 USD per hour for the cluster fee,
one pod and the load balancer, plus OpenAI tokens per report.

## Security notes

This is a demo deployment. Read these before running it in an account that
matters.

- **Your Google credentials go into the cluster.** `up.sh` copies your
  application-default credentials file (a long-lived refresh token for your
  Google account, with your full cloud-platform scope) into the Kubernetes
  Secret `gcp-adc`, so the pod can read WeatherNext as you. Anyone who can
  read Secrets in that namespace, or who gets code execution in the pod,
  can act as your Google account. The proper fix is Workload Identity with a
  dedicated service account that only has WeatherNext read access, with that
  service account's email added through the WeatherNext request form.
  Revoke the copied token with `gcloud auth application-default revoke`
  when you tear the deployment down.
- **Local secrets live in `deploy/gke/.state/`.** `up.sh` writes the
  harness admin password and admin API key to `.state/<name>.env`, and the
  Google Weather API key to `.state/weather.key`, all mode 600. The folder is
  gitignored and excluded from the image build. Never commit, zip or copy it
  with the repository; rotate the keys if it leaks.
- **The Weather API key is restricted by API only.** It can call
  `weather.googleapis.com` and nothing else, but from any IP. Add an IP
  restriction in the Cloud Console (Credentials) if you need one.
- **The harness login is the only gate.** The app is public on the
  internet at `https://<name>.endpoints.<project>.cloud.goog`, with one
  replica and no IAP. The admin password and API key are printed at the end
  of every `up.sh` run, so keep that terminal output private.
- **Admin credentials only apply to a fresh data volume.** Changing
  `.state/<name>.env` later does not change the login of an existing
  deployment. To reset them, `down.sh` the profile (which deletes its data)
  and redeploy.
- **OpenAI and Anthropic keys are passed from your shell** into the
  `harness-env` Secret. They are billed to you for every user of the
  deployment.
- **Reports load Leaflet from unpkg.com and tiles from OpenStreetMap**, so
  report viewers' browsers contact those third parties.

## Known shortcuts (demo)

- The profile is baked into the image. Edits in the Admin tab are lost on
  the next rollout; change files here and rerun `up.sh`.
- Rain severity uses the ensemble mean, which understates convective peaks.

## Data and license

`assets/` is derived from [geoBoundaries](https://www.geoboundaries.org/)
(Argentina ADM2, gbOpen release; attribution required, see the
geoBoundaries metadata for the exact license; rebuild with
`scripts/build_assets.py`).
WeatherNext 3 and Google Weather data are subject to Google's terms. The code
is under the MIT license (see `LICENSE`).
