# Tournament Admin Design

This document defines the recommended MVP path from the current Match Room dashboard toward a small tournament/admin tool.

The goal is not to build a full esports platform yet. The goal is to introduce enough tournament structure that an operator can create teams, schedule matches, allocate verified Xonotic servers, record results, and release infrastructure cleanly.

## Current Baseline

The platform already has:

- in-memory Match Rooms
- Agones Fleet allocation
- FleetAutoscaler standby capacity
- release/end-match by deleting allocated `GameServer` resources
- live `getstatus` telemetry
- whitelisted RCON admin controls
- a React operator dashboard

The next phase should preserve that working platform path and add a thin tournament layer above it.

## Recommended Domain Model

### Tournament

Represents one event or bracket container.

Fields:

- `tournament_id`
- `name`
- `status`: `draft`, `active`, `finished`, `cancelled`
- `created_at`
- `started_at`
- `finished_at`
- `description`
- `format`: initially `manual`

MVP behavior:

- create/list/view tournaments
- mark active/finished manually
- no automatic bracket generation yet

### Team

Represents one competing team within a tournament.

Fields:

- `team_id`
- `tournament_id`
- `name`
- `tag`
- `seed`
- `created_at`
- `player_ids`

MVP behavior:

- create/list/edit teams manually
- seed is optional and manual
- no team invite flow

### Player

Represents one player attached to a team.

Fields:

- `player_id`
- `team_id`
- `display_name`
- `handle`
- `created_at`

MVP behavior:

- manually add/remove players under teams
- no accounts, login, or identity verification
- no automatic linkage to live Xonotic player names yet

### Round

Represents a group of matches inside a tournament.

Fields:

- `round_id`
- `tournament_id`
- `name`
- `order`
- `status`: `created`, `scheduled`, `running`, `finished`
- `created_at`

MVP behavior:

- create rounds manually
- list matches by round
- no automatic winner advancement yet

### Match

Represents the tournament/admin match record.

Fields:

- `match_id`
- `tournament_id`
- `round_id`
- `team_a_id`
- `team_b_id`
- `status`
- `scheduled_at`
- `created_at`
- `started_at`
- `finished_at`
- `winner_team_id`
- `result_notes`
- `requested_map`
- `requested_game_mode`
- `match_room_id`

MVP behavior:

- create scheduled matches manually
- allocate/release one server through an attached Match Room
- record winner manually
- keep live server telemetry visible while running

### Match Room / Allocated Server

Represents the lower-level runtime session and infrastructure binding.

Fields:

- `match_room_id`
- `match_id`
- `status`
- `requested_map`
- `requested_game_mode`
- `allocated_server`
- `joinable`
- `live_status`
- `allocation_config_result`
- `created_at`
- `allocated_at`
- `released_at`

MVP behavior:

- allocate warm Agones server
- apply requested map/mode through whitelisted RCON
- verify with `getstatus`
- expose endpoint only when verified
- release by deleting allocated Agones `GameServer`

## Match vs Match Room

Recommendation: do not rename Match Room into Match.

Use `Match` as the tournament-facing object and keep `Match Room` as a lower-level server/session object.

Reasoning:

- A tournament Match exists before infrastructure is allocated.
- A Match can be scheduled, cancelled, finished, or recorded without a live server.
- A Match Room is specifically about runtime server state: allocation, endpoint, RCON controls, live status, and release.
- Keeping them separate makes it easier later to support rematches, server replacement, warmup rooms, or multiple maps per match.

The frontend can still present this simply: a Match card shows tournament metadata at the top and the attached Match Room/server controls inside the card.

## Match Lifecycle States

Use these states for tournament `Match` records:

- `created`: match exists but is not scheduled.
- `scheduled`: teams/time/config are set, but no server is being allocated.
- `server_allocating`: allocation request is in progress.
- `server_ready`: server is allocated, configured, verified, and joinable.
- `running`: operator has marked the match as live, or live status indicates active play once that signal is reliable.
- `finished`: result/winner has been recorded.
- `released`: backing server has been released. This can happen after `finished`, or as cleanup after failure/cancellation.
- `failed`: allocation, configuration, or release failed and needs operator attention.

Important rule:

- `server_ready` means the endpoint can be shown as a join target.
- `server_allocating` and `failed` must not expose the endpoint as ready for players.

## Backend API Shape

