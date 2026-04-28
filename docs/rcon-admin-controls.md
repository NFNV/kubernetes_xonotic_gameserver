# RCON Admin Controls Investigation

This is an investigation note only. Do not implement RCON-backed admin controls until the command behavior is verified against a running allocated `GameServer`.

## Current Answer

Xonotic RCON looks suitable for a small set of backend-owned admin actions, but it should not be exposed as a generic command runner.

The recommended next implementation shape is:

1. enable RCON on Fleet servers with a strong password from a Kubernetes `Secret`
2. keep the password only in the Xonotic server container and allocator backend Pod environment
3. have the backend allocate a `GameServer`
4. have the backend apply a whitelisted match configuration over RCON before returning the join endpoint
5. verify the result with the existing read-only `getstatus` query
6. return the endpoint only if verification succeeds or return a clear partial-allocation error

## Source Notes

Xonotic's FAQ says RCON is QuakeWorld-compatible, configured with `rcon_password`, and targeted with `rcon_address <ip/hostname>` or `rcon_address <ip/hostname>:<port>`.

The Xonotic basic server configuration docs list the server `port`, `maxplayers`, `gametype`, and `rcon_password` as normal server config settings.

The Xonotic game server configuration docs describe RCON as the way to configure an already-online server without restarting it.

References:

- <https://xonotic.org/faq/>
- <https://github-wiki-see.page/m/xonotic/xonotic/wiki/Basic-server-configuration>
- <https://xonotic.fandom.com/wiki/Game_Server_Configuration>

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

The Fleet does not currently set `XONOTIC_RCON_PASSWORD`, which is correct until the feature is implemented.

Recommended enablement later:

1. create a Kubernetes `Secret` in `xonotic-agones`, for example `xonotic-rcon`
2. add `XONOTIC_RCON_PASSWORD` to the Fleet server container from that Secret
3. add the same Secret value to the allocator backend Pod as `XONOTIC_RCON_PASSWORD`
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

Verify these against a disposable allocated server before building UI controls:

| Candidate command | Purpose | Safety recommendation |
| --- | --- | --- |
| `status` | Confirm RCON auth and inspect players/server state | Safe read command. Useful for RCON smoke tests, though `getstatus` remains better for structured telemetry. |
| `gametype <mode>` | Set game mode | Whitelist modes such as `dm`, `tdm`, `duel`, `ctf`. Verify whether it takes effect immediately or only after map change. |
| `changelevel <map>` | Change map | Whitelist known map names. Apply after `gametype` if mode should affect the next loaded map. |
| `maxplayers <n>` | Set player cap | Verify accepted range and whether reducing below connected players is rejected or disruptive. Keep backend validation at `1-32` for now. |
| `restart` | Restart current map | Useful as a controlled reset action. Could disrupt connected players, so keep it behind explicit confirmation. |
| `endmatch` | End current match | Verify behavior. It may advance to normal map/vote flow rather than release infrastructure. Prefer the existing Agones release flow for "End Match" in the dashboard. |
| `say <message>` | Broadcast admin message | Allow only sanitized text, length-limited, no newlines, no command chaining. |

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
- do not put the password in manifests, `.env` examples, docs, or frontend build config

## Recommendation

Keep the current release/end-match flow as-is. It uses Kubernetes/Agones lifecycle directly and is safer than trying to end infrastructure through RCON.

Use RCON only for structured live server controls:

- allocation-time map selection
- allocation-time game mode selection
- allocation-time player cap
- optional restart current map
- optional broadcast message

The first RCON implementation should be backend-only and allocation-time only: allocate a warm server, apply whitelisted config, verify with `getstatus`, then expose the endpoint.
