# RCON Admin Controls Investigation

This document tracks the RCON investigation and the first whitelisted admin-control phase. Do not add raw RCON command access.

## Current Answer

Xonotic RCON looks suitable for a small set of backend-owned admin actions, but it should not be exposed as a generic command runner.

The recommended full implementation shape is:

1. enable RCON on Fleet servers with a strong password from a Kubernetes `Secret`
2. keep the password only in the Xonotic server container and allocator backend Pod environment
3. have the backend allocate a `GameServer`
4. have the backend apply a whitelisted match configuration over RCON before returning the join endpoint
5. verify the result with the existing read-only `getstatus` query
6. return the endpoint only if verification succeeds or return a clear partial-allocation error

The current implemented phase is smaller than that full shape:

- `./scripts/up.sh` recreates Kubernetes Secrets from local `XONOTIC_RCON_PASSWORD`
- the Xonotic Fleet receives `XONOTIC_RCON_PASSWORD`
- the allocator backend receives `XONOTIC_RCON_PASSWORD`
- the backend exposes `POST /matches/<match_id>/rcon-smoke-test`
- the backend exposes `POST /matches/<match_id>/admin/broadcast`
- the backend exposes `POST /matches/<match_id>/admin/change-map`
- the endpoint sends only a hardcoded `status` command by default
- the endpoint can optionally send a hardcoded `say "RCON smoke test"` after `status`
- the frontend exposes only structured broadcast and map-change controls for allocated Match Rooms
- no arbitrary RCON command endpoint exists
- no frontend raw command input exists

## Source Notes

Xonotic's FAQ says RCON is QuakeWorld-compatible, configured with `rcon_password`, and targeted with `rcon_address <ip/hostname>` or `rcon_address <ip/hostname>:<port>`.

The Xonotic basic server configuration docs list the server `port`, `maxplayers`, `gametype`, and `rcon_password` as normal server config settings.

The Xonotic game server configuration docs describe RCON as the way to configure an already-online server without restarting it.

References:

- <https://xonotic.org/faq/>
- <https://github-wiki-see.page/m/xonotic/xonotic/wiki/Basic-server-configuration>
- <https://xonotic.fandom.com/wiki/Game_Server_Configuration>
- <https://github-wiki-see.page/m/xonotic/xonotic/wiki/Commands>
- <https://xonotic.org/doxygen/qcsrc/sv__cmd_8qc.html>

Secondary, non-authoritative command syntax cross-check:

- <https://legionhosting.net/kb/xonotic/xonotic-admin-commands>

## 1. Does Xonotic RCON Use The Same UDP Game Server IP:Port?

Yes, that is the working assumption and it matches the public Xonotic docs.

There is no separate TCP management listener in this project. Xonotic documents RCON by setting `rcon_address` to the server address, optionally including the port. The same docs describe the normal game server port as UDP `26000` by default.

For this repo's Agones Fleet:

- Xonotic listens on container UDP `26000`
- Agones assigns a dynamic external host port from `7000-7010`
- allocation returns the node address and assigned external UDP port
- clients connect to that returned `address:port`
- backend `getstatus` already queries that returned `address:port`

RCON should target the same allocated `address:port`.

## 2. How Do We Safely Enable RCON In The Container/Fleet?

The container already supports an optional `XONOTIC_RCON_PASSWORD` environment variable. `server/entrypoint.sh` writes `rcon_password "<value>"` into the generated `server.autoexec.cfg` only when the variable is non-empty.

The Fleet now receives `XONOTIC_RCON_PASSWORD` from a Kubernetes Secret named `xonotic-rcon`.

For local dev, set:

```bash
export XONOTIC_RCON_PASSWORD="change-me-local-dev-password"
```

The preferred local workflow is to copy `scripts/env.sh.example` to `scripts/env.sh` and edit the value there. `scripts/env.sh` is ignored by Git.

`./scripts/up.sh` creates the Secret every time it brings the cluster up. Because Kubernetes Secrets are namespace-scoped, the script creates the same-named Secret in both namespaces:

- `xonotic-agones/xonotic-rcon` for Fleet server Pods
- `xonotic-allocator-backend/xonotic-rcon` for the allocator backend Pod

