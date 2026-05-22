# Project Notes

This file is the running context log for the repository. Update it over time so a future session can recover project state quickly.

## Current State

- Stage: Tournament match server assignment persistence
- Status: Terraform has been applied successfully, the GKE Standard cluster exists when infra is up, `kubectl` access works when credentials/cluster are available, the Xonotic server image has been published to GHCR, the plain Kubernetes connectivity checkpoint and Agones phases have worked, the Fleet plus `GameServerAllocation` path exists, the allocator backend/frontend exist, the FleetAutoscaler standby buffer exists, in-memory Match Rooms manage manual Agones-backed server sessions, allocated rooms expose live Xonotic `getstatus` telemetry, rooms can be released by deleting allocated Agones `GameServer` resources, whitelisted RCON controls support broadcast plus allowlisted map change, PostgreSQL-backed tournament CRUD exists, and tournament matches can now persist server assignment history that links them to allocated Agones `GameServer` endpoints while the frontend makes match result, active server, and released-server states explicit
- Goal: bridge persisted tournament Matches to live Agones server assignments while preserving the proven lower-level Match Room allocation/RCON/release workflow

## Locked-In Context

- Game workload: Xonotic dedicated servers
- Cloud: Google Cloud Platform
- Kubernetes mode: GKE Standard
- Game server orchestration: Agones
- CI/CD: GitHub Actions
- GitHub to GCP auth: OIDC with Workload Identity Federation
- Container registry: GHCR
- Primary objective: demonstrate production-style platform engineering skills, not game modding
- Current proof strategy before Agones: one public registry image, one Kubernetes Deployment replica, one UDP `LoadBalancer` Service with `externalTrafficPolicy: Local`, and direct client connect by IP and port

## Current Constraints

- Allow only the minimum Kubernetes manifests required for the cloud connectivity checkpoint
- Keep this checkpoint separate from the later Agones design
- Keep the public repo lean; avoid separate architecture or ADR-heavy docs unless they become necessary again
- Prefer readable Terraform and Dockerfiles over abstraction or framework-heavy setup
- Keep IAM minimal until there is a concrete deployment or access requirement
- Keep the server image focused on the stock dedicated server path before adding orchestration-specific behavior
- Use a public GHCR image for this checkpoint so Kubernetes deployment stays free of image pull secret work
- Allow one narrow VPC firewall rule for UDP `26000` because the first Agones phase uses direct node access through `hostPort`, not a `LoadBalancer` Service
- Allow one additional narrow VPC firewall rule for UDP `7000-7010` because the Fleet phase uses dynamic Agones host ports instead of the single fixed-port model

## Documentation Structure

- `README.md`: public-facing project overview, scope, concise architecture, roadmap, and brief rationale for major choices
- `PROJECT_NOTES.md`: deeper internal context, planning notes, and evolving constraints
- `infra/README.md`: explains the Terraform MVP foundation and how to run it
- `platform/README.md`: explains the platform area and the limited pre-Agones checkpoint exception
- `platform/connectivity-checkpoint/README.md`: exact GHCR publish, deployment, and real-client connectivity test steps for the one-server GKE proof
- `platform/agones/README.md`: the single-GameServer reference plus the current Fleet-and-allocation phase, including networking details
- `platform/agones/manifests/xonotic-fleetautoscaler.yaml`: current buffer autoscaler that keeps a small standby pool of `Ready` Xonotic servers
- `platform/postgres/`: dev/local-cluster PostgreSQL manifests for the tournament persistence MVP
- `platform/allocator-backend/README.md`: deployment and test flow for the first in-cluster allocator backend
- `platform/allocator-frontend/README.md`: deployment and access flow for the small operator dashboard
- `docs/tournament-admin-design.md`: recommended MVP tournament-management domain model, API shape, frontend screens, persistence decision, and implementation phases
- `docs/postgres-persistence-design.md`: minimal PostgreSQL schema design for tournament, team, player, round, match, and match-room assignment persistence
- `allocator-backend/`: Python service code and container image build context for the in-cluster allocator backend
- `allocator-frontend/`: React admin dashboard build context and static frontend source
- `server/README.md`: explains the dedicated server container setup, runtime assumptions, and local test needs
- `scripts/up.sh` and `scripts/down.sh`: local operator scripts for low-cost bring-up and teardown of the Terraform-backed GKE cluster, now aligned with the current Agones phase including the allocator backend
- `scripts/env.sh.example`: template for project-local operator environment variables loaded by the local scripts
- `.github/workflows/publish-server-image.yml`: manual GHCR publish workflow for the server image
- `.github/workflows/publish-allocator-backend-image.yml`: manual and push-triggered GHCR publish workflow for the allocator backend image
- `.github/workflows/publish-allocator-frontend-image.yml`: manual and push-triggered GHCR publish workflow for the allocator frontend image
- `.gitignore`: practical defaults for local development noise, Terraform state, local env files, and generated artifacts

