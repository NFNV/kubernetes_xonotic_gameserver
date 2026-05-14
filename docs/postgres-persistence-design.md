# PostgreSQL Persistence Design

This document defines the minimal PostgreSQL schema for the tournament-management phase.

The goal is to persist admin-authored tournament data without trying to store all live game-server state. PostgreSQL should own tournament records, teams, players, rounds, matches, results, and the durable snapshot of server assignments. Kubernetes and Agones should remain the source of truth for live `GameServer` resources.

## Design Principles

- Keep the first schema small and easy to migrate.
- Store durable admin intent and results.
- Store enough server assignment metadata to recover the dashboard after backend restart.
- Do not store high-volume live telemetry yet.
- Do not mirror Kubernetes resources as full database records.
- Keep bracket generation, seeding automation, and winner advancement manual for now.

## ID And Timestamp Conventions

Use UUID primary keys for all persisted domain records.

Use these timestamp columns on every table:

- `created_at timestamptz not null default now()`
- `updated_at timestamptz not null default now()`

Use explicit lifecycle timestamps only where they matter, such as `started_at`, `finished_at`, `allocated_at`, and `released_at`.

## Lifecycle States

Use database `text` columns with application-level validation first. PostgreSQL enums can come later if state churn settles down.

Tournament states:

- `draft`
- `active`
- `finished`
- `cancelled`

Round states:

- `created`
- `scheduled`
- `running`
- `finished`

Match states:

- `created`
- `scheduled`
- `server_allocating`
- `server_ready`
- `running`
- `finished`
- `released`
- `failed`

Match room states:

- `created`
- `allocating`
- `configuring`
- `ready`
- `released`
- `failed`

Important distinction:

- Match `server_ready` is the tournament-facing state.
- Match room `ready` means the allocated server was configured, verified, and is joinable.
- An allocated Agones `GameServer` can exist while the room is not ready if configuration or verification failed.

## Tables

### `tournaments`

Stores one event or tournament container.

Fields:

```sql
id uuid primary key
name text not null
slug text unique
description text
status text not null default 'draft'
format text not null default 'manual'
started_at timestamptz
finished_at timestamptz
created_at timestamptz not null default now()
updated_at timestamptz not null default now()
```

Validation:

- `name` required, trimmed, reasonable max length.
- `status` must be one of tournament states.
- `format` should remain `manual` for the MVP.

Relationships:

- one tournament has many teams
- one tournament has many rounds
- one tournament has many matches

### `teams`

Stores manually managed teams inside one tournament.

Fields:

```sql
id uuid primary key
tournament_id uuid not null references tournaments(id) on delete cascade
name text not null
tag text
seed integer
created_at timestamptz not null default now()
updated_at timestamptz not null default now()
```

Recommended indexes/constraints:

```sql
unique (tournament_id, name)
unique (tournament_id, seed)
```

Validation:

- `name` required.
- `tag` optional and short.
- `seed` optional and positive when present.

Relationships:

- one team belongs to one tournament
- one team has many players
- one team can appear in many matches as `team_a` or `team_b`

### `players`

Stores manually entered players for a team.

Fields:

```sql
id uuid primary key
team_id uuid not null references teams(id) on delete cascade
display_name text not null
handle text
created_at timestamptz not null default now()
updated_at timestamptz not null default now()
```

Recommended indexes/constraints:

```sql
unique (team_id, display_name)
```

Validation:

- `display_name` required.
- `handle` optional.

Not stored yet:

- login identity
- email
- Discord ID
- country
- ranking
- live Xonotic client ID

### `rounds`

Stores manually created round groups within a tournament.

Fields:

```sql
id uuid primary key
tournament_id uuid not null references tournaments(id) on delete cascade
name text not null
round_order integer not null
status text not null default 'created'
created_at timestamptz not null default now()
updated_at timestamptz not null default now()
```

Recommended indexes/constraints:

```sql
unique (tournament_id, round_order)
unique (tournament_id, name)
```

Validation:

- `round_order` positive integer.
- `status` must be one of round states.

Relationships:

- one round belongs to one tournament
- one round has many matches

### `matches`

Stores the tournament-facing match record.

Fields:

```sql
id uuid primary key
tournament_id uuid not null references tournaments(id) on delete cascade
round_id uuid references rounds(id) on delete set null
team_a_id uuid references teams(id) on delete restrict
team_b_id uuid references teams(id) on delete restrict
status text not null default 'created'
scheduled_at timestamptz
started_at timestamptz
finished_at timestamptz
winner_team_id uuid references teams(id) on delete restrict
team_a_score integer
team_b_score integer
result_notes text
requested_map text
requested_game_mode text
created_at timestamptz not null default now()
updated_at timestamptz not null default now()
```

Recommended indexes:

```sql
index on matches (tournament_id)
index on matches (round_id)
index on matches (status)
index on matches (scheduled_at)
```

Validation:

- `status` must be one of match states.
- `team_a_id` and `team_b_id` must belong to the same tournament as the match.
- `team_a_id` and `team_b_id` must be different when both are present.
- `winner_team_id`, when present, must equal `team_a_id` or `team_b_id`.
- scores are optional, non-negative integers.
- `requested_map` must be one of the backend allowlisted maps when present.
- `requested_game_mode` must be one of the backend allowlisted modes when present.

Relationships:

- one match belongs to one tournament
- one match optionally belongs to one round
- one match has zero or one active match room
- one match can keep historical match room records after server replacement if needed later

MVP simplification:

- treat each match as having at most one current room.
- do not model best-of series or multiple maps yet.

### `match_server_assignments`

Implemented as the first durable bridge between PostgreSQL tournament matches and allocated Agones `GameServer` resources.

This table is not a full mirror of Agones. It stores the assignment snapshot needed for the admin dashboard to recover which tournament match was assigned to which server endpoint after backend restart.

Fields:

```sql
id uuid primary key
tournament_id uuid not null references tournaments(id) on delete cascade
match_id uuid not null references matches(id) on delete cascade
allocated_game_server_name text
allocation_request_name text
address text
port integer
status text not null default 'active'
created_at timestamptz not null default now()
released_at timestamptz
updated_at timestamptz not null default now()
```

Recommended indexes:

```sql
index on match_server_assignments (tournament_id)
index on match_server_assignments (match_id)
index on match_server_assignments (status)
unique (allocated_game_server_name)
unique (match_id) where status = 'active'
```

Validation:

- `status` is currently `active`, `released`, or `failed`.
- `port` must be valid TCP/UDP port range when present.
- `allocated_game_server_name` should be present after allocation succeeds.

Relationships:

- one server assignment belongs to one tournament match
- one tournament match has at most one active assignment
- one tournament match can keep released assignment history

Recommended MVP constraint:

```sql
unique (match_id) where status = 'active'
```

The in-memory Match Room model still exists separately for lower-level/manual server workflow. `match_server_assignments` is the persisted tournament-match bridge.

## Relationship Summary

```text
tournaments 1 -> many teams
tournaments 1 -> many rounds
tournaments 1 -> many matches
teams 1 -> many players
rounds 1 -> many matches
matches 1 -> zero-or-one active match_server_assignments
match_server_assignments 1 -> one Agones GameServer assignment snapshot
```

## What Remains Transient In Kubernetes/Agones

These should not become durable PostgreSQL records in the MVP:

- Agones `Fleet`
- Agones `FleetAutoscaler`
- Agones `GameServer` lifecycle internals
- Agones `GameServerAllocation` resources
- Kubernetes Pods
- Kubernetes Services
- Kubernetes Events
- live container logs
- current Ready/Allocated/Shutdown counts from Kubernetes
- current Pod phase
- current node name

The backend should query Kubernetes/Agones live for these when needed.

PostgreSQL may store references such as:

- `allocated_game_server_name`
- `allocation_request_name`
- `address`
- `port`
- allocation timestamps
- release timestamps

But Kubernetes and Agones remain the source of truth for whether that runtime resource currently exists.

## What Should Not Be Stored Yet

Do not store these in the first persistence phase:

- raw RCON commands
- RCON password
- frontend session data
- user accounts
- admin roles
- player authentication identities
- full live status polling history
- raw scoreboards over time
- chat logs
- server logs
- bracket tree structure
- automatic winner advancement records
- map veto/pick-ban data
- audit log
- payments or registration data

Rationale:

- These are either sensitive, high-volume, or not needed for the next practical MVP.
- Storing them now would force premature schema and security decisions.