That means a destroyed/recreated cluster does not need manual Secret repair as long as `XONOTIC_RCON_PASSWORD` is present in the local environment or `scripts/env.sh`.

Recommended production-grade enablement later:

1. create namespace-scoped Kubernetes Secrets named `xonotic-rcon`
2. add `XONOTIC_RCON_PASSWORD` to the Fleet server container from the `xonotic-agones` Secret
3. add the same Secret value to the allocator backend Pod from the `xonotic-allocator-backend` Secret
4. do not expose the password through frontend config, API responses, logs, or errors
5. rotate the Secret if it is ever copied into a local shell, logs, screenshots, or client console

Do not use `XONOTIC_EXTRA_CFG` for the RCON password except during a throwaway local experiment. A typed Secret reference is clearer and safer.

## 3. Can The Allocator Backend Pod Reach Allocated GameServers Via RCON?

Most likely yes.

The allocator backend already reaches allocated servers with UDP `getstatus` using the allocated server `address` and `port`. If RCON uses the same UDP server endpoint, the backend should be able to use the same network path.

Still verify this explicitly before implementation:

1. allocate a Match Room
2. capture the returned `address:port`
3. from the allocator backend Pod, send an RCON `status` command to that endpoint
4. compare the response to the existing `getstatus` response

If the backend cannot reach the endpoint through the allocated external address from inside the cluster, the fallback investigation path is to use Agones `status.nodeName` plus the node internal IP and assigned host port. Do not assume this is needed until the direct allocated endpoint fails; the existing `getstatus` path suggests direct access should work.

## 4. Commands To Verify For Safe Admin Controls

Verify these against a disposable allocated server before building any new UI controls.

| Candidate | Command syntax | Status | Required validation | Expected result | Verification path | Frontend recommendation |
| --- | --- | --- | --- | --- | --- | --- |
| Status | `status` | Already verified | No user input | RCON returns server/player summary | RCON response must be non-empty; compare player lines with `getstatus` where possible | Keep backend/debug only. Use `getstatus` for normal structured telemetry. |
| Broadcast | `say <message>` | Already verified | Required string, trim whitespace, max 160 chars, reject newlines/control chars/command separators | Message appears in server chat | RCON send succeeds; optionally inspect server logs or client chat | Already exposed. Keep as structured action only. |
| Map change | `changelevel <map>` | Already verified for the current small allowlist | Map must be in the backend allowlist; normal allocation still accepts only verified map/mode pairs | Server changes to requested map | Retry `getstatus` for 10-15s; expected `map` must match | Already exposed as post-allocation override and used during allocation-time config. |
| Restart current map | `restart` | Safe to test | No free-form input; require explicit confirmation | Current level restarts, likely disconnecting/interrupting active play briefly | `getstatus` should recover on same map; server logs should show restart/reload; player count may reset or reconnect | Good next candidate. Add only with confirmation and warning. |
| End current match | `endmatch` | Uncertain | No free-form input; require explicit confirmation | Current match ends and server advances according to normal Xonotic match/map flow | Observe RCON response/logs; `getstatus` may show intermission, next map, or same map depending config | Test after `restart`. Do not confuse with infrastructure release, which should remain Agones delete. |
| Kick player | `kick # <player_id> <reason>` | Risky | Player ID must come from parsed `status`/`getstatus`, not free text; reason length-limited and sanitized | Target player is removed from server | `getstatus`/`status` no longer includes player; logs show kick | Defer until player IDs are parsed reliably and UI clearly targets one connected player. |
| Game mode change | `gametype <mode>` | Already used, still setup-sensitive | Mode must be in the compatibility matrix; verified normal modes are `dm` and `tdm`, while `ctf`, `duel`, `ca`, `dom`, and `kh` stay experimental until proven | Requested game type becomes active, often after map change | `getstatus` `game_mode` must match after `changelevel`/reload | Keep allocation-time only for now; expose standalone mode switch later only after repeated verification. |
| Max players | `maxplayers <n>` | Uncertain | Integer only; proposed range `1-32`; reject values below current player count unless explicitly tested | Server player cap changes | `getstatus` `max_players` should match; logs/RCON output should not show rejection | Defer. It may be disruptive if lowered below connected players and needs exact behavior verification. |

