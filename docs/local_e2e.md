# Local E2E Verification

How to verify the SDK end to end against the local platform stack from the
`gts-local-environment` repository. The environment's own bring-up (compose
files, first-boot initialisation, seeded identities) is documented in that
repository's README — this guide assumes the stack is up and covers the SDK
side only.

The lifecycle script [examples/quick_files.py](../examples/quick_files.py) is
the verification vehicle: it exercises every files-API method with assertions
(list → create folder → upload → list → signed URL → `gtsf://` resolution →
byte-compared download → delete file → delete folder), exits non-zero on any
failure, and cleans up unconditionally so reruns are idempotent.

## Step 0 — Environment pre-check (seconds)

```bash
curl -s http://localhost:13001/api/actuator/_health
# expect: {"status":"ok"}
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/realms/agency/.well-known/openid-configuration
# expect: 200
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:9000/minio/health/live
# expect: 200
```

## Step 1 — Extract the seeded credentials

The local environment seeds an Active API key bound to the realm client
`api-key-2c018ce7-…`; its secret ships in the realm export. These are shell
variables for the current terminal session only — rerun in every new terminal,
and run steps 2/4/5 in the same session.

```bash
cd /path/to/gts-local-environment
CLIENT_ID="api-key-2c018ce7-ffc2-481e-81ef-a65e07c31b61"
CLIENT_SECRET=$(python3 -c "import json; r=json.load(open('configurations/keycloak/realm-export.json')); print([c for c in r['clients'] if c.get('clientId')=='$CLIENT_ID'][0]['secret'])")
echo "secret length: ${#CLIENT_SECRET}"   # non-zero means it worked
```

## Step 2 — Auth-chain smoke test (no SDK involved)

Isolates the environment from the SDK: a raw token exchange plus one API call.

```bash
TOKEN=$(curl -s -d "grant_type=client_credentials&client_id=$CLIENT_ID&client_secret=$CLIENT_SECRET" \
  http://localhost:8080/realms/agency/protocol/openid-connect/token \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")

curl -s -w "\nhttp: %{http_code}\n" -H "Authorization: Bearer $TOKEN" "http://localhost:13001/api/files?o=2"
# expect: {"page":{"page":0,"size":50,"total":N},"items":[...]} with http: 200
# 401 -> token/realm problem; 400 "User not supplied" -> MySQL seed problem
```

## Step 3 — Offline test suite (no environment needed)

Validates the SDK's wire contract, DTOs, and `gtsf://` parsing in isolation:

```bash
cd /path/to/gts-agency_python-sdk
.venv/bin/pytest -v
# expect: all tests pass
```

## Step 4 — Full E2E inside the docker network (recommended)

Presigned download URLs carry the in-network host `minio:9000`, so running the
script inside the compose network needs no host configuration:

```bash
docker run --rm --network gts-local-environment_default \
  -v /path/to/gts-agency_python-sdk:/sdk:ro -e PYTHONPATH=/sdk \
  -e AGENCY_AUTH_URL="http://keycloak:8080/realms/agency/protocol/openid-connect/token" \
  -e AGENCY_API_URL="http://agency-control-plane:13001" \
  -e AGENCY_ORG_ID="2" \
  -e AGENCY_CLIENT_ID="$CLIENT_ID" \
  -e AGENCY_CLIENT_SECRET="$CLIENT_SECRET" \
  python:3.12-slim sh -c "pip install -q requests pydantic pyjwt && python /sdk/examples/quick_files.py"
```

Expected output — nine steps and a zero exit code:

```
1. root listing: N entries
2. created folder 'sdk-e2e-<timestamp>'
3. uploaded sdk-e2e-<timestamp>/sample.bin (1024 bytes, id=file_...)
4. folder listing contains the upload (total=1)
5. signed URL expires at <now + 120 s>
6. gtsf://file_... resolved
7. downloaded bytes match the upload
8. deleted file
9. deleted folder 'sdk-e2e-<timestamp>'
ALL STEPS PASSED
```

## Step 5 (optional) — Run from the host

Everything except the presigned-URL download works out of the box from the
host. To make the download's `minio` hostname resolvable, map it once:

```bash
echo "127.0.0.1 minio" | sudo tee -a /etc/hosts
```

Then:

```bash
cd /path/to/gts-agency_python-sdk
AGENCY_AUTH_URL="http://localhost:8080/realms/agency/protocol/openid-connect/token" \
AGENCY_API_URL="http://localhost:13001" \
AGENCY_ORG_ID="2" \
AGENCY_CLIENT_ID="$CLIENT_ID" \
AGENCY_CLIENT_SECRET="$CLIENT_SECRET" \
.venv/bin/python examples/quick_files.py
```

## Troubleshooting

First stop for any failure:

```bash
docker logs agency-control-plane --since 5m 2>&1 | grep -iE "error|warn"
```

The HTTP status in step 2 triages most problems:

| Symptom | Likely cause |
|---|---|
| 401 on `/api/files` | token rejected — realm missing/not imported, or wrong client secret |
| 400 "User not supplied." | the JWT `sub` has no MySQL registration — environment seed not applied |
| 500 on upload | control plane → object storage failure — MinIO bucket/access key/hostname |
| `ConnectionError` host `minio` (step 7, host runs) | presigned-URL hostname not resolvable — see step 5 |
| 404 on `/api/files` | control-plane image predates the files feature — pull a newer image |
| 403 "HTTPS required" from Keycloak (host runs only; in-network fine) | Docker Desktop is mislabelling the source IP of host→container connections as a public address, so the realm's `sslRequired: external` rejects plain HTTP — a Docker Desktop networking-stack state fault (observed on 4.71 after a crash-restart; fixed by upgrading to 4.77) |

To confirm the 403 case, check what source address a container sees for a
host-originated connection — anything other than a private address (normally
`192.168.65.1`) means Docker needs an upgrade/restart, not Keycloak a config change:

```bash
docker run --rm -d --name srcprobe -p 18080:8000 python:3.12-slim python -m http.server 8000
curl -s -o /dev/null http://127.0.0.1:18080/ && sleep 1 && docker logs srcprobe | tail -1
docker rm -f srcprobe
```
