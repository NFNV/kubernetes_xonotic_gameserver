# Xonotic Dedicated Server Container

This directory contains an initial container setup for the Xonotic dedicated server workload. It is intentionally narrow: a readable image build context plus a startup path that is suitable for local smoke testing and later platform integration work.

## What Is In Scope

- container image build for the dedicated server
- runtime entrypoint for startup and config generation
- a baseline `server.cfg` template
- documentation for build, run, ports, and config assumptions

## Current Intent

- intended v1 deployment target: `linux/amd64`
- local testing on Apple Silicon is a smoke test only, not final production validation
- no multi-arch image work is included yet

## What Is Not In Scope Yet

- full Agones SDK integration beyond the minimal phase-1 `Ready` hook
- production Kubernetes packaging beyond the minimal pre-Agones checkpoint and the first single-`GameServer` Agones phase
- broader CI/CD beyond a single manual image publish workflow
- observability stack
- custom maps, mods, or gameplay changes

## Files

- `Dockerfile`: multi-stage image build using the official Xonotic release archive
- `entrypoint.sh`: runtime startup logic
- `config/server.cfg`: baseline server configuration copied into the container's user data directory on first boot
- `.dockerignore`: trims local noise from the build context

## Ports

The container exposes:

- `26000/udp`: default Xonotic dedicated server port

This setup does not expose a separate TCP management port. RCON, if enabled in config, uses the server protocol path rather than a dedicated admin TCP listener.

For the GKE connectivity checkpoint, this UDP port is the only network contract that matters. The checkpoint intentionally avoids adding sidecars, ingress, or extra management surfaces.

## Runtime Behavior

On container start:

1. the entrypoint creates the Xonotic user data directory if it does not exist
2. the baseline `server.cfg` is copied into the user data directory on first boot if no custom file is already present
3. a generated `server.autoexec.cfg` is written based on environment variables
4. the dedicated server binary starts and loads `server.cfg`, then `server.autoexec.cfg`
5. when explicitly enabled for Agones, the entrypoint waits for the Xonotic UDP port to be bound and only then sends the one-time `Ready` call to the local Agones SDK sidecar

This keeps the baseline config readable in Git while still allowing simple runtime overrides.

## Configuration Assumptions

- official Xonotic release archive is downloaded at build time
- persistent runtime data lives under `/home/xonotic/.xonotic`
- the server reads `server.cfg` from the Xonotic user data directory
- environment variables are used only for a small set of operational overrides
- anything more complex should move into a mounted custom `server.cfg` later
- the current implementation assumes the official release archive contains `/opt/xonotic/xonotic-linux64-dedicated`

## Supported Environment Variables

- `XONOTIC_PORT`: server UDP port, default `26000`
- `XONOTIC_HOSTNAME`: server name shown to clients
- `XONOTIC_MAXPLAYERS`: player limit, default `12`
- `XONOTIC_PUBLIC`: `0` or `1`, default `0`
- `XONOTIC_MOTD`: message of the day
- `XONOTIC_LOG_FILE`: log file path inside the Xonotic data dir, default `server.log`
- `XONOTIC_RCON_PASSWORD`: optional RCON password
- `XONOTIC_START_MAP`: exact startup map to use; takes precedence over random selection
- `XONOTIC_RANDOM_START_MAP_ENABLE`: set to `1` to choose one startup map randomly from `XONOTIC_MAP_POOL`, default `0`
- `XONOTIC_MAP_POOL`: whitespace-separated startup map pool for random selection, default `xoylent stormkeep darkzone drain`
- `XONOTIC_MAP`: legacy optional startup map fallback; used only if `XONOTIC_START_MAP` is unset and random selection is disabled
- `XONOTIC_EXTRA_CFG`: optional raw config lines appended to the generated autoexec file; use it only as a convenience or debug override, not as the long-term config mechanism
- `XONOTIC_AGONES_READY_ENABLE`: set to `1` only when running under Agones phase 1 so the entrypoint sends a local `Ready` call, default `0`
- `XONOTIC_AGONES_READY_DELAY_SECONDS`: delay before the Agones `Ready` call, default `10`
- `XONOTIC_AGONES_READY_ATTEMPTS`: retry count for the Agones `Ready` call, default `30`
- `XONOTIC_AGONES_PORT_BIND_TIMEOUT_SECONDS`: maximum time to wait for the Xonotic UDP socket to bind before failing startup, default `60`

## Build

From the repository root, for the intended v1 target architecture:

```bash
docker build --platform linux/amd64 -t xonotic-server:local ./server
```

Optional version override:

```bash
docker build \
  --platform linux/amd64 \
  --build-arg XONOTIC_VERSION=0.8.6 \
  -t xonotic-server:local \
  ./server
```

## Run

Minimal local run:

```bash
docker run --rm -it \
  --platform linux/amd64 \
  -p 26000:26000/udp \
  xonotic-server:local
```

Run with a named server and a persistent local directory for local development only:

```bash
docker run --rm -it \
  --platform linux/amd64 \
  -p 26000:26000/udp \
  -e XONOTIC_HOSTNAME="Xonotic MVP Server" \
  -e XONOTIC_PUBLIC=0 \
  -e XONOTIC_MAXPLAYERS=8 \
  -v "$PWD/.local/xonotic:/home/xonotic/.xonotic" \
  xonotic-server:local
```

Apple Silicon smoke test flow:

```bash
docker build --platform linux/amd64 -t xonotic-server:local ./server
docker run --rm -it --platform linux/amd64 -p 26000:26000/udp xonotic-server:local
```

This confirms that the image can at least build and attempt to start as a `linux/amd64` container under emulation on macOS. It is not a substitute for validating the image on a real Linux `amd64` host or in the actual deployment environment.

The cloud validation path for that real environment lives in `platform/connectivity-checkpoint/README.md`.

## GHCR Publishing Convention

The repository now uses one simple GHCR package for the dedicated server image:

- package: `ghcr.io/nfnv/xonotic-server`
- stable checkpoint tag: `connectivity-checkpoint`
- immutable trace tag: `sha-<12-char-commit>`

The GitHub Actions workflow in `.github/workflows/publish-server-image.yml` publishes both tags using the repository `GITHUB_TOKEN`. The connectivity checkpoint manifest is pinned to the stable `connectivity-checkpoint` tag so the first GKE deployment path stays simple.

## Build And Runtime Notes

- the image uses a non-root user for the server process
- the official release checksum is verified during the build
- the build now fails early if the expected dedicated binary path is not present in the extracted release
- the runtime image is kept smaller than a one-stage build by separating download and extraction from runtime dependencies
- the container defaults are tuned for local bring-up, not internet-facing production operation
- the runtime image now includes `curl` so the phase-1 Agones path can send a local `Ready` request without full in-process SDK integration
- the Agones startup path now waits for the Xonotic UDP socket to bind before sending `Ready`, so `Allocated` servers are less likely to look healthy before they are actually joinable
- startup map selection is now env-driven in the entrypoint: exact map first, random pool second, legacy `XONOTIC_MAP` last

## Intentionally Deferred

- dedicated map rotation management
- custom packaged server configs
- health checks tailored to orchestration
- sidecar or metrics integration
- multi-arch image support

## What Still Needs Local Testing

- confirm the runtime dependency set is sufficient for the official dedicated server binary
- confirm the dedicated binary starts correctly in the selected `linux/amd64` base image
- confirm the generated config file is loaded in the intended order
- confirm the UDP port binding is reachable from a local Xonotic client
- confirm log output and shutdown behavior are clean
- confirm the startup command assumptions are still correct for the current Xonotic release, even though the binary name is consistent with the current official FAQ