Notes:

- Xonotic FAQ documents `kick # <player id> <reason>` and says the ID is visible in `status`.
- Xonotic command docs list `restart`, `say`, `kick`, and `maxplayers`.
- Xonotic server command Doxygen lists `gametype`, `gotomap`, and `resetmatch`; this makes `gametype` a stronger candidate than arbitrary mode commands.
- `changelevel` is already verified in this project even though Xonotic server-command docs also mention `gotomap`. Keep `changelevel` unless a future test shows `gotomap <map>` is safer.
- `endmatch` appears in admin-command references and restricted-command examples, but should be treated as uncertain until tested on this exact server image/config.

Commands that should not be exposed in the admin UI:

- arbitrary raw RCON
- `quit`
- `exec <file>`
- `fs_rescan`
- `kick`, `ban`, or `unban` until there is a clearer moderation model
- any command containing newline/control characters

## 5. Can The Backend Apply Map/Mode/Max Players After Allocation, Then Verify With getstatus?

Probably yes, with an important sequencing rule:

The backend should apply RCON configuration immediately after allocation and before returning the endpoint as joinable.

Recommended sequence:

1. create Match Room with requested `map`, `game_mode`, and `max_players`
2. create `GameServerAllocation`
3. receive allocated `address`, `port`, and `GameServer` name
4. send whitelisted RCON commands to the allocated `address:port`
5. poll `getstatus` until the live server reports the expected map/mode/player cap or a timeout occurs
6. attach the server to the Match Room only after verification succeeds
7. return the endpoint to the frontend

Expected command order for the first test:

```text
status
gametype dm
maxplayers 8
changelevel xoylent
```

Then verify with `getstatus`:

- `map` should equal `xoylent`
- `game_mode` should equal `dm` or the equivalent Xonotic-reported mode string
- `max_players` should equal `8`

If verification fails, the backend should keep the Match Room out of the "ready to join" state and either release/delete the allocated `GameServer` or mark the room as `configuration_failed` with a clear error.

Max-player verification is not implemented yet. Until it is verified in this exact server setup, do not expose it in the dashboard.

## 6. Safe Backend/Frontend Design

Use explicit action endpoints, not a generic RCON proxy.

Good backend API shape:

- `POST /matches`
- `POST /matches/<match_id>/allocate`
- `POST /matches/<match_id>/configure`
- `POST /matches/<match_id>/restart`
- `POST /matches/<match_id>/say`
- `POST /matches/<match_id>/release`

Better first implementation:

- fold configuration into `POST /matches/<match_id>/allocate`
- do not expose a separate configure endpoint until the allocation-time path is reliable

Backend rules:

- whitelist maps
- whitelist modes
- validate `max_players` as a bounded integer
- validate message length and reject newlines/control characters
- never accept raw command strings from the frontend
- never log the RCON password
- include target `GameServer` name, address, port, action name, and sanitized command category in logs
- treat RCON failures as backend errors, not frontend command errors
- verify state with `getstatus` after mutating actions

Frontend rules:

- show structured controls only after backend support exists
- keep confirmations for disruptive actions like restart/release
- never store or send the RCON password
- show requested config separately from live verified status
- do not show a join endpoint until allocation and configuration verification have completed

Secret handling:

- store the password in a Kubernetes `Secret`
- mount it into the Xonotic Fleet as `XONOTIC_RCON_PASSWORD`
- mount it into the allocator backend as `XONOTIC_RCON_PASSWORD`
- do not put the real password in manifests, docs, or frontend build config

## Recommendation

Keep the current release/end-match flow as-is. It uses Kubernetes/Agones lifecycle directly and is safer than trying to end infrastructure through RCON.

Use RCON only for structured live server controls:

- allocation-time map selection
- allocation-time game mode selection
- allocation-time player cap
- optional restart current map
- optional broadcast message

Recommended implementation order:

1. Keep the current implemented controls: status smoke test, broadcast, allocation-time map/mode, post-allocation map override.
2. Add `restart` next as a confirmed/disruptive admin action with explicit UI confirmation and `getstatus` recovery verification.
3. Test `endmatch` after `restart`, but do not present it as server release. The dashboard's End Match/Release action should continue deleting the allocated Agones `GameServer`.
4. Defer `kick` until backend player parsing produces stable player IDs and the UI can target a single connected player safely.
5. Defer standalone game-mode changes until repeated tests confirm `gametype <mode>` plus reload behavior for all allowlisted modes.
6. Defer `maxplayers <n>` until the command is verified in this exact image/config, especially behavior when reducing below the current player count.

## Implemented Smoke-Test Endpoint

Endpoint:

```text
POST /matches/<match_id>/rcon-smoke-test
```

Behavior:

- requires the Match Room to exist
- requires the Match Room to have an allocated server
- requires `XONOTIC_RCON_PASSWORD` to be configured in the backend Pod
- sends only `status` by default
- tries DarkPlaces secure challenge RCON first
- falls back to secure TIME RCON, then plaintext RCON
- optionally sends only `say "RCON smoke test"` when the request body includes `{"include_say": true}`
- returns sanitized/truncated output
- never returns or logs the RCON password

Protocol detail:

- plaintext packet: `0xffffffff + "rcon <password> <command>"`
- secure TIME packet: `0xffffffff + "srcon HMAC-MD4 TIME " + hmac + " " + "<timestamp.random> <command>"`
- secure challenge packet: request `getchallenge`, strip packet prefix/NUL terminators from the `challenge` reply, then send `0xffffffff + "srcon HMAC-MD4 CHALLENGE " + hmac + " " + "<challenge> <command>"`

DarkPlaces ignores plaintext RCON when `rcon_secure > 0`, and ignores secure TIME RCON when `rcon_secure > 1`, so the backend must not rely on the simple Quake-style packet or the TIME packet alone. Challenge RCON is the safest default for this smoke-test phase.

## Implemented Admin Actions

The first frontend-visible RCON controls are deliberately narrow and whitelisted.

The normal Match Room allocation path now also uses RCON in a controlled way: the admin chooses requested map and mode before allocation, the backend allocates a warm Fleet server, sends whitelisted `gametype <mode>` and `changelevel <map>` commands, verifies live status with `getstatus`, and only exposes the endpoint when verification succeeds.

Implemented endpoints:

```text
POST /matches/<match_id>/admin/broadcast
POST /matches/<match_id>/admin/change-map
```

Safety model:

- both endpoints require an existing allocated Match Room
- neither endpoint accepts raw RCON commands
- allocation-time map/mode configuration also uses whitelisted backend-owned RCON commands only
- the RCON password stays in Kubernetes Secret-backed backend/server environment variables
- the frontend never sees the RCON password
- backend logs include action/target/protocol details, not the password
- change-map is limited to an allowlist: `xoylent`, `stormkeep`, `implosion`, `drain`, `darkzone`, `solarium`
- broadcast messages are required, trimmed, limited to 160 characters, and reject control characters, newlines, and command separators
- allocated-server table command actions route through the linked Match Room when available
- direct/manual allocated servers do not expose RCON actions until they have a safe Match Room context

Broadcast request:

```bash
curl -fsS -X POST "http://127.0.0.1:18080/matches/${MATCH_ID}/admin/broadcast" \
  -H "content-type: application/json" \
  -d '{"message":"Match starts in 2 minutes"}'
```

The backend sends a whitelisted `say "<message>"` RCON command.

Change-map request:

```bash
curl -fsS -X POST "http://127.0.0.1:18080/matches/${MATCH_ID}/admin/change-map" \
  -H "content-type: application/json" \
  -d '{"map":"stormkeep"}'
```

The backend sends `changelevel <map>`, clears cached live status, waits briefly, then retries `getstatus` for a short verification window.

Verification behavior:

- waits 1 second after the RCON command
- retries `getstatus` every 1 second for about 12 seconds
- if `getstatus` succeeds and reports the requested map, returns `verified: true`
- if `getstatus` succeeds but reports another map until the timeout, returns `verified: false` with verification details
- if `getstatus` times out during map reload, returns partial success with `rcon_sent: true`, `verified: false`, and `error: "change_map_verification_failed"`
- transient `getstatus` failures are stored separately as `last_status_error` / `last_status_error_at`
- the previous successful `live_status` is preserved instead of being overwritten by a transient timeout