## Live Status Handling

Keep live `getstatus` telemetry mostly transient.

Recommended MVP behavior:

- Continue polling live status from the allocated endpoint.
- Cache the latest successful live status in process memory.
- Optionally store a small `last_status_error` JSON snapshot in `match_rooms`.
- Do not persist every telemetry sample.

If the backend restarts:

- It can reload `match_rooms` with `status = ready`.
- It can resume polling their stored `address:port`.
- If the Agones `GameServer` no longer exists, mark the room `failed` or `released` depending on live Kubernetes state.

## Minimal Initial Migration Order

Create tables in this order:

1. `tournaments`
2. `teams`
3. `players`
4. `rounds`
5. `matches`
6. `match_server_assignments`

Add simple indexes with the first migration.

Avoid complicated triggers in the first pass. Have the application update `updated_at`.

## Suggested PostgreSQL DDL Sketch

This is intentionally a sketch, not an implementation file.

```sql
create table tournaments (
  id uuid primary key,
  name text not null,
  slug text unique,
  description text,
  status text not null default 'draft',
  format text not null default 'manual',
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table teams (
  id uuid primary key,
  tournament_id uuid not null references tournaments(id) on delete cascade,
  name text not null,
  tag text,
  seed integer,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tournament_id, name),
  unique (tournament_id, seed)
);

create table players (
  id uuid primary key,
  team_id uuid not null references teams(id) on delete cascade,
  display_name text not null,
  handle text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (team_id, display_name)
);

create table rounds (
  id uuid primary key,
  tournament_id uuid not null references tournaments(id) on delete cascade,
  name text not null,
  round_order integer not null,
  status text not null default 'created',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tournament_id, round_order),
  unique (tournament_id, name)
);

create table matches (
  id uuid primary key,
  tournament_id uuid not null references tournaments(id) on delete cascade,
  round_id uuid references rounds(id) on delete set null,
  team_a_id uuid references teams(id) on delete restrict,
  team_b_id uuid references teams(id) on delete restrict,
  status text not null default 'created',
  scheduled_at timestamptz,
  started_at timestamptz,
  finished_at timestamptz,
  winner_team_id uuid references teams(id) on delete restrict,
  team_a_score integer,
  team_b_score integer,
  result_notes text,
  requested_map text,
  requested_game_mode text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table match_server_assignments (
  id uuid primary key,
  tournament_id uuid not null references tournaments(id) on delete cascade,
  match_id uuid not null references matches(id) on delete cascade,
  allocated_game_server_name text,
  allocation_request_name text,
  address text,
  port integer,
  status text not null default 'active',
  created_at timestamptz not null default now(),
  released_at timestamptz,
  updated_at timestamptz not null default now(),
  unique (allocated_game_server_name)
);

create index matches_tournament_id_idx on matches (tournament_id);
create index matches_round_id_idx on matches (round_id);
create index matches_status_idx on matches (status);
create index match_server_assignments_tournament_id_idx on match_server_assignments (tournament_id);
create index match_server_assignments_match_id_idx on match_server_assignments (match_id);
create index match_server_assignments_status_idx on match_server_assignments (status);
create unique index match_server_assignments_one_active_idx
  on match_server_assignments (match_id)
  where status = 'active';
```

## Practical Next Implementation Step

When implementation starts, do this in a narrow slice:

1. Add PostgreSQL connectivity and migrations.
2. Persist tournaments, teams, players, rounds, and matches.
3. Keep existing Match Room runtime behavior working.
4. Add `match_rooms` persistence only after the tournament match CRUD path is stable.
5. Keep live status and Kubernetes reconciliation simple and best-effort.

## Phase 1 Implementation Status

Implemented in the first persistence phase:

- dev PostgreSQL manifests under `platform/postgres/`
- backend PostgreSQL connectivity from environment variables
- startup/on-demand migration path
- `tournaments`
- `teams`
- `players`
- `rounds`
- `matches`
- create/list/get tournaments
- create/list teams for a tournament
- create/list rounds for a tournament
- create/list matches for a tournament
- persisted tournament match server assignments through `match_server_assignments`
- allocate/release server endpoints for persisted tournament matches

Still deferred:

- result recording
- bracket generation
- automatic winner advancement
- persisted live telemetry history
- auth
- production database hardening