Use explicit resource endpoints. Keep raw RCON and arbitrary command execution out of the API.

### Tournaments

```text
POST /tournaments
GET /tournaments
GET /tournaments/<tournament_id>
PATCH /tournaments/<tournament_id>
POST /tournaments/<tournament_id>/start
POST /tournaments/<tournament_id>/finish
```

### Teams

```text
POST /tournaments/<tournament_id>/teams
GET /tournaments/<tournament_id>/teams
GET /tournaments/<tournament_id>/teams/<team_id>
PATCH /tournaments/<tournament_id>/teams/<team_id>
DELETE /tournaments/<tournament_id>/teams/<team_id>
```

### Players

```text
POST /tournaments/<tournament_id>/teams/<team_id>/players
GET /tournaments/<tournament_id>/teams/<team_id>/players
PATCH /tournaments/<tournament_id>/teams/<team_id>/players/<player_id>
DELETE /tournaments/<tournament_id>/teams/<team_id>/players/<player_id>
```

### Rounds

```text
POST /tournaments/<tournament_id>/rounds
GET /tournaments/<tournament_id>/rounds
GET /tournaments/<tournament_id>/rounds/<round_id>
PATCH /tournaments/<tournament_id>/rounds/<round_id>
```

### Matches

```text
POST /tournaments/<tournament_id>/matches
GET /tournaments/<tournament_id>/matches
GET /tournaments/<tournament_id>/matches/<match_id>
PATCH /tournaments/<tournament_id>/matches/<match_id>
POST /tournaments/<tournament_id>/matches/<match_id>/start
POST /tournaments/<tournament_id>/matches/<match_id>/finish
```

### Server Allocation

```text
POST /tournaments/<tournament_id>/matches/<match_id>/allocate-server
POST /tournaments/<tournament_id>/matches/<match_id>/release-server
GET /tournaments/<tournament_id>/matches/<match_id>/server-status
```

Current implementation stores a PostgreSQL-backed `match_server_assignments` row for each tournament Match server assignment.

Allocation reuses the existing Agones allocation primitives:

1. verify the tournament Match exists
2. allocate a warm Agones `GameServer`
3. store `allocated_game_server_name`, allocation request name, address, port, and `active` status in PostgreSQL
4. apply requested map/mode through the existing whitelisted RCON configuration helper
5. verify with `getstatus`
6. mark Match `server_ready` only when verification succeeds

If configuration or verification fails, the assignment remains persisted for operator cleanup, but the Match is marked `failed` rather than `server_ready`.

Release deletes the allocated Agones `GameServer`, marks the assignment `released`, stores `released_at`, and preserves assignment history.

The lower-level in-memory Match Room workflow remains available for manual/operator server sessions and RCON controls.

### Result Recording

```text
POST /tournaments/<tournament_id>/matches/<match_id>/result
PATCH /tournaments/<tournament_id>/matches/<match_id>/result
```

Request shape:

```json
{
  "winner_team_id": "team_abc",
  "team_a_score": 12,
  "team_b_score": 8,
  "notes": "Manual result after referee confirmation"
}
```

MVP behavior:

- validate that winner belongs to the match
- record scores manually
- mark match `finished`
- do not auto-advance winners yet

## Frontend Screens And Components

### Tournament Dashboard

Purpose:

- overview of one tournament
- status, team count, round count, match count
- current matches needing action
- server capacity summary

Components:

- `TournamentHeader`
- `TournamentStatusBadge`
- `TournamentSummaryCards`
- `ActionQueue`
- `FleetCapacityPanel`

### Team Management

Purpose:

- create teams
- add/edit players
- set manual seeds

Components:

- `TeamList`
- `TeamCard`
- `PlayerList`
- `TeamForm`
- `PlayerForm`

### Rounds / Matches View

Purpose:

- show rounds in order
- show match cards grouped by round
- manually create or edit matches

Components:

- `RoundColumn`
- `RoundEditor`
- `MatchList`
- `CreateMatchForm`

### Match Card

Purpose:

- the primary operator unit during tournament execution

Shows:

- teams
- lifecycle state
- requested map/mode
- scheduled time
- result if finished
- attached Match Room/server status

Actions:

- allocate server
- copy connect command
- start match
- record result
- release server
- access safe admin controls

### Allocated Server Controls

Purpose:

- keep runtime controls clearly separated from tournament metadata