Known limitations:

- command behavior depends on Xonotic accepting the chosen RCON command syntax
- `getstatus` is best-effort and may briefly report the old map or a transient failure during a map change; the UI shows a warning and keeps the last known good status in that case
- game mode is applied with `gametype <mode>` and verified through `getstatus`; if verification fails, the room is marked `allocated_needs_attention`
- max players, restart, kick/ban, and arbitrary commands remain intentionally deferred
- mutating RCON/admin endpoints require the basic Admin View session; this is not OAuth or production role management

Example success shape:

```json
{
  "ok": true,
  "target": {
    "address": "34.176.10.20",
    "port": 7003,
    "allocated_game_server_name": "xonotic-fleet-abcde-fghij"
  },
  "commands": [
    {
      "ok": true,
      "command": "status",
      "response_expected": true,
      "bytes": 1200,
      "output": "hostname: Xonotic Agones Fleet\n..."
    }
  ],
  "live_status": {
    "ok": true,
    "source": "getstatus"
  }
}
```

Expected failure cases:

- `404 match_not_found`: the Match Room ID is wrong or backend memory was reset
- `409 match_not_allocated`: the room exists but has no allocated server yet
- `409 match_finished`: the room was already released
- `503 rcon_not_configured`: backend Pod does not have `XONOTIC_RCON_PASSWORD`
- `504 rcon_timeout`: RCON did not respond at the allocated endpoint
- `502 rcon_network_error`: UDP send/receive failed

## Smoke-Test Steps

Bring the cluster up with RCON enabled:

```bash
cp scripts/env.sh.example scripts/env.sh
```

Edit `scripts/env.sh`, set the GCP values and replace `XONOTIC_RCON_PASSWORD`.

```bash
./scripts/up.sh
```

Port-forward the backend:

```bash
kubectl port-forward -n xonotic-allocator-backend service/xonotic-allocator-backend 18080:8080
```

Create an admin session cookie:

```bash
ADMIN_COOKIE="$(mktemp)"
curl -fsS -c "${ADMIN_COOKIE}" -X POST http://127.0.0.1:18080/admin/login \
  -H "content-type: application/json" \
  -d '{"username":"admin","password":"<admin-password>"}'
```

Create a Match Room:

```bash
MATCH_ID="$(curl -fsS -b "${ADMIN_COOKIE}" -X POST http://127.0.0.1:18080/matches \
  -H "content-type: application/json" \
  -d '{"name":"RCON smoke test"}' | jq -r .match_id)"
```

Allocate a server:

```bash
curl -fsS -b "${ADMIN_COOKIE}" -X POST "http://127.0.0.1:18080/matches/${MATCH_ID}/allocate"
```

Run the RCON status smoke test:

```bash
curl -fsS -b "${ADMIN_COOKIE}" -X POST "http://127.0.0.1:18080/matches/${MATCH_ID}/rcon-smoke-test"
```

Optionally also send the hardcoded smoke message:

```bash
curl -fsS -b "${ADMIN_COOKIE}" -X POST "http://127.0.0.1:18080/matches/${MATCH_ID}/rcon-smoke-test" \
  -H "content-type: application/json" \
  -d '{"include_say":true}'
```

Release the server when done:

```bash
curl -fsS -b "${ADMIN_COOKIE}" -X POST "http://127.0.0.1:18080/matches/${MATCH_ID}/release"
```

## Security Limitations In This Phase

This is still a dev-cluster smoke test:

- the RCON password is stored in Kubernetes Secrets, not an external secret manager
- the Admin View uses basic password-protected sessions, not OAuth or production role management
- anyone with valid admin credentials and access to the backend HTTP endpoint can call the smoke-test endpoint
- the smoke-test endpoint does not expose arbitrary RCON, but it still proves privileged server control
- local `scripts/env.sh` must stay uncommitted

Before any non-local/admin-only use, replace basic auth with the platform's chosen identity and secret-management model.