## Phase 1 Terraform Shape

- one zonal GKE Standard cluster
- one small node pool
- explicit node disk size and disk type
- node disk default increased to `100 GB` on `pd-standard` so a single-node dev cluster has enough allocatable ephemeral storage for the first Agones controller footprint
- default region and zone set for South America deployment (`southamerica-west1` / `southamerica-west1-a`)
- required GCP API enablement only
- no Artifact Registry resources because images will come from GHCR
- no GitHub OIDC or Workload Identity Federation resources yet
- no dedicated VPC yet; the MVP assumes the existing default network and subnetwork

## Initial Server Container Shape

- multi-stage Docker build using the official Xonotic release archive
- runtime image based on Debian slim with a non-root `xonotic` user
- baseline `server.cfg` stored in the repo
- runtime-generated `server.autoexec.cfg` for environment-driven overrides
- startup map selection now handled in the entrypoint through `XONOTIC_START_MAP` or optional random selection from `XONOTIC_MAP_POOL`
- intended v1 image/runtime target is `linux/amd64`
- Apple Silicon local runs are smoke tests only
- no full Agones SDK integration yet; only a minimal phase-1 `Ready` hook is added so one `GameServer` can reach `Ready`

## Internal Planning Notes

- Keep architecture and decision detail summarized, not academic
- Public documentation should stay readable in one pass from the root README
- The strongest portfolio story is the end-to-end platform flow: GitHub -> OIDC/WIF -> GCP -> GKE Standard -> Agones -> Xonotic servers
- Initial implementation should continue to favor one cluster and one environment until there is a working baseline worth promoting
- The plain Kubernetes checkpoint is now validated, so future work can treat the image, UDP port, and basic GKE exposure path as a known-good baseline
- The checkpoint used the least ambiguous networking path rather than the eventual long-term production exposure model
- The first Agones phase should stay limited to controller installation plus one `GameServer`; that phase is now reference-only
- The Fleet-and-allocation phase is now the reference baseline, while the current Agones phase adds a FleetAutoscaler buffer on top of it
- The first backend phase should run inside the cluster and use the Kubernetes API directly rather than introducing the external Agones Allocator Service; that backend now exists and should remain compatible with the autoscaled Fleet
- The first frontend phase should stay operator-focused and use the in-cluster allocator backend as its only API surface
- The admin frontend should treat only `Allocated` `GameServer` instances as user-facing join targets; `Ready` servers are standby capacity
- Match Rooms are now the admin-facing objects; allocated Agones `GameServer` instances are infrastructure backing those rooms
- For the tournament-management phase, `Match` should become the tournament-facing record while Match Room should remain the lower-level server/session object that owns allocation, endpoint, RCON controls, live status, and release
- Match Room state is intentionally in-memory and disappears on backend Pod restart until a later persistence phase
- The next real tournament-management phase should add PostgreSQL for tournaments, teams, players, rounds, matches, and results rather than keeping those admin-authored records in-memory
- The first PostgreSQL phase now persists tournament, team, player, round, tournament match records, and tournament match server assignment history; Match Rooms and live server telemetry remain runtime/in-memory for now
- The frontend now has a Tournament Management section for persisted tournaments, teams, rounds, tournament match records, server assignment controls, manual result recording, and active-assignment admin controls; it intentionally does not render brackets or advance winners yet
- PostgreSQL should persist durable admin intent and assignment snapshots, while Kubernetes/Agones remain the source of truth for live Fleet, GameServer, Pod, allocation-resource, and endpoint runtime state
- Live player/map/score data should use read-only Xonotic `getstatus` first; avoid RCON unless a later feature truly requires command execution
- Match Rooms now store requested map and game mode before allocation; allocation still uses a warm Fleet server, then applies requested config with whitelisted RCON and only exposes the endpoint when `getstatus` verifies it
- `docs/rcon-admin-controls.md` captures the RCON investigation, smoke-test endpoint, and first whitelisted admin-control phase; `./scripts/up.sh` recreates namespace-scoped `xonotic-rcon` Secrets from local `XONOTIC_RCON_PASSWORD`
- The RCON client should use DarkPlaces secure HMAC-MD4 challenge RCON first because plaintext RCON is ignored when `rcon_secure > 0` and secure TIME RCON is ignored when `rcon_secure > 1`; challenge replies may be NUL-terminated and must be stripped before signing `"<challenge> <command>"`
- RCON admin controls must remain whitelisted backend actions only; the current frontend exposes broadcast message and allowlisted map change for allocated Match Rooms and active tournament match assignments, but no raw command input and no RCON password
- Additional RCON controls should follow the command matrix in `docs/rcon-admin-controls.md`: prioritize `restart`, then verify `endmatch`; defer kick until player IDs are parsed reliably; defer standalone game-mode and max-player controls until behavior is verified in this exact server setup
- Match Room `live_status` should represent the last known good `getstatus` result; transient map-reload timeouts are tracked separately as status/verification errors so the admin UI does not lose map/mode/player context
- Manual Direct Allocation is now an Advanced / Debug path; normal operator workflow is Match Rooms, and allocated-server table actions can terminate only `Allocated` GameServers or route safe commands through a linked Match Room
- Releasing a Match Room deletes the allocated Agones `GameServer` resource and relies on Fleet/FleetAutoscaler to replenish standby capacity
- Persisted tournament Match server assignment uses `match_server_assignments`: one active assignment per match, historical released rows preserved, and Agones/Kubernetes remain the source of truth for whether the runtime `GameServer` currently exists
- Tournament Match server allocation must apply requested map/mode through the existing whitelisted RCON configuration helper and verify with `getstatus`; matches only become `server_ready` after verification, while failed verification preserves the active assignment for operator cleanup
- Requested map/mode selection is now backed by a conservative compatibility matrix exposed through `GET /game-config/options`; normal allocation only allows verified combinations (`dm/xoylent`, `dm/stormkeep`, `dm/solarium`, `tdm/stormkeep`) and keeps `ctf`/`duel`/`ca`/`dom`/`kh` deferred as experimental candidates until a controlled tournament allocation probe verifies each map/mode pair with `getstatus`
- Tournament Match result recording is manual and PostgreSQL-backed: operators record scores, winner, and optional notes, which marks the match `finished`; bracket advancement and server release remain separate
- Tournament Match lifecycle UI treats result recording and server release as separate states: finished matches show final score and winner, active assignments expose endpoint/connect plus the same whitelisted broadcast/map-change controls used by Match Rooms, finished matches with active assignments prompt release, and released matches hide/de-emphasize endpoint details
- The local `up.sh` operator path should track the current Agones phase rather than automatically redeploying the old plain checkpoint
- The local operator path should treat the allocator backend as part of the current baseline, not an optional manual follow-up
- Reliability for this phase means every allocated server endpoint must be joinable, not just that some allocations succeed
- The current Agones reliability risk is that a `GameServer` can look `Ready` before the Xonotic UDP socket is actually bound; the startup path should make `Ready` closer to real joinability
- Distinguish clearly between infrastructure that is implemented in Terraform and infrastructure that has actually been applied in a real GCP project
- Observability should be added later with a practical minimum: logs, metrics, alerts, and short runbooks
- If the default VPC assumption becomes a blocker, add dedicated networking in a later infra iteration rather than now
- The current platform milestone is validating one Agones-managed Xonotic server on top of the already-proven connectivity baseline

## Expected Next Steps

- republish the Xonotic server image so the GHCR tag includes the phase-1 Agones `Ready` hook
- install Agones on the existing GKE cluster
- deploy the `Fleet` and validate two `Ready` Xonotic `GameServer` instances
- apply the FleetAutoscaler and validate that the standby pool stays at `3` `Ready` servers during allocation
- make the lifecycle scripts bring the allocator backend up and down automatically with the rest of the current phase
- test manual and backend-driven allocation against the autoscaled Fleet
- verify that all allocated Fleet endpoints are reachable and recycle any stale `GameServer` instances that were created under older Agones port-range settings
- republish the Xonotic server image and refresh the Fleet so the tighter Agones readiness contract is in use
- publish the allocator frontend image and deploy the in-cluster admin dashboard
- validate the admin dashboard against the existing allocator backend read and allocate endpoints
- add remote state once the project moves beyond local-only iteration
- add minimal cluster access and deployment identity groundwork when GitHub delivery is introduced
- document observability and operations plan in more depth