Controls:

- copy endpoint
- copy `connect <ip>:<port>`
- broadcast message
- change map override
- restart once verified in the next RCON phase
- release server

Do not add:

- raw RCON command input
- password display
- unauthenticated destructive controls beyond the current dev phase

## What Should Remain Manual For Now

Keep these manual in the next MVP:

- bracket generation
- winner advancement
- team seeding
- match scheduling
- result confirmation
- player identity matching between live server names and registered players

Reasoning:

- Manual controls are enough to demonstrate the platform flow.
- Automatic bracket logic adds complexity without improving the core platform story yet.
- Result recording should be trusted to the admin until scoreboard parsing and match-end detection are reliable.

## Persistence Recommendation

Recommendation: add PostgreSQL in the next tournament-management implementation phase.

Do not keep tournament data in-memory for another full feature phase.

Reasoning:

- Tournament, team, player, round, match, and result data are admin-authored records.
- Losing this data on backend Pod restart would make the tournament workflow feel fake.
- PostgreSQL is a standard, recruiter-friendly platform choice.
- It gives a natural next infrastructure story: database manifests or managed Postgres decision, migrations, backups later, and app config through environment variables/secrets.

Practical MVP approach:

- Use PostgreSQL for tournament domain data.
- Keep live server telemetry transient/cache-like.
- Keep Agones `GameServer` state as Kubernetes-owned runtime state.
- Start with a simple schema and one migration path.

If the goal is one more ultra-small UI-only iteration before database work, in-memory is acceptable only for a prototype. But the recommended next real phase is PostgreSQL.

## Risks

- State explosion: tournament states, server states, and live game states can drift unless lifecycle transitions are explicit.
- Over-automation: bracket generation and winner advancement can create wrong outcomes faster than manual admin tools.
- Live status ambiguity: `getstatus` is useful telemetry, not a source of truth for official results yet.
- RCON safety: every new command must remain whitelisted, validated, logged safely, and verified.
- Persistence scope creep: adding PostgreSQL should not become a full production data platform immediately.
- Auth gap: the current admin UI has no authentication, so any persistent/destructive tournament control should remain local/dev until auth is added.
- Server replacement: if an allocated server fails mid-match, the model needs an operator path to release/reallocate without losing the Match record.

## What Not To Build Yet

Do not build these in the next phase:

- public user accounts
- team self-registration
- payments
- full bracket generation
- automatic winner advancement
- automatic result ingestion from scoreboard
- player kick/ban UI
- raw RCON console
- chat moderation tools
- multi-region scheduling
- production-grade observability suite
- complex role-based access control

## Recommended Implementation Phases

### Phase 1: Persistent Tournament Core

Goal:

- introduce PostgreSQL-backed tournament records while preserving current Match Room allocation behavior.

Deliverables:

- database schema for tournaments, teams, players, rounds, matches, results
- backend CRUD endpoints
- simple frontend tournament dashboard
- manual team and match creation
- no bracket automation

### Phase 2: Match-To-Room Integration

Goal:

- wire tournament Matches to existing Match Rooms.

Deliverables:

- allocate server from a Match card
- map/mode config comes from Match fields
- Match enters `server_ready` only after Match Room is verified joinable
- release server from Match card
- result recording remains manual

### Phase 3: Operator Match Execution

Goal:

- make running a match clean for admins.

Deliverables:

- `start match` and `finish match` actions
- manual winner/score recording
- safe restart action if RCON verification is complete
- activity timeline per match
- clearer error recovery for allocation/config failures

### Phase 4: Bracket Assistance

Goal:

- add optional automation after the manual workflow is trustworthy.

Deliverables:

- bracket templates
- manual seeding helper
- optional winner advancement preview
- operator confirmation before advancing teams

### Phase 5: Production Hardening

Goal:

- make the admin tool safer beyond a dev demo.

Deliverables:

- authentication
- admin roles
- audit log
- database backups
- migration discipline
- basic monitoring and alerting

## MVP Design Decision Summary

- `Match` should become the tournament-facing object.
- `Match Room` should remain the lower-level server/session object.
- PostgreSQL should be added for the next real tournament-management phase.
- Brackets, seeding, and winner advancement should remain manual.
- Server allocation should continue using the proven Agones Match Room flow.
- RCON should stay whitelisted and never become a raw command console.
