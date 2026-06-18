#!/usr/bin/env python3
import os
import re
import socket
import struct
import time
import uuid
from datetime import UTC, datetime
from threading import Lock
from typing import Any

import psycopg
from psycopg.rows import dict_row
from flask import Flask, Response, g, jsonify, request
from kubernetes import client, config
from kubernetes.client import ApiException
from kubernetes.config.config_exception import ConfigException
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from werkzeug.exceptions import BadRequest, HTTPException, UnsupportedMediaType


APP = Flask(__name__)

HTTP_REQUEST_COUNT = Counter(
    "allocator_backend_http_requests_total",
    "Allocator backend HTTP requests.",
    ("method", "endpoint", "status"),
)
HTTP_REQUEST_LATENCY_SECONDS = Histogram(
    "allocator_backend_http_request_duration_seconds",
    "Allocator backend HTTP request latency.",
    ("method", "endpoint"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
ALLOCATION_ATTEMPTS = Counter("allocator_allocation_attempts_total", "Agones GameServer allocation attempts.")
ALLOCATION_SUCCESSES = Counter("allocator_allocation_successes_total", "Agones GameServer allocation successes.")
ALLOCATION_FAILURES = Counter("allocator_allocation_failures_total", "Agones GameServer allocation failures.", ("reason",))
ACTIVE_MATCH_SERVER_ASSIGNMENTS = Gauge(
    "allocator_active_match_server_assignments",
    "Active persisted tournament match server assignments.",
)
RCON_COMMAND_ATTEMPTS = Counter("allocator_rcon_command_attempts_total", "RCON command attempts.", ("command",))
RCON_COMMAND_FAILURES = Counter("allocator_rcon_command_failures_total", "RCON command failures.", ("command", "reason"))
MAP_MODE_VERIFICATION_SUCCESSES = Counter(
    "allocator_map_mode_verification_successes_total",
    "Successful requested map/mode verifications after server configuration.",
    ("requested_game_mode", "requested_map"),
)
MAP_MODE_VERIFICATION_FAILURES = Counter(
    "allocator_map_mode_verification_failures_total",
    "Failed requested map/mode verifications after server configuration.",
    ("requested_game_mode", "requested_map", "reason"),
)

AGONES_NAMESPACE = os.environ.get("AGONES_NAMESPACE", "xonotic-agones")
FLEET_NAME = os.environ.get("FLEET_NAME", "xonotic-fleet")
GAME_LABEL = os.environ.get("GAME_LABEL", "xonotic")
DEFAULT_SERVER_POOL_ID = "south-america-default"
SERVER_POOLS = (
    {
        "id": DEFAULT_SERVER_POOL_ID,
        "display_name": "South America - Default",
        "region": "south-america",
        "provider": "gcp",
        "gcp_region": "southamerica-west1",
        "cluster_name": "xonotic-mvp",
        "agones_namespace": "xonotic-agones",
        "fleet_name": "xonotic-fleet",
        "provisioned": True,
        "enabled": True,
        "default": True,
        "status": "provisioned",
    },
    {
        "id": "europe-simulated",
        "display_name": "Europe - Simulated",
        "region": "europe",
        "provider": "gcp",
        "gcp_region": None,
        "cluster_name": None,
        "agones_namespace": None,
        "fleet_name": None,
        "provisioned": False,
        "enabled": False,
        "default": False,
        "status": "not-provisioned",
    },
    {
        "id": "north-america-simulated",
        "display_name": "North America - Simulated",
        "region": "north-america",
        "provider": "gcp",
        "gcp_region": None,
        "cluster_name": None,
        "agones_namespace": None,
        "fleet_name": None,
        "provisioned": False,
        "enabled": False,
        "default": False,
        "status": "not-provisioned",
    },
)
ALLOCATION_TIMEOUT_SECONDS = int(os.environ.get("ALLOCATION_TIMEOUT_SECONDS", "5"))
ALLOCATION_POLL_INTERVAL_SECONDS = float(os.environ.get("ALLOCATION_POLL_INTERVAL_SECONDS", "0.25"))
XONOTIC_STATUS_TIMEOUT_SECONDS = float(os.environ.get("XONOTIC_STATUS_TIMEOUT_SECONDS", "1"))
XONOTIC_STATUS_CACHE_SECONDS = float(os.environ.get("XONOTIC_STATUS_CACHE_SECONDS", "5"))
XONOTIC_RCON_PASSWORD = os.environ.get("XONOTIC_RCON_PASSWORD", "")
XONOTIC_RCON_TIMEOUT_SECONDS = float(os.environ.get("XONOTIC_RCON_TIMEOUT_SECONDS", "2"))
XONOTIC_RCON_OUTPUT_LIMIT = int(os.environ.get("XONOTIC_RCON_OUTPUT_LIMIT", "4000"))
XONOTIC_RCON_CHANGE_MAP_STATUS_DELAY_SECONDS = float(os.environ.get("XONOTIC_RCON_CHANGE_MAP_STATUS_DELAY_SECONDS", "1"))
XONOTIC_RCON_CHANGE_MAP_VERIFY_TIMEOUT_SECONDS = float(os.environ.get("XONOTIC_RCON_CHANGE_MAP_VERIFY_TIMEOUT_SECONDS", "12"))
XONOTIC_RCON_CHANGE_MAP_VERIFY_INTERVAL_SECONDS = float(os.environ.get("XONOTIC_RCON_CHANGE_MAP_VERIFY_INTERVAL_SECONDS", "1"))
TOURNAMENT_ALLOCATION_STALE_SECONDS = float(os.environ.get("TOURNAMENT_ALLOCATION_STALE_SECONDS", "60"))
XONOTIC_RCON_PROTOCOLS = tuple(
    protocol.strip()
    for protocol in os.environ.get("XONOTIC_RCON_PROTOCOLS", "secure-challenge,secure-time,plaintext").split(",")
    if protocol.strip()
)

ALLOCATION_GROUP = "allocation.agones.dev"
ALLOCATION_VERSION = "v1"
ALLOCATION_PLURAL = "gameserverallocations"
ALLOCATION_RESOURCE_KIND = "GameServerAllocation"
GAMESERVER_RESOURCE_KIND = "GameServer"
FLEET_RESOURCE_KIND = "Fleet"

MATCHES: dict[str, dict[str, Any]] = {}
MATCHES_LOCK = Lock()
STATUS_CACHE: dict[str, dict[str, Any]] = {}
STATUS_CACHE_LOCK = Lock()
DEFAULT_MAX_PLAYERS = int(os.environ.get("DEFAULT_MATCH_MAX_PLAYERS", "8"))
MAX_MATCH_PLAYERS_LIMIT = int(os.environ.get("MAX_MATCH_PLAYERS_LIMIT", "32"))
FINISHED_MATCH_STATUSES = {"released", "finished"}
PLAYER_STATUS_PATTERN = re.compile(r'^(?P<score>\S+)\s+(?P<ping>\d+)(?:\s+(?P<team>\d+))?\s+"(?P<name>.*)"$')
ADMIN_BROADCAST_MAX_LENGTH = 160
DEFAULT_REQUESTED_MAP = os.environ.get("DEFAULT_REQUESTED_MAP", "xoylent")
DEFAULT_REQUESTED_GAME_MODE = os.environ.get("DEFAULT_REQUESTED_GAME_MODE", "dm")
XONOTIC_ENABLE_EXPERIMENTAL_GAME_CONFIG = os.environ.get("XONOTIC_ENABLE_EXPERIMENTAL_GAME_CONFIG", "0") == "1"
GAME_CONFIG_OPTIONS = {
    "dm": {
        "label": "Deathmatch",
        "selectable": True,
        "verified_maps": ("xoylent", "stormkeep", "solarium"),
        "experimental_maps": ("drain", "darkzone", "runningman", "warfare"),
    },
    "tdm": {
        "label": "Team Deathmatch",
        "selectable": True,
        "verified_maps": ("stormkeep",),
        "experimental_maps": ("xoylent", "solarium", "darkzone", "implosion", "runningman", "silentsiege"),
    },
    "ctf": {
        "label": "Capture The Flag",
        "selectable": True,
        "verified_maps": ("runningmanctf",),
        "experimental_maps": ("catharsis", "courtfun", "dance", "go", "implosion", "space-elevator", "vorix"),
    },
    "duel": {
        "label": "Duel",
        "selectable": True,
        "verified_maps": ("xoylent",),
        "experimental_maps": ("darkzone", "fuse", "stormkeep", "warfare"),
    },
    "ca": {
        "label": "Clan Arena",
        "selectable": True,
        "verified_maps": ("stormkeep", "xoylent"),
        "experimental_maps": ("darkzone", "implosion", "runningman", "solarium"),
    },
    "dom": {
        "label": "Domination",
        "selectable": False,
        "disabled_reason": "deferred until Domination map/mode combinations are verified",
        "verified_maps": (),
        "experimental_maps": ("afterslime", "geoplanetary", "glowplant", "implosion", "runningmanctf", "stormkeep"),
    },
    "kh": {
        "label": "Key Hunt",
        "selectable": False,
        "disabled_reason": "deferred until Key Hunt map/mode combinations are verified",
        "verified_maps": (),
        "experimental_maps": ("implosion", "runningman", "runningmanctf", "solarium", "stormkeep"),
    },
}


@APP.errorhandler(Exception)
def handle_unexpected_error(exc: Exception):
    if isinstance(exc, HTTPException):
        return jsonify({"error": "http_error", "message": exc.description}), exc.code or 500

    APP.logger.exception("Unhandled backend exception")
    return jsonify({"error": "internal_server_error", "message": "unexpected backend error"}), 500


@APP.before_request
def start_request_metrics_timer():
    g.metrics_started_at = time.perf_counter()


@APP.after_request
def record_request_metrics(response):
    if request.path != "/metrics":
        endpoint = request.url_rule.rule if request.url_rule else request.path
        HTTP_REQUEST_COUNT.labels(request.method, endpoint, str(response.status_code)).inc()
        started_at = getattr(g, "metrics_started_at", None)
        if started_at is not None:
            HTTP_REQUEST_LATENCY_SECONDS.labels(request.method, endpoint).observe(time.perf_counter() - started_at)
    return response


ADMIN_ALLOWED_MAPS = tuple(sorted({map_name for config in GAME_CONFIG_OPTIONS.values() for map_name in config["verified_maps"] + config["experimental_maps"]}))
ADMIN_ALLOWED_GAME_MODES = tuple(GAME_CONFIG_OPTIONS.keys())
GAME_MODE_ALIASES = {
    "deathmatch": "dm",
    "dm": "dm",
    "ffa": "dm",
    "free for all": "dm",
    "team deathmatch": "tdm",
    "tdm": "tdm",
    "capture the flag": "ctf",
    "ctf": "ctf",
    "duel": "duel",
    "duels": "duel",
    "clan arena": "ca",
    "clanarena": "ca",
    "ca": "ca",
    "domination": "dom",
    "dom": "dom",
    "key hunt": "kh",
    "keyhunt": "kh",
    "kh": "kh",
}
DATABASE_URL = os.environ.get("DATABASE_URL", "")
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "")
POSTGRES_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.environ.get("POSTGRES_DB", "")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")
POSTGRES_CONNECT_TIMEOUT_SECONDS = int(os.environ.get("POSTGRES_CONNECT_TIMEOUT_SECONDS", "3"))

RCON_PACKET_PREFIX = b"\xff\xff\xff\xff"
DB_MIGRATIONS_READY = False
DB_MIGRATION_ERROR: str | None = None
DB_MIGRATIONS_LOCK = Lock()

TOURNAMENT_STATUSES = ("draft", "active", "completed", "finished", "cancelled")
ROUND_STATUSES = ("created", "scheduled", "running", "finished")
TOURNAMENT_MATCH_STATUSES = (
    "created",
    "scheduled",
    "server_allocating",
    "server_ready",
    "running",
    "finished",
    "released",
    "failed",
)
SUPPORTED_BRACKET_SIZES = (2, 4, 8)
BRACKET_REGENERABLE_MATCH_STATUSES = ("created", "scheduled")
BRACKET_SEED_PAIRS = {
    2: ((1, 2),),
    4: ((1, 4), (2, 3)),
    8: ((1, 8), (4, 5), (2, 7), (3, 6)),
}
BRACKET_ROUND_NAMES = {
    2: ("Final",),
    4: ("Semifinals", "Final"),
    8: ("Quarterfinals", "Semifinals", "Final"),
}

DB_MIGRATIONS = (
    (
        "001_tournament_core",
        """
        CREATE TABLE IF NOT EXISTS tournaments (
          id uuid PRIMARY KEY,
          name text NOT NULL,
          slug text UNIQUE,
          description text,
          status text NOT NULL DEFAULT 'draft',
          format text NOT NULL DEFAULT 'manual',
          winner_team_id uuid,
          bracket_size integer,
          bracket_generated_at timestamptz,
          started_at timestamptz,
          finished_at timestamptz,
          completed_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS teams (
          id uuid PRIMARY KEY,
          tournament_id uuid NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
          name text NOT NULL,
          tag text,
          seed integer,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tournament_id, name),
          UNIQUE (tournament_id, seed)
        );

        CREATE TABLE IF NOT EXISTS players (
          id uuid PRIMARY KEY,
          team_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
          display_name text NOT NULL,
          handle text,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (team_id, display_name)
        );

        CREATE TABLE IF NOT EXISTS rounds (
          id uuid PRIMARY KEY,
          tournament_id uuid NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
          name text NOT NULL,
          round_order integer NOT NULL,
          status text NOT NULL DEFAULT 'created',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tournament_id, round_order),
          UNIQUE (tournament_id, name)
        );

        CREATE TABLE IF NOT EXISTS matches (
          id uuid PRIMARY KEY,
          tournament_id uuid NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
          round_id uuid REFERENCES rounds(id) ON DELETE SET NULL,
          team_a_id uuid REFERENCES teams(id) ON DELETE RESTRICT,
          team_b_id uuid REFERENCES teams(id) ON DELETE RESTRICT,
          status text NOT NULL DEFAULT 'created',
          scheduled_at timestamptz,
          started_at timestamptz,
          finished_at timestamptz,
          winner_team_id uuid REFERENCES teams(id) ON DELETE RESTRICT,
          team_a_score integer,
          team_b_score integer,
          result_notes text,
          requested_map text,
          requested_game_mode text,
          requested_region text,
          requested_server_pool_id text,
          bracket_position integer,
          next_match_id uuid REFERENCES matches(id) ON DELETE SET NULL,
          next_match_slot text,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE INDEX IF NOT EXISTS matches_tournament_id_idx ON matches (tournament_id);
        CREATE INDEX IF NOT EXISTS matches_round_id_idx ON matches (round_id);
        CREATE INDEX IF NOT EXISTS matches_status_idx ON matches (status);
        CREATE INDEX IF NOT EXISTS matches_scheduled_at_idx ON matches (scheduled_at);
        """,
    ),
    (
        "002_match_server_assignments",
        """
        CREATE TABLE IF NOT EXISTS match_server_assignments (
          id uuid PRIMARY KEY,
          tournament_id uuid NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
          match_id uuid NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
          allocated_game_server_name text NOT NULL,
          allocation_request_name text,
          address text NOT NULL,
          port integer NOT NULL,
          server_pool_id text,
          region text,
          provider text,
          gcp_region text,
          cluster_name text,
          agones_namespace text,
          fleet_name text,
          status text NOT NULL DEFAULT 'active',
          created_at timestamptz NOT NULL DEFAULT now(),
          released_at timestamptz,
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (allocated_game_server_name)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS match_server_assignments_one_active_idx
          ON match_server_assignments (match_id)
          WHERE status = 'active';

        CREATE INDEX IF NOT EXISTS match_server_assignments_tournament_id_idx
          ON match_server_assignments (tournament_id);

        CREATE INDEX IF NOT EXISTS match_server_assignments_match_id_idx
          ON match_server_assignments (match_id);

        CREATE INDEX IF NOT EXISTS match_server_assignments_status_idx
          ON match_server_assignments (status);
        """,
    ),
    (
        "003_single_elimination_brackets",
        """
        ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS bracket_size integer;
        ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS bracket_generated_at timestamptz;
        ALTER TABLE matches ADD COLUMN IF NOT EXISTS bracket_position integer;
        ALTER TABLE matches ADD COLUMN IF NOT EXISTS next_match_id uuid;
        ALTER TABLE matches ADD COLUMN IF NOT EXISTS next_match_slot text;

        DO $$
        BEGIN
          ALTER TABLE tournaments
            ADD CONSTRAINT tournaments_bracket_size_check
            CHECK (bracket_size IS NULL OR bracket_size IN (2, 4, 8));
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;

        DO $$
        BEGIN
          ALTER TABLE matches
            ADD CONSTRAINT matches_next_match_id_fkey
            FOREIGN KEY (next_match_id) REFERENCES matches(id) ON DELETE SET NULL;
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;

        DO $$
        BEGIN
          ALTER TABLE matches
            ADD CONSTRAINT matches_next_match_slot_check
            CHECK (next_match_slot IS NULL OR next_match_slot IN ('team_a', 'team_b'));
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;

        CREATE INDEX IF NOT EXISTS matches_bracket_order_idx
          ON matches (tournament_id, round_id, bracket_position)
          WHERE bracket_position IS NOT NULL;

        CREATE INDEX IF NOT EXISTS matches_next_match_id_idx
          ON matches (next_match_id)
          WHERE next_match_id IS NOT NULL;
        """,
    ),
    (
        "004_tournament_finalize_summary",
        """
        ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS winner_team_id uuid;
        ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS completed_at timestamptz;

        DO $$
        BEGIN
          ALTER TABLE tournaments
            ADD CONSTRAINT tournaments_winner_team_id_fkey
            FOREIGN KEY (winner_team_id) REFERENCES teams(id) ON DELETE RESTRICT;
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;

        CREATE INDEX IF NOT EXISTS tournaments_winner_team_id_idx
          ON tournaments (winner_team_id)
          WHERE winner_team_id IS NOT NULL;
        """,
    ),
    (
        "005_tournament_completed_at",
        """
        ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS completed_at timestamptz;
        """,
    ),
    (
        "006_server_pool_metadata",
        """
        ALTER TABLE matches ADD COLUMN IF NOT EXISTS requested_region text;
        ALTER TABLE matches ADD COLUMN IF NOT EXISTS requested_server_pool_id text;

        UPDATE matches
        SET requested_region = COALESCE(requested_region, 'south-america'),
            requested_server_pool_id = COALESCE(requested_server_pool_id, 'south-america-default')
        WHERE requested_region IS NULL OR requested_server_pool_id IS NULL;

        ALTER TABLE match_server_assignments ADD COLUMN IF NOT EXISTS server_pool_id text;
        ALTER TABLE match_server_assignments ADD COLUMN IF NOT EXISTS region text;
        ALTER TABLE match_server_assignments ADD COLUMN IF NOT EXISTS provider text;
        ALTER TABLE match_server_assignments ADD COLUMN IF NOT EXISTS gcp_region text;
        ALTER TABLE match_server_assignments ADD COLUMN IF NOT EXISTS cluster_name text;
        ALTER TABLE match_server_assignments ADD COLUMN IF NOT EXISTS agones_namespace text;
        ALTER TABLE match_server_assignments ADD COLUMN IF NOT EXISTS fleet_name text;

        UPDATE match_server_assignments
        SET server_pool_id = COALESCE(server_pool_id, 'south-america-default'),
            region = COALESCE(region, 'south-america'),
            provider = COALESCE(provider, 'gcp'),
            gcp_region = COALESCE(gcp_region, 'southamerica-west1'),
            cluster_name = COALESCE(cluster_name, 'xonotic-mvp'),
            agones_namespace = COALESCE(agones_namespace, 'xonotic-agones'),
            fleet_name = COALESCE(fleet_name, 'xonotic-fleet')
        WHERE server_pool_id IS NULL
           OR region IS NULL
           OR provider IS NULL
           OR gcp_region IS NULL
           OR cluster_name IS NULL
           OR agones_namespace IS NULL
           OR fleet_name IS NULL;

        CREATE INDEX IF NOT EXISTS matches_requested_server_pool_idx
          ON matches (requested_server_pool_id);

        CREATE INDEX IF NOT EXISTS match_server_assignments_server_pool_idx
          ON match_server_assignments (server_pool_id);
        """,
    ),
)


class BackendApiError(Exception):
    def __init__(self, payload: dict[str, Any], status_code: int):
        super().__init__(payload.get("message", "backend API error"))
        self.payload = payload
        self.status_code = status_code


@APP.errorhandler(BackendApiError)
def handle_backend_api_error(exc: BackendApiError):
    return jsonify(exc.payload), exc.status_code


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def new_db_id() -> str:
    return str(uuid.uuid4())


def db_configured() -> bool:
    return bool(DATABASE_URL or (POSTGRES_HOST and POSTGRES_DB and POSTGRES_USER))


def db_connect():
    if not db_configured():
        raise BackendApiError(
            {
                "error": "database_not_configured",
                "message": "PostgreSQL is not configured for tournament persistence",
            },
            503,
        )

    try:
        if DATABASE_URL:
            return psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=POSTGRES_CONNECT_TIMEOUT_SECONDS)

        return psycopg.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            row_factory=dict_row,
            connect_timeout=POSTGRES_CONNECT_TIMEOUT_SECONDS,
        )
    except psycopg.Error as exc:
        APP.logger.error("PostgreSQL connection failed: %s", exc)
        raise BackendApiError(
            {
                "error": "database_unavailable",
                "message": "PostgreSQL is unavailable",
                "details": str(exc),
            },
            503,
        ) from exc


def active_match_server_assignment_count() -> float:
    if not db_configured():
        return 0

    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS count FROM match_server_assignments WHERE status = 'active'")
                row = cur.fetchone()
                return float(row["count"] if row else 0)
    except Exception:
        APP.logger.warning("Failed to collect active match server assignment metric", exc_info=True)
        return 0


ACTIVE_MATCH_SERVER_ASSIGNMENTS.set_function(active_match_server_assignment_count)


def row_to_json(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None

    normalized = {}
    for key, value in row.items():
        if isinstance(value, uuid.UUID):
            normalized[key] = str(value)
        elif isinstance(value, datetime):
            normalized[key] = value.astimezone(UTC).isoformat().replace("+00:00", "Z")
        else:
            normalized[key] = value
    return normalized


def rows_to_json(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row_to_json(row) for row in rows if row is not None]


def configured_server_pools() -> list[dict[str, Any]]:
    return [pool.copy() for pool in SERVER_POOLS]


def enabled_server_pools() -> list[dict[str, Any]]:
    return [pool.copy() for pool in SERVER_POOLS if pool.get("enabled") and pool.get("provisioned", True)]


def default_server_pool() -> dict[str, Any]:
    for pool in enabled_server_pools():
        if pool.get("default"):
            return pool

    pools = enabled_server_pools()
    if not pools:
        raise BackendApiError(
            {
                "error": "server_pool_unavailable",
                "message": "no enabled server pools are configured",
            },
            503,
        )
    return pools[0]


def server_pool_response(pool: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": pool["id"],
        "display_name": pool["display_name"],
        "region": pool["region"],
        "provider": pool["provider"],
        "gcp_region": pool.get("gcp_region"),
        "cluster_name": pool.get("cluster_name"),
        "agones_namespace": pool.get("agones_namespace"),
        "fleet_name": pool.get("fleet_name"),
        "provisioned": bool(pool.get("provisioned", True)),
        "enabled": bool(pool.get("enabled")),
        "default": bool(pool.get("default")),
        "status": pool.get("status") or ("provisioned" if pool.get("provisioned", True) else "not-provisioned"),
    }


def server_pool_metadata(pool: dict[str, Any]) -> dict[str, Any]:
    return {
        "server_pool_id": pool["id"],
        "region": pool["region"],
        "provider": pool["provider"],
        "gcp_region": pool["gcp_region"],
        "cluster_name": pool["cluster_name"],
        "agones_namespace": pool["agones_namespace"],
        "fleet_name": pool["fleet_name"],
    }


def find_server_pool_by_id(server_pool_id: str) -> dict[str, Any] | None:
    return next((pool.copy() for pool in SERVER_POOLS if pool["id"] == server_pool_id), None)


def validate_requested_region(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise BackendApiError({"error": "invalid_region", "message": "requested_region must be a string"}, 400)

    region = value.strip().lower()
    if not region:
        return None
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,80}[a-z0-9]", region):
        raise BackendApiError({"error": "invalid_region", "message": "requested_region contains unsupported characters"}, 400)
    return region


def validate_requested_server_pool_id(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise BackendApiError({"error": "invalid_server_pool", "message": "requested_server_pool_id must be a string"}, 400)

    server_pool_id = value.strip()
    if not server_pool_id:
        return None
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,100}[a-z0-9]", server_pool_id):
        raise BackendApiError({"error": "invalid_server_pool", "message": "requested_server_pool_id contains unsupported characters"}, 400)
    return server_pool_id


def resolve_server_pool(
    requested_server_pool_id: Any = None,
    requested_region: Any = None,
) -> dict[str, Any]:
    server_pool_id = validate_requested_server_pool_id(requested_server_pool_id)
    region = validate_requested_region(requested_region)

    if server_pool_id:
        pool = find_server_pool_by_id(server_pool_id)
        if not pool:
            raise BackendApiError(
                {
                    "error": "unknown_server_pool",
                    "message": f"server pool {server_pool_id} is not configured",
                    "requested_server_pool_id": server_pool_id,
                },
                400,
            )
        if not pool.get("provisioned", True):
            raise BackendApiError(
                {
                    "error": "server_pool_not_provisioned",
                    "message": f"Server pool {pool['display_name']} is not provisioned in this dev deployment.",
                    "requested_server_pool_id": server_pool_id,
                    "server_pool": server_pool_response(pool),
                },
                400,
            )
        if not pool.get("enabled"):
            raise BackendApiError(
                {
                    "error": "disabled_server_pool",
                    "message": f"server pool {server_pool_id} is disabled",
                    "requested_server_pool_id": server_pool_id,
                },
                400,
            )
        if region and pool["region"] != region:
            raise BackendApiError(
                {
                    "error": "server_pool_region_mismatch",
                    "message": f"server pool {server_pool_id} belongs to {pool['region']}, not {region}",
                    "requested_server_pool_id": server_pool_id,
                    "requested_region": region,
                    "server_pool_region": pool["region"],
                },
                400,
            )
        return pool

    if region:
        configured_region_pools = [pool for pool in configured_server_pools() if pool["region"] == region]
        region_pools = [pool for pool in enabled_server_pools() if pool["region"] == region]
        if not region_pools:
            simulated_pool = next((pool for pool in configured_region_pools if not pool.get("provisioned", True)), None)
            if simulated_pool:
                raise BackendApiError(
                    {
                        "error": "server_pool_not_provisioned",
                        "message": f"Server pool {simulated_pool['display_name']} is not provisioned in this dev deployment.",
                        "requested_region": region,
                        "server_pool": server_pool_response(simulated_pool),
                    },
                    400,
                )
            raise BackendApiError(
                {
                    "error": "unknown_region_server_pool",
                    "message": f"no enabled server pool is configured for region {region}",
                    "requested_region": region,
                },
                400,
            )
        return next((pool for pool in region_pools if pool.get("default")), region_pools[0])

    return default_server_pool()


def default_server_pool_metadata() -> dict[str, Any]:
    return server_pool_metadata(default_server_pool())


def tournament_match_response(match: dict[str, Any] | None) -> dict[str, Any] | None:
    response = row_to_json(match)
    if response is None:
        return None

    default_metadata = default_server_pool_metadata()
    response["requested_region"] = response.get("requested_region") or default_metadata["region"]
    response["requested_server_pool_id"] = response.get("requested_server_pool_id") or default_metadata["server_pool_id"]
    return response


def tournament_match_rows_response(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [response for response in (tournament_match_response(match) for match in matches) if response is not None]


def run_db_migrations() -> None:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                  id text PRIMARY KEY,
                  applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )

            for migration_id, migration_sql in DB_MIGRATIONS:
                cur.execute("SELECT id FROM schema_migrations WHERE id = %s", (migration_id,))
                if cur.fetchone():
                    continue

                APP.logger.info("Applying database migration %s", migration_id)
                cur.execute(migration_sql)
                cur.execute("INSERT INTO schema_migrations (id) VALUES (%s)", (migration_id,))

        conn.commit()


def ensure_db_ready() -> None:
    global DB_MIGRATIONS_READY, DB_MIGRATION_ERROR

    if DB_MIGRATIONS_READY:
        return

    with DB_MIGRATIONS_LOCK:
        if DB_MIGRATIONS_READY:
            return

        try:
            run_db_migrations()
        except BackendApiError as exc:
            DB_MIGRATION_ERROR = exc.payload.get("message")
            raise
        except psycopg.Error as exc:
            DB_MIGRATION_ERROR = str(exc)
            APP.logger.error("Database migration failed: %s", exc)
            raise BackendApiError(
                {
                    "error": "database_migration_failed",
                    "message": "PostgreSQL migration failed",
                    "details": str(exc),
                },
                503,
            ) from exc

        DB_MIGRATIONS_READY = True
        DB_MIGRATION_ERROR = None


def database_error_response(exc: BackendApiError) -> tuple[Any, int]:
    return jsonify(exc.payload), exc.status_code


def require_db() -> None:
    ensure_db_ready()


def parse_optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_score_value(value: str) -> int | float | str:
    try:
        parsed_float = float(value)
    except ValueError:
        return value

    parsed_int = int(parsed_float)
    return parsed_int if parsed_float == parsed_int else parsed_float


def clean_score_label(label: str) -> str:
    return label.replace("!", "").replace("<", "").strip()


def load_kubernetes_config() -> None:
    try:
        config.load_incluster_config()
    except ConfigException:
        config.load_kube_config()


load_kubernetes_config()
custom_objects_api = client.CustomObjectsApi()
if db_configured():
    try:
        ensure_db_ready()
    except BackendApiError:
        APP.logger.warning("PostgreSQL is configured but not ready; tournament endpoints will retry migrations on demand", exc_info=True)


def build_allocation_manifest(server_pool: dict[str, Any] | None = None) -> dict:
    pool = server_pool or default_server_pool()
    return {
        "apiVersion": f"{ALLOCATION_GROUP}/{ALLOCATION_VERSION}",
        "kind": "GameServerAllocation",
        "metadata": {
            "generateName": "xonotic-allocation-",
            "namespace": pool["agones_namespace"],
        },
        "spec": {
            "scheduling": "Packed",
            "selectors": [
                {
                    "matchLabels": {
                        "agones.dev/fleet": pool["fleet_name"],
                        "game": GAME_LABEL,
                    }
                }
            ],
        },
    }


def log_kubernetes_api_error(
    *,
    operation: str,
    resource_type: str,
    namespace: str,
    name: str | None,
    request_context: dict[str, Any] | None,
    exc: ApiException,
) -> None:
    APP.logger.error(
        (
            "Kubernetes API error during %s for %s in namespace=%s name=%s "
            "status=%s reason=%s body=%s request_context=%s"
        ),
        operation,
        resource_type,
        namespace,
        name,
        exc.status,
        exc.reason,
        exc.body,
        request_context,
    )


def kubernetes_api_error_payload(
    *,
    resource_type: str,
    namespace: str,
    name: str | None,
    request_context: dict[str, Any] | None,
    exc: ApiException,
    allocation_request_name: str | None = None,
) -> dict[str, Any]:
    response = {
        "error": "kubernetes_api_error",
        "message": exc.reason,
        "status": exc.status,
        "resource_type": resource_type,
        "namespace": namespace,
        "object_name": name,
        "request_context": request_context,
    }
    if allocation_request_name:
        response["allocation_request_name"] = allocation_request_name
    return response


def kubernetes_api_error_response(
    *,
    operation: str,
    resource_type: str,
    namespace: str,
    name: str | None,
    request_context: dict[str, Any] | None,
    exc: ApiException,
    allocation_request_name: str | None = None,
) -> tuple[Any, int]:
    log_kubernetes_api_error(
        operation=operation,
        resource_type=resource_type,
        namespace=namespace,
        name=name,
        request_context=request_context,
        exc=exc,
    )
    return jsonify(
        kubernetes_api_error_payload(
            resource_type=resource_type,
            namespace=namespace,
            name=name,
            request_context=request_context,
            exc=exc,
            allocation_request_name=allocation_request_name,
        )
    ), 502


def raise_kubernetes_api_error(
    *,
    operation: str,
    resource_type: str,
    namespace: str,
    name: str | None,
    request_context: dict[str, Any] | None,
    exc: ApiException,
    allocation_request_name: str | None = None,
) -> None:
    log_kubernetes_api_error(
        operation=operation,
        resource_type=resource_type,
        namespace=namespace,
        name=name,
        request_context=request_context,
        exc=exc,
    )
    raise BackendApiError(
        kubernetes_api_error_payload(
            resource_type=resource_type,
            namespace=namespace,
            name=name,
            request_context=request_context,
            exc=exc,
            allocation_request_name=allocation_request_name,
        ),
        502,
    )


def read_fleet_status(server_pool: dict[str, Any] | None = None) -> dict[str, Any]:
    pool = server_pool or default_server_pool()
    fleet = custom_objects_api.get_namespaced_custom_object(
        group="agones.dev",
        version="v1",
        namespace=pool["agones_namespace"],
        plural="fleets",
        name=pool["fleet_name"],
    )
    status = extract_fleet_status(fleet)
    status["server_pool"] = server_pool_response(pool)
    return status


def server_pool_capacity_response(pool: dict[str, Any]) -> dict[str, Any]:
    pool_response = server_pool_response(pool)
    capacity = {
        "server_pool_id": pool_response["id"],
        "display_name": pool_response["display_name"],
        "region": pool_response["region"],
        "provider": pool_response["provider"],
        "gcp_region": pool_response["gcp_region"],
        "cluster_name": pool_response["cluster_name"],
        "agones_namespace": pool_response["agones_namespace"],
        "fleet_name": pool_response["fleet_name"],
        "provisioned": pool_response["provisioned"],
        "enabled": pool_response["enabled"],
        "desired_replicas": None,
        "current_replicas": None,
        "ready_replicas": None,
        "allocated_replicas": None,
        "reserved_replicas": None,
        "status": "unavailable",
        "capacity_state": "unavailable",
        "warning_message": "Server pool capacity could not be queried.",
        "operator_action": "Check backend Kubernetes access and Agones Fleet health for this pool.",
        "error": None,
    }

    if not pool.get("provisioned", True):
        capacity["status"] = "not-provisioned"
        capacity["capacity_state"] = "not-provisioned"
        capacity["warning_message"] = "This server pool is planned but not provisioned in this dev deployment."
        capacity["operator_action"] = "Create regional infrastructure before enabling this pool."
        capacity["error"] = {
            "error": "server_pool_not_provisioned",
            "message": f"Server pool {pool['display_name']} is planned but not provisioned in this dev deployment.",
        }
        return capacity

    try:
        fleet = read_fleet_status(pool)
    except ApiException as exc:
        log_kubernetes_api_error(
            operation="get",
            resource_type=FLEET_RESOURCE_KIND,
            namespace=pool["agones_namespace"],
            name=pool["fleet_name"],
            request_context={"server_pool": pool_response},
            exc=exc,
        )
        capacity["error"] = kubernetes_api_error_payload(
            resource_type=FLEET_RESOURCE_KIND,
            namespace=pool["agones_namespace"],
            name=pool["fleet_name"],
            request_context={"server_pool": pool_response},
            exc=exc,
        )
        return capacity
    except Exception as exc:
        APP.logger.warning(
            "Server pool capacity read failed pool_id=%s namespace=%s fleet=%s error=%s",
            pool["id"],
            pool["agones_namespace"],
            pool["fleet_name"],
            exc,
        )
        capacity["error"] = {
            "error": "fleet_status_read_failed",
            "message": str(exc),
        }
        return capacity

    ready_replicas = int(fleet.get("ready_replicas") or 0)
    capacity_state = "available" if ready_replicas > 0 else "no-ready-capacity"
    capacity.update(
        {
            "desired_replicas": fleet.get("desired_replicas"),
            "current_replicas": fleet.get("current_replicas", fleet.get("replicas")),
            "ready_replicas": ready_replicas,
            "allocated_replicas": fleet.get("allocated_replicas"),
            "reserved_replicas": fleet.get("reserved_replicas"),
            "status": capacity_state,
            "capacity_state": capacity_state,
            "warning_message": None if ready_replicas > 0 else "No Ready GameServers are available in this pool.",
            "operator_action": (
                "Ready to allocate match servers."
                if ready_replicas > 0
                else "Wait for FleetAutoscaler, release an active match server, or increase pool capacity."
            ),
        }
    )
    return capacity


def ensure_ready_gameserver_capacity(server_pool: dict[str, Any] | None = None) -> dict[str, Any]:
    pool = server_pool or default_server_pool()
    try:
        fleet = read_fleet_status(pool)
    except ApiException as exc:
        raise_kubernetes_api_error(
            operation="get",
            resource_type=FLEET_RESOURCE_KIND,
            namespace=pool["agones_namespace"],
            name=pool["fleet_name"],
            request_context={"server_pool": server_pool_response(pool)},
            exc=exc,
        )
    except Exception as exc:
        raise BackendApiError({"error": "fleet_status_read_failed", "message": str(exc)}, 500) from exc

    if int(fleet.get("ready_replicas") or 0) <= 0:
        raise BackendApiError(
            {
                "error": "no_ready_servers",
                "message": "no Ready Xonotic servers are available yet; wait for FleetAutoscaler to replenish capacity",
                "fleet": fleet,
                "server_pool": server_pool_response(pool),
            },
            409,
        )

    return fleet


def extract_allocation_response(allocation: dict) -> dict:
    metadata = allocation.get("metadata", {})
    status = allocation.get("status", {})
    ports = status.get("ports") or []
    allocated_game_server_name = status.get("gameServerName")
    allocation_request_name = metadata.get("name")

    if status.get("state") != "Allocated":
        raise ValueError(f"allocation state is {status.get('state', 'unknown')}")

    if not status.get("address"):
        raise ValueError("allocated address is missing")

    if not ports or ports[0].get("port") is None:
        raise ValueError("allocated port is missing")

    if not allocated_game_server_name:
        raise ValueError("allocated GameServer name is missing")

    if allocation_request_name == allocated_game_server_name:
        allocation_request_name = None

    return {
        "allocation_request_name": allocation_request_name,
        "allocated_game_server_name": allocated_game_server_name,
        "address": status["address"],
        "port": ports[0]["port"],
    }


def extract_fleet_status(fleet: dict) -> dict:
    metadata = fleet.get("metadata", {})
    spec = fleet.get("spec", {})
    status = fleet.get("status", {})
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "desired_replicas": spec.get("replicas", 0),
        "replicas": status.get("replicas", 0),
        "current_replicas": status.get("replicas", 0),
        "ready_replicas": status.get("readyReplicas", 0),
        "allocated_replicas": status.get("allocatedReplicas", 0),
        "reserved_replicas": status.get("reservedReplicas", 0),
    }


def extract_gameserver_summary(gameserver: dict) -> dict:
    metadata = gameserver.get("metadata", {})
    status = gameserver.get("status", {})
    ports = status.get("ports") or []
    return {
        "name": metadata.get("name"),
        "state": status.get("state"),
        "address": status.get("address"),
        "port": ports[0].get("port") if ports else None,
        "node_name": status.get("nodeName"),
    }


def parse_info_string(info_string: str) -> dict[str, str]:
    parts = info_string.strip().split("\\")
    info: dict[str, str] = {}

    for index in range(1, len(parts) - 1, 2):
        key = parts[index]
        value = parts[index + 1]
        if key:
            info[key] = value

    return info


def parse_qcstatus(qcstatus: str | None) -> dict[str, Any]:
    if not qcstatus:
        return {"game_mode": None, "player_score_labels": [], "team_score_labels": [], "teams": []}

    metadata, _, scores = qcstatus.partition("::")
    metadata_parts = metadata.split(":")
    score_parts = scores.split(":") if scores else []
    player_score_labels = []
    team_score_labels = []
    teams = []

    if score_parts:
        player_score_labels = [clean_score_label(label) for label in score_parts[0].split(",") if clean_score_label(label)]

    if len(score_parts) > 1:
        team_score_labels = [clean_score_label(label) for label in score_parts[1].split(",") if clean_score_label(label)]

    for index in range(2, len(score_parts) - 1, 2):
        team_id = parse_optional_int(score_parts[index])
        score_values = score_parts[index + 1].split(",") if score_parts[index + 1] else []
        team_scores = {
            label: parse_score_value(score_values[label_index])
            for label_index, label in enumerate(team_score_labels)
            if label_index < len(score_values)
        }
        teams.append(
            {
                "team": team_id,
                "score_raw": score_parts[index + 1],
                "scores": team_scores,
            }
        )

    return {
        "game_mode": metadata_parts[0] if metadata_parts else None,
        "player_score_labels": player_score_labels,
        "team_score_labels": team_score_labels,
        "teams": teams,
    }


def parse_player_status_line(line: str, score_labels: list[str]) -> dict[str, Any] | None:
    match = PLAYER_STATUS_PATTERN.match(line.strip())
    if not match:
        return None

    score_raw = match.group("score")
    score_values = score_raw.split(",")
    scores = {
        label: parse_score_value(score_values[index])
        for index, label in enumerate(score_labels)
        if index < len(score_values)
    }

    return {
        "name": match.group("name"),
        "ping": parse_optional_int(match.group("ping")),
        "team": parse_optional_int(match.group("team")),
        "score": parse_score_value(score_values[0]) if score_values and score_values[0] else None,
        "score_raw": score_raw,
        "scores": scores,
    }


def parse_status_response(raw_response: bytes) -> dict[str, Any]:
    if raw_response.startswith(b"\xff\xff\xff\xff"):
        raw_response = raw_response[4:]

    response = raw_response.decode("utf-8", errors="replace")

    header, _, body = response.partition("\n")
    if header != "statusResponse":
        raise ValueError(f"unexpected status response header: {header}")

    info_line, _, player_blob = body.partition("\n")
    info = parse_info_string(info_line)
    qcstatus = parse_qcstatus(info.get("qcstatus"))
    players = []

    for line in player_blob.splitlines():
        if not line.strip():
            continue
        player = parse_player_status_line(line, qcstatus["player_score_labels"])
        if player:
            players.append(player)

    return {
        "ok": True,
        "source": "getstatus",
        "queried_at": utc_now(),
        "hostname": info.get("hostname"),
        "map": info.get("mapname"),
        "game_mode": qcstatus["game_mode"] or info.get("gamename"),
        "current_players": parse_optional_int(info.get("clients")),
        "bots": parse_optional_int(info.get("bots")),
        "max_players": parse_optional_int(info.get("sv_maxclients")),
        "players": players,
        "player_score_labels": qcstatus["player_score_labels"],
        "team_score_labels": qcstatus["team_score_labels"],
        "teams": qcstatus["teams"],
    }


def query_xonotic_status(address: str, port: int) -> dict[str, Any]:
    message = b"\xff\xff\xff\xffgetstatus xonotic-admin\n"

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(XONOTIC_STATUS_TIMEOUT_SECONDS)
        sock.sendto(message, (address, port))
        response, _ = sock.recvfrom(8192)

    return parse_status_response(response)


def live_status_error_payload(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "source": "getstatus",
        "queried_at": utc_now(),
        "error": "status_query_failed",
        "message": message,
    }


def get_cached_xonotic_status(address: str | None, port: int | None) -> dict[str, Any] | None:
    if not address or not port:
        return None

    cache_key = f"{address}:{port}"
    now = time.monotonic()

    with STATUS_CACHE_LOCK:
        cached = STATUS_CACHE.get(cache_key)
        if cached and cached["expires_at"] > now:
            return cached["status"]

    try:
        status = query_xonotic_status(address, port)
    except Exception as exc:
        APP.logger.info("Xonotic getstatus query failed for %s: %s", cache_key, exc)
        status = live_status_error_payload(str(exc))

    if status.get("ok"):
        with STATUS_CACHE_LOCK:
            STATUS_CACHE[cache_key] = {
                "expires_at": now + XONOTIC_STATUS_CACHE_SECONDS,
                "status": status,
            }

    return status


def clear_status_cache(address: str | None, port: int | None) -> None:
    if not address or not port:
        return

    with STATUS_CACHE_LOCK:
        STATUS_CACHE.pop(f"{address}:{port}", None)


def apply_successful_live_status(match: dict[str, Any], live_status: dict[str, Any]) -> None:
    match["live_status"] = live_status
    match["last_status_error"] = None
    match["last_status_error_at"] = None
    match["current_players"] = live_status.get("current_players")
    match["live_max_players"] = live_status.get("max_players")
    match["game_mode"] = live_status.get("game_mode") or match.get("game_mode")
    match["map"] = live_status.get("map") or match.get("map")


def apply_live_status_error(match: dict[str, Any], error_status: dict[str, Any]) -> None:
    match["last_status_error"] = error_status
    match["last_status_error_at"] = error_status.get("queried_at") or utc_now()


def update_stored_match_live_status(match_id: str, status: dict[str, Any]) -> dict[str, Any] | None:
    with MATCHES_LOCK:
        stored_match = MATCHES.get(match_id)
        if not stored_match:
            return None

        if status.get("ok"):
            apply_successful_live_status(stored_match, status)
        else:
            apply_live_status_error(stored_match, status)

        return stored_match.copy()


def query_live_status_with_preservation(match: dict[str, Any]) -> dict[str, Any] | None:
    allocated_server = match.get("allocated_server")
    if not allocated_server:
        return None

    status = get_cached_xonotic_status(allocated_server.get("address"), allocated_server.get("port"))
    if not status:
        return match.get("live_status")

    if status.get("ok"):
        apply_successful_live_status(match, status)
    else:
        apply_live_status_error(match, status)

    stored_snapshot = update_stored_match_live_status(match["match_id"], status)
    if stored_snapshot:
        match.update(stored_snapshot)

    return match.get("live_status") or status


def md4_digest(message: bytes) -> bytes:
    def left_rotate(value: int, shift: int) -> int:
        value &= 0xFFFFFFFF
        return ((value << shift) | (value >> (32 - shift))) & 0xFFFFFFFF

    def f(x: int, y: int, z: int) -> int:
        return ((x & y) | (~x & z)) & 0xFFFFFFFF

    def g(x: int, y: int, z: int) -> int:
        return ((x & y) | (x & z) | (y & z)) & 0xFFFFFFFF

    def h(x: int, y: int, z: int) -> int:
        return (x ^ y ^ z) & 0xFFFFFFFF

    original_bit_length = (8 * len(message)) & 0xFFFFFFFFFFFFFFFF
    message += b"\x80"
    while len(message) % 64 != 56:
        message += b"\x00"
    message += struct.pack("<Q", original_bit_length)

    a = 0x67452301
    b = 0xEFCDAB89
    c = 0x98BADCFE
    d = 0x10325476

    for offset in range(0, len(message), 64):
        x = list(struct.unpack("<16I", message[offset : offset + 64]))
        aa, bb, cc, dd = a, b, c, d

        for index in range(0, 16, 4):
            a = left_rotate(a + f(b, c, d) + x[index], 3)
            d = left_rotate(d + f(a, b, c) + x[index + 1], 7)
            c = left_rotate(c + f(d, a, b) + x[index + 2], 11)
            b = left_rotate(b + f(c, d, a) + x[index + 3], 19)

        for index in (0, 1, 2, 3):
            a = left_rotate(a + g(b, c, d) + x[index] + 0x5A827999, 3)
            d = left_rotate(d + g(a, b, c) + x[index + 4] + 0x5A827999, 5)
            c = left_rotate(c + g(d, a, b) + x[index + 8] + 0x5A827999, 9)
            b = left_rotate(b + g(c, d, a) + x[index + 12] + 0x5A827999, 13)

        for index in (0, 2, 1, 3):
            a = left_rotate(a + h(b, c, d) + x[index] + 0x6ED9EBA1, 3)
            d = left_rotate(d + h(a, b, c) + x[index + 8] + 0x6ED9EBA1, 9)
            c = left_rotate(c + h(d, a, b) + x[index + 4] + 0x6ED9EBA1, 11)
            b = left_rotate(b + h(c, d, a) + x[index + 12] + 0x6ED9EBA1, 15)

        a = (a + aa) & 0xFFFFFFFF
        b = (b + bb) & 0xFFFFFFFF
        c = (c + cc) & 0xFFFFFFFF
        d = (d + dd) & 0xFFFFFFFF

    return struct.pack("<4I", a, b, c, d)


def hmac_md4(key: bytes, message: bytes) -> bytes:
    block_size = 64
    if len(key) > block_size:
        key = md4_digest(key)
    key = key.ljust(block_size, b"\x00")
    outer_key_pad = bytes(byte ^ 0x5C for byte in key)
    inner_key_pad = bytes(byte ^ 0x36 for byte in key)
    return md4_digest(outer_key_pad + md4_digest(inner_key_pad + message))


def sanitize_rcon_output(raw_response: bytes, password: str) -> str:
    if raw_response.startswith(RCON_PACKET_PREFIX):
        raw_response = raw_response[4:]

    output = raw_response.decode("utf-8", errors="replace")
    output = output.replace(password, "[redacted]")
    output = "".join(character for character in output if character == "\n" or character == "\t" or ord(character) >= 32)
    output = output.strip()

    if output.startswith("print\n"):
        output = output.removeprefix("print\n").strip()
    elif output.startswith("n"):
        output = output[1:].strip()

    if len(output) > XONOTIC_RCON_OUTPUT_LIMIT:
        return f"{output[:XONOTIC_RCON_OUTPUT_LIMIT]}\n...[truncated]"

    return output


def build_rcon_packet(command: str, protocol: str, challenge: str | None = None) -> bytes:
    password = XONOTIC_RCON_PASSWORD.encode("utf-8")
    command_bytes = command.encode("utf-8")

    if protocol == "secure-time":
        timestamp = f"{int(time.time())}.{uuid.uuid4().int % 1000000:06d}".encode("ascii")
        signed_payload = timestamp + b" " + command_bytes
        digest = hmac_md4(password, signed_payload)
        return RCON_PACKET_PREFIX + b"srcon HMAC-MD4 TIME " + digest + b" " + signed_payload

    if protocol == "secure-challenge":
        if not challenge:
            raise ValueError("secure-challenge requires a challenge")
        signed_payload = challenge.encode("ascii") + b" " + command_bytes
        digest = hmac_md4(password, signed_payload)
        return RCON_PACKET_PREFIX + b"srcon HMAC-MD4 CHALLENGE " + digest + b" " + signed_payload

    if protocol == "plaintext":
        return RCON_PACKET_PREFIX + b"rcon " + password + b" " + command_bytes

    raise ValueError(f"unsupported RCON protocol {protocol}")


def receive_rcon_response(sock: socket.socket, address: str, port: int, command_name: str, protocol: str) -> bytes:
    try:
        response, _ = sock.recvfrom(16384)
    except socket.timeout as exc:
        APP.logger.warning("RCON timeout target=%s:%s command=%s protocol=%s", address, port, command_name, protocol)
        raise BackendApiError(
            {
                "error": "rcon_timeout",
                "message": f"timed out waiting for RCON response from {address}:{port}",
                "target": {"address": address, "port": port},
                "command": command_name,
                "protocol": protocol,
            },
            504,
        ) from exc

    return response


def request_rcon_challenge(sock: socket.socket, address: str, port: int, command_name: str) -> str:
    APP.logger.info("RCON requesting challenge target=%s:%s command=%s protocol=secure-challenge", address, port, command_name)
    sock.sendto(RCON_PACKET_PREFIX + b"getchallenge", (address, port))
    APP.logger.info("RCON challenge request sent target=%s:%s command=%s protocol=secure-challenge", address, port, command_name)
    response = receive_rcon_response(sock, address, port, command_name, "secure-challenge")

    if response.startswith(RCON_PACKET_PREFIX):
        response = response[4:]

    decoded = response.decode("ascii", errors="replace").replace("\x00", "").strip()
    if not decoded.startswith("challenge "):
        raise BackendApiError(
            {
                "error": "rcon_invalid_challenge",
                "message": f"unexpected RCON challenge response: {decoded[:80]}",
                "target": {"address": address, "port": port},
                "command": command_name,
                "protocol": "secure-challenge",
            },
            502,
        )

    challenge = decoded.removeprefix("challenge ").split()[0]
    APP.logger.info("RCON challenge received target=%s:%s command=%s protocol=secure-challenge", address, port, command_name)
    return challenge


def _send_rcon_command(
    address: str,
    port: int,
    command: str,
    *,
    expect_response: bool,
    preferred_protocol: str | None = None,
) -> dict[str, Any]:
    if not XONOTIC_RCON_PASSWORD:
        raise BackendApiError(
            {
                "error": "rcon_not_configured",
                "message": "XONOTIC_RCON_PASSWORD is not configured for the allocator backend",
            },
            503,
        )

    command_name = command.split(" ", 1)[0]
    protocols = (preferred_protocol,) if preferred_protocol else XONOTIC_RCON_PROTOCOLS
    timeout_errors: list[dict[str, Any]] = []

    for protocol in protocols:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(XONOTIC_RCON_TIMEOUT_SECONDS)
                challenge = request_rcon_challenge(sock, address, port, command_name) if protocol == "secure-challenge" else None
                packet = build_rcon_packet(command, protocol, challenge)

                APP.logger.info("RCON sending target=%s:%s command=%s protocol=%s", address, port, command_name, protocol)
                sock.sendto(packet, (address, port))
                APP.logger.info("RCON packet sent target=%s:%s command=%s protocol=%s bytes=%s", address, port, command_name, protocol, len(packet))

                if not expect_response:
                    return {
                        "ok": True,
                        "command": command_name,
                        "protocol": protocol,
                        "response_expected": False,
                        "sent": True,
                        "output": None,
                    }

                response = receive_rcon_response(sock, address, port, command_name, protocol)
        except BackendApiError as exc:
            if exc.payload.get("error") == "rcon_timeout":
                timeout_errors.append(exc.payload)
                continue
            raise
        except OSError as exc:
            APP.logger.warning(
                "RCON network error target=%s:%s command=%s protocol=%s error=%s",
                address,
                port,
                command_name,
                protocol,
                exc,
            )
            raise BackendApiError(
                {
                    "error": "rcon_network_error",
                    "message": str(exc),
                    "target": {"address": address, "port": port},
                    "command": command_name,
                    "protocol": protocol,
                },
                502,
            ) from exc

        sanitized_output = sanitize_rcon_output(response, XONOTIC_RCON_PASSWORD)
        APP.logger.info("RCON response received target=%s:%s command=%s protocol=%s bytes=%s", address, port, command_name, protocol, len(response))
        return {
            "ok": True,
            "command": command_name,
            "protocol": protocol,
            "response_expected": True,
            "bytes": len(response),
            "output": sanitized_output,
        }

    if timeout_errors:
        raise BackendApiError(
            {
                "error": "rcon_timeout",
                "message": f"timed out waiting for RCON response from {address}:{port}",
                "target": {"address": address, "port": port},
                "command": command_name,
                "protocols_attempted": list(protocols),
                "attempts": timeout_errors,
            },
            504,
        )

    raise BackendApiError(
        {
            "error": "rcon_protocol_error",
            "message": "no RCON protocol attempts were configured",
            "target": {"address": address, "port": port},
            "command": command_name,
        },
        500,
    )


def send_rcon_command(
    address: str,
    port: int,
    command: str,
    *,
    expect_response: bool,
    preferred_protocol: str | None = None,
) -> dict[str, Any]:
    command_name = command.split(" ", 1)[0] if command else "unknown"
    RCON_COMMAND_ATTEMPTS.labels(command_name).inc()

    try:
        return _send_rcon_command(
            address,
            port,
            command,
            expect_response=expect_response,
            preferred_protocol=preferred_protocol,
        )
    except BackendApiError as exc:
        RCON_COMMAND_FAILURES.labels(command_name, exc.payload.get("error", "backend_api_error")).inc()
        raise


def run_rcon_smoke_test(allocated_server: dict[str, Any], *, include_say: bool) -> dict[str, Any]:
    address = allocated_server.get("address")
    port = allocated_server.get("port")
    if not address or not port:
        raise BackendApiError({"error": "missing_allocated_endpoint", "message": "allocated server address or port is missing"}, 409)

    target = {
        "address": address,
        "port": port,
        "allocated_game_server_name": allocated_server.get("allocated_game_server_name"),
    }
    status_result = send_rcon_command(address, int(port), "status", expect_response=True)
    commands = [status_result]

    if include_say:
        commands.append(
            send_rcon_command(
                address,
                int(port),
                'say "RCON smoke test"',
                expect_response=False,
                preferred_protocol=status_result["protocol"],
            )
        )

    return {
        "ok": True,
        "target": target,
        "commands": commands,
        "live_status": get_cached_xonotic_status(address, int(port)),
    }


def validate_admin_broadcast_message(value: Any) -> str:
    if not isinstance(value, str):
        raise BackendApiError({"error": "invalid_message", "message": "message is required and must be a string"}, 400)

    message = value.strip()
    if not message:
        raise BackendApiError({"error": "invalid_message", "message": "message cannot be empty"}, 400)

    if len(message) > ADMIN_BROADCAST_MAX_LENGTH:
        raise BackendApiError(
            {
                "error": "invalid_message",
                "message": f"message must be {ADMIN_BROADCAST_MAX_LENGTH} characters or fewer",
            },
            400,
        )

    if any(ord(character) < 32 or ord(character) == 127 for character in message) or ";" in message:
        raise BackendApiError(
            {
                "error": "invalid_message",
                "message": "message cannot contain control characters, newlines, or command separators",
            },
            400,
        )

    return message


def validate_admin_map(value: Any) -> str:
    if not isinstance(value, str):
        raise BackendApiError({"error": "invalid_map", "message": "map is required and must be a string"}, 400)

    map_name = value.strip().lower()
    if map_name not in ADMIN_ALLOWED_MAPS:
        raise BackendApiError(
            {
                "error": "invalid_map",
                "message": f"map must be one of: {', '.join(ADMIN_ALLOWED_MAPS)}",
                "allowed_maps": list(ADMIN_ALLOWED_MAPS),
            },
            400,
        )

    return map_name


def validate_requested_map(value: Any) -> str:
    if value is None or value == "":
        value = DEFAULT_REQUESTED_MAP
    return validate_admin_map(value)


def validate_requested_game_mode(value: Any, *, require_selectable: bool = True) -> str:
    if value is None or value == "":
        value = DEFAULT_REQUESTED_GAME_MODE

    if not isinstance(value, str):
        raise BackendApiError({"error": "invalid_game_mode", "message": "requested_game_mode must be a string"}, 400)

    game_mode = value.strip().lower()
    mode_config = GAME_CONFIG_OPTIONS.get(game_mode)
    if not mode_config:
        raise BackendApiError(
            {
                "error": "invalid_game_mode",
                "message": f"requested_game_mode must be one of: {', '.join(ADMIN_ALLOWED_GAME_MODES)}",
                "allowed_game_modes": list(ADMIN_ALLOWED_GAME_MODES),
            },
            400,
        )

    if require_selectable and not mode_config.get("selectable"):
        raise BackendApiError(
            {
                "error": "unsupported_game_mode",
                "message": f"{game_mode} is not available for normal match allocation yet",
                "requested_game_mode": game_mode,
                "supported_modes": supported_game_modes(),
                "valid_maps": verified_maps_for_mode(game_mode),
                "experimental_maps": list(mode_config.get("experimental_maps", ())),
                "disabled_reason": mode_config.get("disabled_reason"),
            },
            400,
        )

    return game_mode


def supported_game_modes() -> list[str]:
    return [mode for mode, config in GAME_CONFIG_OPTIONS.items() if config.get("selectable")]


def verified_maps_for_mode(game_mode: str) -> list[str]:
    return list(GAME_CONFIG_OPTIONS.get(game_mode, {}).get("verified_maps", ()))


def experimental_maps_for_mode(game_mode: str) -> list[str]:
    return list(GAME_CONFIG_OPTIONS.get(game_mode, {}).get("experimental_maps", ()))


def candidate_maps_for_mode(game_mode: str, *, allow_experimental: bool = False) -> list[str]:
    maps = verified_maps_for_mode(game_mode)
    if allow_experimental:
        maps = [*maps, *experimental_maps_for_mode(game_mode)]
    return maps


def default_verified_game_config() -> tuple[str, str]:
    configured_mode = DEFAULT_REQUESTED_GAME_MODE.strip().lower()
    configured_map = DEFAULT_REQUESTED_MAP.strip().lower()
    mode_config = GAME_CONFIG_OPTIONS.get(configured_mode)
    if mode_config and mode_config.get("selectable") and configured_map in mode_config["verified_maps"]:
        return configured_mode, configured_map

    for mode, config in GAME_CONFIG_OPTIONS.items():
        if config.get("selectable") and config["verified_maps"]:
            return mode, config["verified_maps"][0]

    raise RuntimeError("no verified game configuration is available")


def experimental_game_config_allowed(body: dict[str, Any]) -> bool:
    requested = validate_optional_bool(
        body.get("allow_experimental_game_config", body.get("verification_probe")),
        "allow_experimental_game_config",
    )
    if requested and not XONOTIC_ENABLE_EXPERIMENTAL_GAME_CONFIG:
        raise BackendApiError(
            {
                "error": "experimental_game_config_disabled",
                "message": "experimental map/mode probes require XONOTIC_ENABLE_EXPERIMENTAL_GAME_CONFIG=1",
            },
            403,
        )
    return requested


def validate_requested_game_config(map_value: Any, mode_value: Any, *, allow_experimental: bool = False) -> tuple[str, str]:
    default_mode, default_map = default_verified_game_config()
    game_mode = validate_requested_game_mode(mode_value if mode_value not in (None, "") else default_mode, require_selectable=not allow_experimental)
    map_name = validate_requested_map(map_value if map_value not in (None, "") else default_map)
    valid_maps = candidate_maps_for_mode(game_mode, allow_experimental=allow_experimental)

    if map_name not in valid_maps:
        raise BackendApiError(
            {
                "error": "invalid_map_mode_combination",
                "message": f"{map_name} is not a {'known candidate' if allow_experimental else 'verified map'} for {game_mode}",
                "requested_map": map_name,
                "requested_game_mode": game_mode,
                "valid_maps": valid_maps,
                "verified_maps": verified_maps_for_mode(game_mode),
                "experimental_maps": experimental_maps_for_mode(game_mode),
                "supported_modes": supported_game_modes(),
            },
            400,
        )

    return map_name, game_mode


def game_config_options_response() -> dict[str, Any]:
    default_mode, default_map = default_verified_game_config()
    modes = []
    valid_maps_by_mode = {}

    for mode, config in GAME_CONFIG_OPTIONS.items():
        maps = list(config["verified_maps"])
        if config.get("selectable"):
            valid_maps_by_mode[mode] = maps
        modes.append(
            {
                "mode": mode,
                "label": config["label"],
                "selectable": bool(config.get("selectable")),
                "verified_maps": maps,
                "experimental_maps": list(config.get("experimental_maps", ())),
                "disabled_reason": config.get("disabled_reason"),
            }
        )

    return {
        "default": {
            "requested_game_mode": default_mode,
            "requested_map": default_map,
        },
        "supported_modes": supported_game_modes(),
        "valid_maps_by_mode": valid_maps_by_mode,
        "modes": modes,
        "experimental_probe_enabled": XONOTIC_ENABLE_EXPERIMENTAL_GAME_CONFIG,
        "note": "Only verified map/mode combinations are selectable by default.",
    }


def normalize_game_mode(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    normalized = re.sub(r"[\s_-]+", " ", value.strip().lower()).strip()
    if not normalized:
        return None

    without_g_prefix = normalized[2:] if normalized.startswith("g ") else normalized
    return GAME_MODE_ALIASES.get(normalized) or GAME_MODE_ALIASES.get(without_g_prefix) or without_g_prefix


def rcon_quote_argument(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def allocated_match_snapshot(match_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    with MATCHES_LOCK:
        match = MATCHES.get(match_id)
        if not match:
            raise BackendApiError({"error": "match_not_found", "message": f"match {match_id} was not found"}, 404)
        if match["status"] in FINISHED_MATCH_STATUSES:
            raise BackendApiError({"error": "match_finished", "message": f"match {match_id} has already been released"}, 409)

        allocated_server = match.get("allocated_server")
        if not allocated_server:
            raise BackendApiError({"error": "match_not_allocated", "message": f"match {match_id} does not have an allocated server"}, 409)

        return match.copy(), allocated_server.copy()


def run_admin_broadcast(match_id: str, message: str) -> dict[str, Any]:
    match_snapshot, allocated_server = allocated_match_snapshot(match_id)
    address = allocated_server.get("address")
    port = allocated_server.get("port")
    if not address or not port:
        raise BackendApiError({"error": "missing_allocated_endpoint", "message": "allocated server address or port is missing"}, 409)

    rcon_result = send_rcon_command(
        address,
        int(port),
        f"say {rcon_quote_argument(message)}",
        expect_response=False,
    )

    return {
        "ok": True,
        "action": "broadcast",
        "message": message,
        "match": match_response(match_snapshot),
        "target": {
            "address": address,
            "port": port,
            "allocated_game_server_name": allocated_server.get("allocated_game_server_name"),
        },
        "rcon": rcon_result,
    }


def active_tournament_assignment_snapshot(tournament_id: str, match_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    require_db()
    with db_connect() as conn:
        with conn.cursor() as cur:
            fetch_tournament(cur, tournament_id)
            match = fetch_tournament_match(cur, tournament_id, match_id)
            active_assignment = fetch_active_server_assignment(cur, tournament_id, match_id)
            if not active_assignment:
                raise BackendApiError(
                    {
                        "error": "server_assignment_not_found",
                        "message": f"match {match_id} does not have an active server assignment",
                    },
                    404,
                )

    return match, active_assignment


def tournament_assignment_target(assignment: dict[str, Any]) -> tuple[str, int, dict[str, Any]]:
    address = assignment.get("address")
    port = assignment.get("port")
    if not address or not port:
        raise BackendApiError({"error": "missing_allocated_endpoint", "message": "active server assignment address or port is missing"}, 409)

    return address, int(port), {
        "address": address,
        "port": int(port),
        "allocated_game_server_name": assignment.get("allocated_game_server_name"),
        "assignment_id": str(assignment.get("id")) if assignment.get("id") else None,
    }


def run_tournament_admin_broadcast(tournament_id: str, match_id: str, message: str) -> dict[str, Any]:
    match, assignment = active_tournament_assignment_snapshot(tournament_id, match_id)
    address, port, target = tournament_assignment_target(assignment)
    rcon_result = send_rcon_command(
        address,
        port,
        f"say {rcon_quote_argument(message)}",
        expect_response=False,
    )

    response_match = tournament_match_response(match)
    response_match["active_server_assignment"] = assignment_response(assignment, include_live_status=False)
    return {
        "ok": True,
        "action": "broadcast",
        "message": message,
        "match": response_match,
        "assignment": response_match["active_server_assignment"],
        "target": target,
        "rcon": rcon_result,
    }


def verify_live_config_status(
    address: str,
    port: int,
    *,
    expected_map: str | None,
    expected_game_mode: str | None = None,
) -> dict[str, Any]:
    time.sleep(XONOTIC_RCON_CHANGE_MAP_STATUS_DELAY_SECONDS)
    deadline = time.monotonic() + XONOTIC_RCON_CHANGE_MAP_VERIFY_TIMEOUT_SECONDS
    last_error = None
    last_live_status = None

    while time.monotonic() <= deadline:
        try:
            live_status = query_xonotic_status(address, port)
        except Exception as exc:
            APP.logger.info("Xonotic getstatus verification failed for %s:%s after changelevel: %s", address, port, exc)
            last_error = live_status_error_payload(str(exc))
        else:
            last_live_status = live_status
            with STATUS_CACHE_LOCK:
                STATUS_CACHE[f"{address}:{port}"] = {
                    "expires_at": time.monotonic() + XONOTIC_STATUS_CACHE_SECONDS,
                    "status": live_status,
                }

            actual_map = live_status.get("map")
            actual_game_mode = normalize_game_mode(live_status.get("game_mode"))
            map_matches = expected_map is None or actual_map == expected_map
            mode_matches = expected_game_mode is None or actual_game_mode == expected_game_mode

            if map_matches and mode_matches:
                return {
                    "ok": True,
                    "verified": True,
                    "expected_map": expected_map,
                    "actual_map": actual_map,
                    "expected_game_mode": expected_game_mode,
                    "actual_game_mode": actual_game_mode,
                    "live_status": live_status,
                    "error": None,
                }

            mismatch_messages = []
            if not map_matches:
                mismatch_messages.append(f"expected map {expected_map}, got {actual_map or 'unknown'}")
            if not mode_matches:
                mismatch_messages.append(f"expected mode {expected_game_mode}, got {actual_game_mode or 'unknown'}")

            last_error = {
                "ok": False,
                "source": "getstatus",
                "queried_at": utc_now(),
                "error": "live_config_verification_failed",
                "message": "; ".join(mismatch_messages),
                "expected_map": expected_map,
                "actual_map": actual_map,
                "expected_game_mode": expected_game_mode,
                "actual_game_mode": actual_game_mode,
            }

        time.sleep(XONOTIC_RCON_CHANGE_MAP_VERIFY_INTERVAL_SECONDS)

    return {
        "ok": False,
        "verified": False,
        "expected_map": expected_map,
        "actual_map": last_live_status.get("map") if last_live_status else None,
        "expected_game_mode": expected_game_mode,
        "actual_game_mode": normalize_game_mode(last_live_status.get("game_mode")) if last_live_status else None,
        "live_status": last_live_status,
        "error": last_error
        or {
            "ok": False,
            "source": "getstatus",
            "queried_at": utc_now(),
            "error": "live_config_verification_failed",
            "message": "live status verification is temporarily unavailable",
            "expected_map": expected_map,
            "actual_map": None,
            "expected_game_mode": expected_game_mode,
            "actual_game_mode": None,
        },
    }


def verify_change_map_status(address: str, port: int, expected_map: str) -> dict[str, Any]:
    verification = verify_live_config_status(address, port, expected_map=expected_map)
    if verification.get("error"):
        verification["error"]["error"] = "change_map_verification_failed"
    return verification


def run_admin_change_map(match_id: str, map_name: str) -> dict[str, Any]:
    match_snapshot, allocated_server = allocated_match_snapshot(match_id)
    address = allocated_server.get("address")
    port = allocated_server.get("port")
    if not address or not port:
        raise BackendApiError({"error": "missing_allocated_endpoint", "message": "allocated server address or port is missing"}, 409)

    rcon_result = send_rcon_command(
        address,
        int(port),
        f"changelevel {map_name}",
        expect_response=False,
    )

    clear_status_cache(address, int(port))
    verification = verify_change_map_status(address, int(port), map_name)
    live_status = verification.get("live_status")

    if live_status and live_status.get("ok"):
        with MATCHES_LOCK:
            match = MATCHES.get(match_id)
            if match:
                apply_successful_live_status(match, live_status)
                match["change_map_verification"] = verification
                if not verification.get("verified"):
                    apply_live_status_error(match, verification["error"])
                match_snapshot = match.copy()
    else:
        with MATCHES_LOCK:
            match = MATCHES.get(match_id)
            if match:
                match["change_map_verification"] = verification
                apply_live_status_error(match, verification["error"])
                match_snapshot = match.copy()

    return {
        "ok": verification.get("verified") is True,
        "action": "change_map",
        "map": map_name,
        "rcon_sent": True,
        "verified": verification.get("verified") is True,
        "error": None if verification.get("verified") else "change_map_verification_failed",
        "message": None
        if verification.get("verified")
        else "Map change command sent, but live status verification is temporarily unavailable.",
        "match": match_response(match_snapshot),
        "target": {
            "address": address,
            "port": port,
            "allocated_game_server_name": allocated_server.get("allocated_game_server_name"),
        },
        "rcon": rcon_result,
        "change_map_verification": verification,
        "live_status": live_status,
    }


def run_tournament_admin_change_map(tournament_id: str, match_id: str, map_name: str) -> dict[str, Any]:
    match, assignment = active_tournament_assignment_snapshot(tournament_id, match_id)
    address, port, target = tournament_assignment_target(assignment)
    rcon_result = send_rcon_command(
        address,
        port,
        f"changelevel {map_name}",
        expect_response=False,
    )

    clear_status_cache(address, port)
    verification = verify_change_map_status(address, port, map_name)
    live_status = verification.get("live_status")

    response_match = tournament_match_response(match)
    response_match["active_server_assignment"] = assignment_response(assignment, include_live_status=False)
    return {
        "ok": verification.get("verified") is True,
        "action": "change_map",
        "map": map_name,
        "rcon_sent": True,
        "verified": verification.get("verified") is True,
        "error": None if verification.get("verified") else "change_map_verification_failed",
        "message": None
        if verification.get("verified")
        else "Map change command sent, but live status verification is temporarily unavailable.",
        "match": response_match,
        "assignment": response_match["active_server_assignment"],
        "target": target,
        "rcon": rcon_result,
        "change_map_verification": verification,
        "live_status": live_status,
    }


def configure_allocated_server(
    allocated_server: dict[str, Any],
    *,
    requested_map: str,
    requested_game_mode: str,
) -> dict[str, Any]:
    address = allocated_server.get("address")
    port = allocated_server.get("port")
    if not address or not port:
        raise BackendApiError({"error": "missing_allocated_endpoint", "message": "allocated server address or port is missing"}, 409)

    commands = []
    mode_result = send_rcon_command(
        address,
        int(port),
        f"gametype {requested_game_mode}",
        expect_response=False,
    )
    commands.append(mode_result)

    map_result = send_rcon_command(
        address,
        int(port),
        f"changelevel {requested_map}",
        expect_response=False,
        preferred_protocol=mode_result.get("protocol"),
    )
    commands.append(map_result)

    clear_status_cache(address, int(port))
    verification = verify_live_config_status(
        address,
        int(port),
        expected_map=requested_map,
        expected_game_mode=requested_game_mode,
    )
    verified = verification.get("verified") is True
    failure_reason = None
    if not verified:
        verification_error = verification.get("error")
        if isinstance(verification_error, dict):
            failure_reason = verification_error.get("message")
        failure_reason = failure_reason or "Allocated server was configured, but live status did not verify the requested map/mode."

    if verified:
        MAP_MODE_VERIFICATION_SUCCESSES.labels(requested_game_mode, requested_map).inc()
    else:
        verification_error = verification.get("error")
        reason = "live_config_verification_failed"
        if isinstance(verification_error, dict):
            reason = verification_error.get("error") or reason
        MAP_MODE_VERIFICATION_FAILURES.labels(requested_game_mode, requested_map, reason).inc()

    return {
        "ok": verified,
        "rcon_sent": True,
        "verified": verified,
        "requested_map": requested_map,
        "requested_game_mode": requested_game_mode,
        "expected_map": verification.get("expected_map"),
        "actual_map": verification.get("actual_map"),
        "expected_game_mode": verification.get("expected_game_mode"),
        "actual_game_mode": verification.get("actual_game_mode"),
        "commands": commands,
        "verification": verification,
        "error": None if verified else "live_config_verification_failed",
        "failure_reason": failure_reason,
        "message": None
        if verified
        else "Allocated server was configured, but live status did not verify the requested map/mode.",
        "live_status": verification.get("live_status"),
    }


def wait_for_allocation(name: str, server_pool: dict[str, Any]) -> dict:
    deadline = time.time() + ALLOCATION_TIMEOUT_SECONDS

    while time.time() < deadline:
        allocation = custom_objects_api.get_namespaced_custom_object(
            group=ALLOCATION_GROUP,
            version=ALLOCATION_VERSION,
            namespace=server_pool["agones_namespace"],
            plural=ALLOCATION_PLURAL,
            name=name,
        )

        try:
            return extract_allocation_response(allocation)
        except ValueError:
            time.sleep(ALLOCATION_POLL_INTERVAL_SECONDS)

    raise TimeoutError(f"allocation {name} did not return address/port before timeout")


def allocate_gameserver(server_pool: dict[str, Any] | None = None) -> dict:
    pool = server_pool or default_server_pool()
    try:
        ALLOCATION_ATTEMPTS.inc()
        request_body = build_allocation_manifest(pool)
        try:
            allocation = custom_objects_api.create_namespaced_custom_object(
                group=ALLOCATION_GROUP,
                version=ALLOCATION_VERSION,
                namespace=pool["agones_namespace"],
                plural=ALLOCATION_PLURAL,
                body=request_body,
            )
        except ApiException as exc:
            raise_kubernetes_api_error(
                operation="create",
                resource_type=ALLOCATION_RESOURCE_KIND,
                namespace=pool["agones_namespace"],
                name=request_body.get("metadata", {}).get("name") or request_body.get("metadata", {}).get("generateName"),
                request_context={"server_pool": server_pool_response(pool), "allocation": request_body},
                exc=exc,
            )
        except Exception as exc:
            raise BackendApiError({"error": "allocation_create_failed", "message": str(exc)}, 500)

        allocation_name = allocation.get("metadata", {}).get("name")
        try:
            allocation_response = extract_allocation_response(allocation)
            allocation_response["server_pool"] = server_pool_response(pool)
            ALLOCATION_SUCCESSES.inc()
            return allocation_response
        except ValueError:
            pass

        if not allocation_name:
            raise BackendApiError(
                {
                    "error": "allocation_create_failed",
                    "message": "allocation request name missing from create response",
                    "resource_type": ALLOCATION_RESOURCE_KIND,
                    "namespace": pool["agones_namespace"],
                    "request_context": {"server_pool": server_pool_response(pool), "allocation": request_body},
                },
                500,
            )

        try:
            allocation_response = wait_for_allocation(allocation_name, pool)
            allocation_response["server_pool"] = server_pool_response(pool)
            ALLOCATION_SUCCESSES.inc()
            return allocation_response
        except TimeoutError as exc:
            raise BackendApiError(
                {
                    "error": "allocation_timeout",
                    "message": str(exc),
                    "allocation_request_name": allocation_name,
                },
                504,
            ) from exc
        except ApiException as exc:
            raise_kubernetes_api_error(
                operation="get",
                resource_type=ALLOCATION_RESOURCE_KIND,
                namespace=pool["agones_namespace"],
                name=allocation_name,
                request_context={"allocation_request_name": allocation_name, "server_pool": server_pool_response(pool)},
                exc=exc,
                allocation_request_name=allocation_name,
            )
        except ValueError as exc:
            raise BackendApiError(
                {
                    "error": "allocation_invalid",
                    "message": str(exc),
                    "allocation_request_name": allocation_name,
                },
                502,
            ) from exc
        except Exception as exc:
            raise BackendApiError(
                {
                    "error": "allocation_read_failed",
                    "message": str(exc),
                    "allocation_request_name": allocation_name,
                },
                500,
            ) from exc
    except BackendApiError as exc:
        ALLOCATION_FAILURES.labels(exc.payload.get("error", "backend_api_error")).inc()
        raise


def delete_gameserver(name: str, *, agones_namespace: str | None = None) -> dict[str, Any]:
    namespace = agones_namespace or AGONES_NAMESPACE
    try:
        custom_objects_api.delete_namespaced_custom_object(
            group="agones.dev",
            version="v1",
            namespace=namespace,
            plural="gameservers",
            name=name,
        )
    except ApiException as exc:
        if exc.status == 404:
            return {"deleted": False, "already_missing": True}
        raise_kubernetes_api_error(
            operation="delete",
            resource_type=GAMESERVER_RESOURCE_KIND,
            namespace=namespace,
            name=name,
            request_context={"gameserver_name": name},
            exc=exc,
        )
    except Exception as exc:
        raise BackendApiError({"error": "gameserver_delete_failed", "message": str(exc), "gameserver_name": name}, 500) from exc

    return {"deleted": True, "already_missing": False}


def get_gameserver(name: str) -> dict[str, Any]:
    try:
        return custom_objects_api.get_namespaced_custom_object(
            group="agones.dev",
            version="v1",
            namespace=AGONES_NAMESPACE,
            plural="gameservers",
            name=name,
        )
    except ApiException as exc:
        if exc.status == 404:
            raise BackendApiError(
                {
                    "error": "gameserver_not_found",
                    "message": f"GameServer {name} was not found",
                    "resource_type": GAMESERVER_RESOURCE_KIND,
                    "namespace": AGONES_NAMESPACE,
                    "name": name,
                },
                404,
            ) from exc
        raise_kubernetes_api_error(
            operation="get",
            resource_type=GAMESERVER_RESOURCE_KIND,
            namespace=AGONES_NAMESPACE,
            name=name,
            request_context={"gameserver_name": name},
            exc=exc,
        )
    except Exception as exc:
        raise BackendApiError({"error": "gameserver_read_failed", "message": str(exc), "gameserver_name": name}, 500) from exc


def linked_match_for_gameserver(name: str) -> dict[str, Any] | None:
    for match in MATCHES.values():
        allocated_server = match.get("allocated_server") or {}
        if allocated_server.get("allocated_game_server_name") == name:
            return match

    return None


def terminate_allocated_gameserver(name: str) -> dict[str, Any]:
    gameserver = get_gameserver(name)
    summary = extract_gameserver_summary(gameserver)

    if summary.get("state") != "Allocated":
        raise BackendApiError(
            {
                "error": "gameserver_not_allocated",
                "message": f"GameServer {name} is {summary.get('state') or 'unknown'}, not Allocated",
                "gameserver": summary,
            },
            409,
        )

    with MATCHES_LOCK:
        linked_match = linked_match_for_gameserver(name)
        if linked_match and linked_match["status"] not in FINISHED_MATCH_STATUSES:
            linked_match["status"] = "releasing"

    try:
        release_result = delete_gameserver(name)
    except BackendApiError:
        with MATCHES_LOCK:
            linked_match = linked_match_for_gameserver(name)
            if linked_match and linked_match["status"] == "releasing":
                linked_match["status"] = "allocated"
        raise

    linked_match_snapshot = None

    with MATCHES_LOCK:
        linked_match = linked_match_for_gameserver(name)
        if linked_match:
            released_server = linked_match.get("allocated_server")
            linked_match["released_server"] = released_server
            linked_match["release_result"] = release_result
            linked_match["allocated_server"] = None
            linked_match["released_at"] = utc_now()
            linked_match["status"] = "released"
            linked_match_snapshot = linked_match.copy()

    clear_status_cache(summary.get("address"), summary.get("port"))

    return {
        "terminated": release_result.get("deleted") is True,
        "already_missing": release_result.get("already_missing") is True,
        "gameserver": summary,
        "release_result": release_result,
        "linked_match": match_response(linked_match_snapshot) if linked_match_snapshot else None,
    }


def parse_json_body() -> dict[str, Any]:
    if not request.data:
        return {}

    try:
        body = request.get_json(force=False, silent=False)
    except (BadRequest, UnsupportedMediaType) as exc:
        raise BackendApiError({"error": "malformed_json", "message": "request body must be valid JSON"}, 400) from exc

    if body is None:
        return {}

    if not isinstance(body, dict):
        raise BackendApiError({"error": "invalid_json", "message": "request body must be a JSON object"}, 400)

    return body


def validate_max_players(value: Any) -> int:
    if value is None or value == "":
        return DEFAULT_MAX_PLAYERS

    try:
        max_players = int(value)
    except (TypeError, ValueError) as exc:
        raise BackendApiError({"error": "invalid_max_players", "message": "max_players must be a positive integer"}, 400) from exc

    if max_players < 1 or max_players > MAX_MATCH_PLAYERS_LIMIT:
        raise BackendApiError(
            {
                "error": "invalid_max_players",
                "message": f"max_players must be between 1 and {MAX_MATCH_PLAYERS_LIMIT}",
            },
            400,
        )

    return max_players


def validate_db_id(value: str, field: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (TypeError, ValueError) as exc:
        raise BackendApiError({"error": "invalid_id", "message": f"{field} must be a valid UUID"}, 400) from exc


def validate_required_text(value: Any, field: str, *, max_length: int = 120) -> str:
    if not isinstance(value, str):
        raise BackendApiError({"error": "invalid_field", "message": f"{field} is required"}, 400)

    cleaned = value.strip()
    if not cleaned:
        raise BackendApiError({"error": "invalid_field", "message": f"{field} is required"}, 400)

    if len(cleaned) > max_length:
        raise BackendApiError({"error": "invalid_field", "message": f"{field} must be {max_length} characters or fewer"}, 400)

    return cleaned


def validate_optional_text(value: Any, field: str, *, max_length: int = 240) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise BackendApiError({"error": "invalid_field", "message": f"{field} must be a string"}, 400)

    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > max_length:
        raise BackendApiError({"error": "invalid_field", "message": f"{field} must be {max_length} characters or fewer"}, 400)
    return cleaned


def validate_optional_positive_int(value: Any, field: str) -> int | None:
    if value is None or value == "":
        return None

    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise BackendApiError({"error": "invalid_field", "message": f"{field} must be an integer"}, 400) from exc

    if parsed <= 0:
        raise BackendApiError({"error": "invalid_field", "message": f"{field} must be positive"}, 400)

    return parsed


def validate_non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise BackendApiError({"error": "invalid_field", "message": f"{field} must be a non-negative integer"}, 400)
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        raise BackendApiError({"error": "invalid_field", "message": f"{field} must be a non-negative integer"}, 400)
    if parsed < 0:
        raise BackendApiError({"error": "invalid_field", "message": f"{field} must be a non-negative integer"}, 400)
    return parsed


def validate_optional_bool(value: Any, field: str) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y"}:
            return True
        if normalized in {"0", "false", "no", "n"}:
            return False
    raise BackendApiError({"error": "invalid_field", "message": f"{field} must be a boolean"}, 400)


def validate_status(value: Any, field: str, allowed: tuple[str, ...], default: str) -> str:
    if value is None or value == "":
        return default
    if not isinstance(value, str):
        raise BackendApiError({"error": "invalid_status", "message": f"{field} must be a string"}, 400)

    status = value.strip().lower()
    if status not in allowed:
        raise BackendApiError(
            {
                "error": "invalid_status",
                "message": f"{field} must be one of: {', '.join(allowed)}",
                "allowed_statuses": list(allowed),
            },
            400,
        )
    return status


def validate_optional_timestamp(value: Any, field: str) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise BackendApiError({"error": "invalid_field", "message": f"{field} must be an ISO-8601 timestamp string"}, 400)

    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise BackendApiError({"error": "invalid_field", "message": f"{field} must be an ISO-8601 timestamp string"}, 400) from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def validate_optional_db_id(value: Any, field: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise BackendApiError({"error": "invalid_id", "message": f"{field} must be a UUID string"}, 400)
    return validate_db_id(value, field)


def db_exception_response(exc: psycopg.Error, *, fallback_error: str, fallback_message: str) -> tuple[Any, int]:
    APP.logger.error("PostgreSQL query failed sqlstate=%s error=%s", getattr(exc, "sqlstate", None), exc)
    if exc.sqlstate == "23505":
        return jsonify({"error": "database_conflict", "message": "a record with those values already exists"}), 409
    if exc.sqlstate == "23503":
        return jsonify({"error": "invalid_reference", "message": "referenced record was not found"}), 400

    return jsonify({"error": fallback_error, "message": fallback_message, "details": str(exc)}), 500


def fetch_tournament(cur, tournament_id: str) -> dict[str, Any]:
    cur.execute(
        """
        SELECT
          id, name, slug, description, status, format, winner_team_id, bracket_size,
          bracket_generated_at, started_at, finished_at, completed_at, created_at, updated_at
        FROM tournaments
        WHERE id = %s
        """,
        (tournament_id,),
    )
    tournament = cur.fetchone()
    if not tournament:
        raise BackendApiError({"error": "tournament_not_found", "message": f"tournament {tournament_id} was not found"}, 404)
    return tournament


def fetch_tournament_teams(cur, tournament_id: str) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT id, tournament_id, name, tag, seed, created_at, updated_at
        FROM teams
        WHERE tournament_id = %s
        ORDER BY seed NULLS LAST, name
        """,
        (tournament_id,),
    )
    return cur.fetchall()


def fetch_tournament_rounds(cur, tournament_id: str) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT id, tournament_id, name, round_order, status, created_at, updated_at
        FROM rounds
        WHERE tournament_id = %s
        ORDER BY round_order, created_at
        """,
        (tournament_id,),
    )
    return cur.fetchall()


def fetch_tournament_matches(cur, tournament_id: str) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
          m.id, m.tournament_id, m.round_id, m.team_a_id, m.team_b_id, m.status,
          m.scheduled_at, m.started_at, m.finished_at, m.winner_team_id,
          m.team_a_score, m.team_b_score, m.result_notes, m.requested_map,
          m.requested_game_mode, m.requested_region, m.requested_server_pool_id,
          m.bracket_position, m.next_match_id,
          m.next_match_slot, m.created_at, m.updated_at
        FROM matches m
        LEFT JOIN rounds r ON r.id = m.round_id
        WHERE m.tournament_id = %s
        ORDER BY r.round_order NULLS LAST, m.bracket_position NULLS LAST, m.scheduled_at NULLS LAST, m.created_at
        """,
        (tournament_id,),
    )
    return cur.fetchall()


def tournament_match_has_result(match: dict[str, Any] | None) -> bool:
    if not match:
        return False
    return (
        match.get("team_a_score") is not None
        and match.get("team_b_score") is not None
        and bool(match.get("winner_team_id"))
    )


def find_tournament_final_match(matches: list[dict[str, Any]], rounds: list[dict[str, Any]]) -> dict[str, Any] | None:
    round_order_by_id = {str(round_row["id"]): round_row.get("round_order") or 0 for round_row in rounds}
    bracket_finals = [
        match
        for match in matches
        if match.get("bracket_position") is not None and not match.get("next_match_id")
    ]

    if bracket_finals:
        return sorted(
            bracket_finals,
            key=lambda match: (
                round_order_by_id.get(str(match.get("round_id")), 0),
                match.get("bracket_position") or 0,
                str(match.get("created_at") or match.get("id")),
            ),
            reverse=True,
        )[0]

    finished_matches = [match for match in matches if tournament_match_has_result(match)]
    if not finished_matches:
        return None

    return sorted(
        finished_matches,
        key=lambda match: str(match.get("finished_at") or match.get("updated_at") or match.get("created_at") or match.get("id")),
        reverse=True,
    )[0]


def team_by_id(teams: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(team["id"]): team for team in teams}


def tournament_team_summary(teams_by_id: dict[str, dict[str, Any]], team_id: Any) -> dict[str, Any] | None:
    if not team_id:
        return None
    return row_to_json(teams_by_id.get(str(team_id)))


def build_tournament_summary(cur, tournament_id: str) -> dict[str, Any]:
    tournament = fetch_tournament(cur, tournament_id)
    teams = fetch_tournament_teams(cur, tournament_id)
    rounds = fetch_tournament_rounds(cur, tournament_id)
    matches = fetch_tournament_matches(cur, tournament_id)
    final_match = find_tournament_final_match(matches, rounds)
    final_has_result = tournament_match_has_result(final_match)
    teams_by_id = team_by_id(teams)

    champion_team_id = str(final_match["winner_team_id"]) if final_has_result else None
    runner_up_team_id = None
    if champion_team_id and final_match.get("team_a_id") and final_match.get("team_b_id"):
        team_a_id = str(final_match["team_a_id"])
        team_b_id = str(final_match["team_b_id"])
        runner_up_team_id = team_b_id if champion_team_id == team_a_id else team_a_id

    official_winner_team = tournament_team_summary(teams_by_id, tournament.get("winner_team_id"))
    champion_team = tournament_team_summary(teams_by_id, champion_team_id)
    runner_up_team = tournament_team_summary(teams_by_id, runner_up_team_id)
    total_matches = len(matches)
    finished_matches = sum(1 for match in matches if tournament_match_has_result(match))
    remaining_matches = max(total_matches - finished_matches, 0)
    final_score = None
    if final_has_result:
        final_score = {
            "team_a": final_match.get("team_a_score"),
            "team_b": final_match.get("team_b_score"),
            "label": f"{final_match.get('team_a_score')} - {final_match.get('team_b_score')}",
        }

    cur.execute(
        """
        SELECT COUNT(*) AS count
        FROM match_server_assignments
        WHERE tournament_id = %s AND status = 'active'
        """,
        (tournament_id,),
    )
    active_assignment_count = cur.fetchone()["count"]

    finalize_blockers = []
    is_finalized = tournament.get("status") in {"completed", "finished"} and bool(tournament.get("winner_team_id"))
    if is_finalized:
        finalize_blockers.append("Tournament is already finalized.")
    elif tournament.get("status") == "cancelled":
        finalize_blockers.append("Cancelled tournaments cannot be finalized.")
    elif not final_match:
        finalize_blockers.append("No final match is available yet.")
    elif not final_has_result:
        finalize_blockers.append("Record the final match result before finalizing the tournament.")

    final_teams_label = "No final match yet"
    if final_match:
        team_a_name = teams_by_id.get(str(final_match.get("team_a_id")), {}).get("name") if final_match.get("team_a_id") else "TBD"
        team_b_name = teams_by_id.get(str(final_match.get("team_b_id")), {}).get("name") if final_match.get("team_b_id") else "TBD"
        final_teams_label = f"{team_a_name} vs {team_b_name}"

    if official_winner_team:
        story = f"{official_winner_team['name']} is the finalized tournament winner."
    elif champion_team and runner_up_team and final_score:
        story = f"{champion_team['name']} defeated {runner_up_team['name']} {final_score['label']} in the final. Finalize to lock the tournament winner."
    elif champion_team:
        story = f"{champion_team['name']} won the latest completed match. Finalize when this is the intended tournament result."
    elif total_matches:
        story = f"{finished_matches} of {total_matches} matches are finished. {remaining_matches} remaining."
    else:
        story = "No matches yet. Add teams and generate a bracket or create matches manually."

    return {
        "tournament": row_to_json(tournament),
        "status": tournament.get("status"),
        "completed": is_finalized,
        "completed_at": row_to_json({"completed_at": tournament.get("completed_at")})["completed_at"],
        "winner_team": official_winner_team,
        "champion_team": champion_team,
        "runner_up_team": runner_up_team,
        "final_match": row_to_json(final_match),
        "final_teams_label": final_teams_label,
        "final_score": final_score,
        "final_notes": final_match.get("result_notes") if final_match else None,
        "story": story,
        "can_finalize": len(finalize_blockers) == 0,
        "finalize_blockers": finalize_blockers,
        "active_server_assignment_count": active_assignment_count,
        "counts": {
            "teams": len(teams),
            "rounds": len(rounds),
            "matches": total_matches,
            "finished_matches": finished_matches,
            "remaining_matches": remaining_matches,
        },
        "bracket": {
            "format": tournament.get("format"),
            "size": tournament.get("bracket_size"),
            "generated_at": row_to_json({"generated_at": tournament.get("bracket_generated_at")})["generated_at"],
        },
    }


def fetch_tournament_match(cur, tournament_id: str, match_id: str) -> dict[str, Any]:
    cur.execute(
        """
        SELECT
          id, tournament_id, round_id, team_a_id, team_b_id, status,
          scheduled_at, started_at, finished_at, winner_team_id,
          team_a_score, team_b_score, result_notes, requested_map,
          requested_game_mode, requested_region, requested_server_pool_id,
          bracket_position, next_match_id,
          next_match_slot, created_at, updated_at
        FROM matches
        WHERE id = %s AND tournament_id = %s
        """,
        (match_id, tournament_id),
    )
    match = cur.fetchone()
    if not match:
        raise BackendApiError({"error": "match_not_found", "message": f"match {match_id} was not found in this tournament"}, 404)
    return match


def fetch_active_server_assignment(cur, tournament_id: str, match_id: str) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT
          id, tournament_id, match_id, allocated_game_server_name,
          allocation_request_name, address, port, server_pool_id, region,
          provider, gcp_region, cluster_name, agones_namespace, fleet_name,
          status, created_at, released_at, updated_at
        FROM match_server_assignments
        WHERE tournament_id = %s AND match_id = %s AND status = 'active'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (tournament_id, match_id),
    )
    return cur.fetchone()


def fetch_active_tournament_server_assignments(cur, tournament_id: str) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
          id, tournament_id, match_id, allocated_game_server_name,
          allocation_request_name, address, port, server_pool_id, region,
          provider, gcp_region, cluster_name, agones_namespace, fleet_name,
          status, created_at, released_at, updated_at
        FROM match_server_assignments
        WHERE tournament_id = %s AND status = 'active'
        ORDER BY created_at
        """,
        (tournament_id,),
    )
    return cur.fetchall()


def mark_tournament_assignments_released(
    tournament_id: str,
    released_attempts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not released_attempts:
        return [], []

    assignment_ids = [attempt["assignment"]["id"] for attempt in released_attempts]
    match_ids = list({attempt["assignment"]["match_id"] for attempt in released_attempts})
    assignment_placeholders = ", ".join(["%s"] * len(assignment_ids))
    match_placeholders = ", ".join(["%s"] * len(match_ids))

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE match_server_assignments
                SET status = 'released', released_at = now(), updated_at = now()
                WHERE id IN ({assignment_placeholders})
                RETURNING
                  id, tournament_id, match_id, allocated_game_server_name,
                  allocation_request_name, address, port, server_pool_id, region,
                  provider, gcp_region, cluster_name, agones_namespace, fleet_name,
                  status, created_at, released_at, updated_at
                """,
                assignment_ids,
            )
            released_assignments = cur.fetchall()
            cur.execute(
                f"""
                UPDATE matches
                SET status = 'released', finished_at = COALESCE(finished_at, now()), updated_at = now()
                WHERE tournament_id = %s AND id IN ({match_placeholders})
                RETURNING
                  id, tournament_id, round_id, team_a_id, team_b_id, status,
                  scheduled_at, started_at, finished_at, winner_team_id,
                  team_a_score, team_b_score, result_notes, requested_map,
                  requested_game_mode, requested_region, requested_server_pool_id,
                  bracket_position, next_match_id,
                  next_match_slot, created_at, updated_at
                """,
                [tournament_id, *match_ids],
            )
            released_matches = cur.fetchall()
        conn.commit()

    return released_assignments, released_matches


def release_active_tournament_server_assignments(tournament_id: str) -> dict[str, Any]:
    with db_connect() as conn:
        with conn.cursor() as cur:
            fetch_tournament(cur, tournament_id)
            active_assignments = fetch_active_tournament_server_assignments(cur, tournament_id)

    released_attempts = []
    failed = []
    release_warnings = []

    for assignment in active_assignments:
        assignment_json = assignment_response(assignment, include_live_status=False)
        gameserver_name = assignment["allocated_game_server_name"]
        try:
            release_result = delete_gameserver(gameserver_name, agones_namespace=assignment.get("agones_namespace"))
            clear_status_cache(assignment.get("address"), assignment.get("port"))
            if release_result.get("already_missing"):
                message = f"GameServer {gameserver_name} was already gone; assignment marked released."
                APP.logger.warning(
                    "Tournament server assignment already missing tournament_id=%s assignment_id=%s match_id=%s gameserver=%s",
                    tournament_id,
                    assignment["id"],
                    assignment["match_id"],
                    gameserver_name,
                )
                release_warnings.append(
                    {
                        "assignment": assignment_json,
                        "gameserver_name": gameserver_name,
                        "message": message,
                    }
                )
            released_attempts.append({"assignment": assignment, "release_result": release_result})
        except BackendApiError as exc:
            failed.append(
                {
                    "assignment": assignment_json,
                    "error": exc.payload,
                    "message": exc.payload.get("message", "failed to release server assignment"),
                }
            )

    try:
        released_assignments, released_matches = mark_tournament_assignments_released(tournament_id, released_attempts)
    except psycopg.Error as exc:
        raise BackendApiError(
            {
                "error": "server_assignment_release_update_failed",
                "message": "servers were deleted, but failed to update tournament assignment release state",
            },
            500,
        ) from exc

    release_result_by_assignment_id = {
        str(attempt["assignment"]["id"]): attempt["release_result"]
        for attempt in released_attempts
    }
    match_by_id = {str(match["id"]): match for match in released_matches}
    released = [
        {
            "assignment": assignment_response(assignment, include_live_status=False),
            "match": tournament_match_response(match_by_id.get(str(assignment["match_id"]))),
            "release_result": release_result_by_assignment_id.get(str(assignment["id"])),
        }
        for assignment in released_assignments
    ]

    return {
        "active_count_before": len(active_assignments),
        "released_count": len(released),
        "failed_count": len(failed),
        "released": released,
        "failed": failed,
        "release_warnings": release_warnings,
    }


def release_match_server_assignment_after_result(
    tournament_id: str,
    match_id: str,
    active_assignment: dict[str, Any] | None,
) -> dict[str, Any]:
    if not active_assignment:
        return {
            "server_released": False,
            "released_assignment_id": None,
            "released_assignment": None,
            "release_result": None,
            "release_warning": None,
            "match": None,
        }

    gameserver_name = active_assignment["allocated_game_server_name"]
    release_warning = None

    try:
        release_result = delete_gameserver(
            gameserver_name,
            agones_namespace=active_assignment.get("agones_namespace"),
        )
        clear_status_cache(active_assignment.get("address"), active_assignment.get("port"))
        if release_result.get("already_missing"):
            release_warning = f"GameServer {gameserver_name} was already gone; assignment marked released."
            APP.logger.warning(
                "Match result release found missing GameServer tournament_id=%s match_id=%s assignment_id=%s gameserver=%s",
                tournament_id,
                match_id,
                active_assignment["id"],
                gameserver_name,
            )
    except BackendApiError as exc:
        message = exc.payload.get("message", "failed to release match server after result save")
        APP.logger.warning(
            "Match result saved but server release failed tournament_id=%s match_id=%s assignment_id=%s gameserver=%s error=%s",
            tournament_id,
            match_id,
            active_assignment["id"],
            gameserver_name,
            exc.payload,
        )
        return {
            "server_released": False,
            "released_assignment_id": None,
            "released_assignment": None,
            "release_result": None,
            "release_warning": message,
            "match": None,
            "active_assignment": assignment_response(active_assignment, include_live_status=False),
        }

    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE match_server_assignments
                    SET status = 'released', released_at = now(), updated_at = now()
                    WHERE id = %s
                    RETURNING
                      id, tournament_id, match_id, allocated_game_server_name,
                      allocation_request_name, address, port, server_pool_id, region,
                      provider, gcp_region, cluster_name, agones_namespace, fleet_name,
                      status, created_at, released_at, updated_at
                    """,
                    (active_assignment["id"],),
                )
                released_assignment = cur.fetchone()
                cur.execute(
                    """
                    UPDATE matches
                    SET status = 'released', finished_at = COALESCE(finished_at, now()), updated_at = now()
                    WHERE id = %s AND tournament_id = %s
                    RETURNING
                      id, tournament_id, round_id, team_a_id, team_b_id, status,
                      scheduled_at, started_at, finished_at, winner_team_id,
                      team_a_score, team_b_score, result_notes, requested_map,
                      requested_game_mode, requested_region, requested_server_pool_id,
                      bracket_position, next_match_id,
                      next_match_slot, created_at, updated_at
                    """,
                    (match_id, tournament_id),
                )
                released_match = cur.fetchone()
            conn.commit()
    except psycopg.Error as exc:
        APP.logger.exception(
            "Match result release deleted GameServer but failed to mark assignment released tournament_id=%s match_id=%s assignment_id=%s",
            tournament_id,
            match_id,
            active_assignment["id"],
        )
        return {
            "server_released": False,
            "released_assignment_id": None,
            "released_assignment": None,
            "release_result": release_result,
            "release_warning": "match result was saved and GameServer release was requested, but assignment release state could not be stored",
            "match": None,
            "active_assignment": assignment_response(active_assignment, include_live_status=False),
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }

    return {
        "server_released": True,
        "released_assignment_id": str(released_assignment["id"]) if released_assignment else str(active_assignment["id"]),
        "released_assignment": assignment_response(released_assignment, include_live_status=False) if released_assignment else None,
        "release_result": release_result,
        "release_warning": release_warning,
        "match": released_match,
        "active_assignment": None,
    }


def assignment_response(assignment: dict[str, Any], *, include_live_status: bool = True) -> dict[str, Any]:
    response = row_to_json(assignment) or {}
    default_metadata = default_server_pool_metadata()
    for key, value in default_metadata.items():
        response[key] = response.get(key) or value

    response["server_pool"] = {
        "id": response.get("server_pool_id"),
        "region": response.get("region"),
        "provider": response.get("provider"),
        "gcp_region": response.get("gcp_region"),
        "cluster_name": response.get("cluster_name"),
        "agones_namespace": response.get("agones_namespace"),
        "fleet_name": response.get("fleet_name"),
    }
    address = assignment.get("address")
    port = assignment.get("port")
    response["endpoint"] = f"{address}:{port}" if address and port else None

    if include_live_status and address and port:
        response["live_status"] = get_cached_xonotic_status(address, int(port))

    return response


def attach_active_assignments_to_matches(cur, matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not matches:
        return []

    match_ids = [match["id"] for match in matches]
    placeholders = ", ".join(["%s"] * len(match_ids))
    cur.execute(
        f"""
        SELECT
          id, tournament_id, match_id, allocated_game_server_name,
          allocation_request_name, address, port, server_pool_id, region,
          provider, gcp_region, cluster_name, agones_namespace, fleet_name,
          status, created_at, released_at, updated_at
        FROM match_server_assignments
        WHERE status = 'active' AND match_id IN ({placeholders})
        ORDER BY created_at DESC
        """,
        match_ids,
    )
    assignment_by_match_id: dict[str, dict[str, Any]] = {}
    for assignment in cur.fetchall():
        assignment_by_match_id.setdefault(str(assignment["match_id"]), assignment)

    enriched = []
    for match in matches:
        match_json = tournament_match_response(match) or {}
        active_assignment = assignment_by_match_id.get(str(match["id"]))
        match_json["active_server_assignment"] = assignment_response(active_assignment, include_live_status=False) if active_assignment else None
        enriched.append(match_json)

    return enriched


def recover_stale_tournament_match_allocations(cur, tournament_id: str) -> None:
    cur.execute(
        """
        SELECT
          m.id, m.requested_map, m.requested_game_mode, m.updated_at,
          msa.id AS assignment_id, msa.address, msa.port
        FROM matches m
        LEFT JOIN LATERAL (
          SELECT id, address, port
          FROM match_server_assignments
          WHERE tournament_id = m.tournament_id
            AND match_id = m.id
            AND status = 'active'
          ORDER BY created_at DESC
          LIMIT 1
        ) msa ON TRUE
        WHERE m.tournament_id = %s
          AND m.status = 'server_allocating'
          AND m.updated_at < now() - (%s * interval '1 second')
        ORDER BY m.updated_at
        """,
        (tournament_id, TOURNAMENT_ALLOCATION_STALE_SECONDS),
    )
    stale_matches = cur.fetchall()

    for stale_match in stale_matches:
        next_status = "failed"
        recovery_reason = "no_active_assignment"
        live_status = None

        if stale_match.get("assignment_id"):
            live_status = get_cached_xonotic_status(stale_match.get("address"), stale_match.get("port"))
            if live_status and live_status.get("ok"):
                actual_map = live_status.get("map")
                actual_game_mode = normalize_game_mode(live_status.get("game_mode"))
                requested_map = stale_match.get("requested_map")
                requested_game_mode = stale_match.get("requested_game_mode")
                map_verified = requested_map is None or actual_map == requested_map
                mode_verified = requested_game_mode is None or actual_game_mode == requested_game_mode
                if map_verified and mode_verified:
                    next_status = "server_ready"
                    recovery_reason = "active_assignment_verified"
                else:
                    recovery_reason = "active_assignment_verification_mismatch"
            else:
                recovery_reason = (live_status or {}).get("error") or "status_query_failed"

        cur.execute(
            """
            UPDATE matches
            SET status = %s, updated_at = now()
            WHERE id = %s
              AND tournament_id = %s
              AND status = 'server_allocating'
            RETURNING id
            """,
            (next_status, stale_match["id"], tournament_id),
        )
        if cur.fetchone():
            APP.logger.info(
                "Recovered stale tournament match allocation tournament_id=%s match_id=%s status=%s reason=%s live_status_ok=%s",
                tournament_id,
                stale_match["id"],
                next_status,
                recovery_reason,
                bool(live_status and live_status.get("ok")),
            )


def mark_tournament_match_allocation_failed(tournament_id: str, match_id: str) -> None:
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE matches m
                    SET status = 'failed', updated_at = now()
                    WHERE m.id = %s
                      AND m.tournament_id = %s
                      AND m.status = 'server_allocating'
                      AND NOT EXISTS (
                        SELECT 1
                        FROM match_server_assignments msa
                        WHERE msa.tournament_id = m.tournament_id
                          AND msa.match_id = m.id
                          AND msa.status = 'active'
                      )
                    """,
                    (match_id, tournament_id),
                )
            conn.commit()
    except Exception:
        APP.logger.warning(
            "Failed to mark tournament match allocation failure tournament_id=%s match_id=%s",
            tournament_id,
            match_id,
            exc_info=True,
        )


def ensure_team_in_tournament(cur, tournament_id: str, team_id: str | None, field: str) -> None:
    if not team_id:
        return

    cur.execute("SELECT id FROM teams WHERE id = %s AND tournament_id = %s", (team_id, tournament_id))
    if not cur.fetchone():
        raise BackendApiError({"error": "team_not_found", "message": f"{field} was not found in this tournament"}, 404)


def validate_match_winner(match: dict[str, Any], winner_team_id: str) -> str:
    normalized_winner_team_id = validate_db_id(winner_team_id, "winner_team_id")
    normalized_team_a_id = str(match["team_a_id"]) if match.get("team_a_id") else None
    normalized_team_b_id = str(match["team_b_id"]) if match.get("team_b_id") else None
    valid_winner_ids = [team_id for team_id in (normalized_team_a_id, normalized_team_b_id) if team_id]
    if normalized_winner_team_id not in valid_winner_ids:
        APP.logger.warning(
            "Invalid tournament match winner tournament_id=%s match_id=%s winner_team_id=%s team_a_id=%s team_b_id=%s",
            match.get("tournament_id"),
            match.get("id"),
            normalized_winner_team_id,
            normalized_team_a_id,
            normalized_team_b_id,
        )
        raise BackendApiError(
            {
                "error": "invalid_winner_team",
                "message": "winner_team_id must be either team_a_id or team_b_id for this match",
                "winner_team_id": normalized_winner_team_id,
                "team_a_id": normalized_team_a_id,
                "team_b_id": normalized_team_b_id,
                "valid_winner_team_ids": valid_winner_ids,
            },
            400,
        )
    return normalized_winner_team_id


def advance_bracket_winner(cur, tournament_id: str, match: dict[str, Any], winner_team_id: str) -> dict[str, Any] | None:
    next_match_id = str(match["next_match_id"]) if match.get("next_match_id") else None
    next_match_slot = match.get("next_match_slot")
    if not next_match_id:
        return None

    if next_match_slot not in {"team_a", "team_b"}:
        raise BackendApiError(
            {
                "error": "invalid_bracket_link",
                "message": "match has a next_match_id but no valid next_match_slot",
                "match_id": str(match.get("id")),
                "next_match_id": next_match_id,
                "next_match_slot": next_match_slot,
            },
            500,
        )

    next_match = fetch_tournament_match(cur, tournament_id, next_match_id)
    slot_field = f"{next_match_slot}_id"
    existing_team_id = str(next_match[slot_field]) if next_match.get(slot_field) else None
    if existing_team_id and existing_team_id != winner_team_id:
        raise BackendApiError(
            {
                "error": "bracket_advance_conflict",
                "message": "winner cannot advance because the next match slot is already occupied by a different team",
                "match_id": str(match.get("id")),
                "winner_team_id": winner_team_id,
                "next_match_id": next_match_id,
                "next_match_slot": next_match_slot,
                "existing_team_id": existing_team_id,
            },
            409,
        )

    if existing_team_id == winner_team_id:
        return next_match

    slot_column = "team_a_id" if next_match_slot == "team_a" else "team_b_id"
    cur.execute(
        f"""
        UPDATE matches
        SET {slot_column} = %s, updated_at = now()
        WHERE id = %s AND tournament_id = %s
        RETURNING
          id, tournament_id, round_id, team_a_id, team_b_id, status,
          scheduled_at, started_at, finished_at, winner_team_id,
          team_a_score, team_b_score, result_notes, requested_map,
          requested_game_mode, requested_region, requested_server_pool_id,
          bracket_position, next_match_id,
          next_match_slot, created_at, updated_at
        """,
        (winner_team_id, next_match_id, tournament_id),
    )
    return cur.fetchone()


def ensure_round_in_tournament(cur, tournament_id: str, round_id: str | None) -> None:
    if not round_id:
        return

    cur.execute("SELECT id FROM rounds WHERE id = %s AND tournament_id = %s", (round_id, tournament_id))
    if not cur.fetchone():
        raise BackendApiError({"error": "round_not_found", "message": "round_id was not found in this tournament"}, 404)


def fetch_seeded_bracket_teams(cur, tournament_id: str) -> tuple[int, dict[int, dict[str, Any]]]:
    cur.execute(
        """
        SELECT id, tournament_id, name, tag, seed, created_at, updated_at
        FROM teams
        WHERE tournament_id = %s
        ORDER BY seed NULLS LAST, name
        """,
        (tournament_id,),
    )
    teams = cur.fetchall()
    bracket_size = len(teams)
    if bracket_size not in SUPPORTED_BRACKET_SIZES:
        raise BackendApiError(
            {
                "error": "unsupported_bracket_size",
                "message": "single-elimination bracket generation currently supports exactly 2, 4, or 8 teams",
                "team_count": bracket_size,
                "supported_sizes": list(SUPPORTED_BRACKET_SIZES),
            },
            400,
        )

    seeds = [team.get("seed") for team in teams]
    expected_seeds = list(range(1, bracket_size + 1))
    if sorted(seed for seed in seeds if seed is not None) != expected_seeds:
        raise BackendApiError(
            {
                "error": "invalid_bracket_seeds",
                "message": "teams must have unique seeds from 1 through the bracket size before generation",
                "expected_seeds": expected_seeds,
                "actual_seeds": seeds,
            },
            400,
        )

    return bracket_size, {team["seed"]: team for team in teams}


def ensure_bracket_generation_allowed(cur, tournament_id: str) -> None:
    cur.execute("SELECT COUNT(*) AS count FROM rounds WHERE tournament_id = %s", (tournament_id,))
    round_count = cur.fetchone()["count"]
    cur.execute("SELECT COUNT(*) AS count FROM matches WHERE tournament_id = %s", (tournament_id,))
    match_count = cur.fetchone()["count"]
    if round_count or match_count:
        raise BackendApiError(
            {
                "error": "bracket_generation_conflict",
                "message": "bracket generation requires a tournament with no existing rounds or matches",
                "round_count": round_count,
                "match_count": match_count,
            },
            409,
        )


def ensure_bracket_replacement_allowed(cur, tournament: dict[str, Any]) -> None:
    tournament_id = str(tournament["id"])
    if tournament.get("status") in {"completed", "finished"}:
        raise BackendApiError(
            {
                "error": "bracket_replacement_conflict",
                "message": "completed tournaments cannot regenerate brackets",
                "tournament_status": tournament.get("status"),
            },
            409,
        )

    cur.execute(
        """
        SELECT COUNT(*) AS count
        FROM matches
        WHERE tournament_id = %s
          AND (
            winner_team_id IS NOT NULL
            OR team_a_score IS NOT NULL
            OR team_b_score IS NOT NULL
            OR finished_at IS NOT NULL
            OR status = 'finished'
          )
        """,
        (tournament_id,),
    )
    result_count = cur.fetchone()["count"]
    if result_count:
        raise BackendApiError(
            {
                "error": "bracket_replacement_conflict",
                "message": "recorded match results must be cleared by creating a new tournament; this bracket cannot be regenerated",
                "result_match_count": result_count,
            },
            409,
        )

    cur.execute(
        """
        SELECT COUNT(*) AS count
        FROM match_server_assignments
        WHERE tournament_id = %s
        """,
        (tournament_id,),
    )
    assignment_count = cur.fetchone()["count"]
    if assignment_count:
        raise BackendApiError(
            {
                "error": "bracket_replacement_conflict",
                "message": "server assignment history exists; release or avoid allocating servers before regenerating the bracket",
                "assignment_count": assignment_count,
            },
            409,
        )

    cur.execute(
        """
        SELECT COUNT(*) AS count
        FROM matches
        WHERE tournament_id = %s
          AND status <> ALL(%s)
        """,
        (tournament_id, list(BRACKET_REGENERABLE_MATCH_STATUSES)),
    )
    started_count = cur.fetchone()["count"]
    if started_count:
        raise BackendApiError(
            {
                "error": "bracket_replacement_conflict",
                "message": "brackets can only be regenerated before matches start",
                "started_match_count": started_count,
            },
            409,
        )


def build_bracket_rows(
    *,
    bracket_size: int,
    teams_by_seed: dict[int, dict[str, Any]],
    round_id_by_order: dict[int, str],
    requested_map: str,
    requested_game_mode: str,
    requested_region: str,
    requested_server_pool_id: str,
) -> list[dict[str, Any]]:
    first_round_pairs = BRACKET_SEED_PAIRS[bracket_size]
    match_rows = []
    matches_by_round: dict[int, list[dict[str, Any]]] = {}

    for round_index, _round_name in enumerate(BRACKET_ROUND_NAMES[bracket_size], start=1):
        match_count = max(1, len(first_round_pairs) // (2 ** (round_index - 1)))
        matches_by_round[round_index] = []
        for position in range(1, match_count + 1):
            team_a_id = None
            team_b_id = None
            if round_index == 1:
                team_a_seed, team_b_seed = first_round_pairs[position - 1]
                team_a_id = str(teams_by_seed[team_a_seed]["id"])
                team_b_id = str(teams_by_seed[team_b_seed]["id"])

            match_row = {
                "id": new_db_id(),
                "round_id": round_id_by_order[round_index],
                "team_a_id": team_a_id,
                "team_b_id": team_b_id,
                "bracket_position": position,
                "next_match_id": None,
                "next_match_slot": None,
                "requested_map": requested_map,
                "requested_game_mode": requested_game_mode,
                "requested_region": requested_region,
                "requested_server_pool_id": requested_server_pool_id,
            }
            matches_by_round[round_index].append(match_row)
            match_rows.append(match_row)

    for round_index in range(1, len(matches_by_round)):
        next_round_matches = matches_by_round[round_index + 1]
        for index, match_row in enumerate(matches_by_round[round_index]):
            next_match = next_round_matches[index // 2]
            match_row["next_match_id"] = next_match["id"]
            match_row["next_match_slot"] = "team_a" if index % 2 == 0 else "team_b"

    return match_rows


def apply_match_config_fields(match: dict[str, Any], body: dict[str, Any]) -> None:
    has_map = "requested_map" in body or "map" in body
    has_mode = "requested_game_mode" in body or "game_mode" in body

    if has_map or has_mode:
        allow_experimental = experimental_game_config_allowed(body)
        requested_map, requested_game_mode = validate_requested_game_config(
            body.get("requested_map", body.get("map", match.get("requested_map"))),
            body.get("requested_game_mode", body.get("game_mode", match.get("requested_game_mode"))),
            allow_experimental=allow_experimental,
        )
        match["requested_map"] = requested_map
        match["requested_game_mode"] = requested_game_mode


def match_response(match: dict[str, Any]) -> dict[str, Any]:
    allocated_server = match["allocated_server"]
    live_status = match.get("live_status")

    if allocated_server:
        live_status = query_live_status_with_preservation(match)

    return {
        "match_id": match["match_id"],
        "name": match["name"],
        "status": match["status"],
        "created_at": match["created_at"],
        "allocated_at": match["allocated_at"],
        "released_at": match.get("released_at"),
        "max_players": match["max_players"],
        "requested_map": match.get("requested_map"),
        "requested_game_mode": match.get("requested_game_mode"),
        "current_players": match["current_players"],
        "game_mode": match["game_mode"],
        "map": match["map"],
        "live_max_players": match.get("live_max_players"),
        "last_status_error": match.get("last_status_error"),
        "last_status_error_at": match.get("last_status_error_at"),
        "change_map_verification": match.get("change_map_verification"),
        "allocation_config_result": match.get("allocation_config_result"),
        "joinable": bool(match.get("allocated_server") and match.get("status") == "allocated"),
        "released_server": match.get("released_server"),
        "release_result": match.get("release_result"),
        "allocated_server": allocated_server,
        "live_status": live_status,
    }


def build_match(body: dict[str, Any]) -> dict[str, Any]:
    match_id = uuid.uuid4().hex[:12]
    name = str(body.get("name") or f"Match {len(MATCHES) + 1}").strip()
    if not name:
        name = f"Match {len(MATCHES) + 1}"
    allow_experimental = experimental_game_config_allowed(body)
    requested_map, requested_game_mode = validate_requested_game_config(
        body.get("requested_map", body.get("map")),
        body.get("requested_game_mode", body.get("game_mode")),
        allow_experimental=allow_experimental,
    )

    return {
        "match_id": match_id,
        "name": name,
        "status": "waiting_for_server",
        "created_at": utc_now(),
        "allocated_at": None,
        "released_at": None,
        "max_players": validate_max_players(body.get("max_players")),
        "requested_map": requested_map,
        "requested_game_mode": requested_game_mode,
        "current_players": None,
        "live_max_players": None,
        "game_mode": None,
        "map": None,
        "live_status": None,
        "last_status_error": None,
        "last_status_error_at": None,
        "change_map_verification": None,
        "allocation_config_result": None,
        "allocated_server": None,
        "released_server": None,
        "release_result": None,
    }


@APP.post("/tournaments")
def create_tournament():
    try:
        require_db()
        body = parse_json_body()
        tournament_id = new_db_id()
        name = validate_required_text(body.get("name"), "name")
        slug = validate_optional_text(body.get("slug"), "slug", max_length=80)
        description = validate_optional_text(body.get("description"), "description", max_length=1000)
        status = validate_status(body.get("status"), "status", TOURNAMENT_STATUSES, "draft")
        tournament_format = validate_optional_text(body.get("format"), "format", max_length=40) or "manual"
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code

    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tournaments (id, name, slug, description, status, format)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING
                      id, name, slug, description, status, format, winner_team_id, bracket_size,
                      bracket_generated_at, started_at, finished_at, completed_at, created_at, updated_at
                    """,
                    (tournament_id, name, slug, description, status, tournament_format),
                )
                tournament = cur.fetchone()
            conn.commit()
    except psycopg.Error as exc:
        return db_exception_response(exc, fallback_error="tournament_create_failed", fallback_message="failed to create tournament")

    return jsonify(row_to_json(tournament)), 201


@APP.get("/tournaments")
def list_tournaments():
    try:
        require_db()
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      id, name, slug, description, status, format, winner_team_id, bracket_size,
                      bracket_generated_at, started_at, finished_at, completed_at, created_at, updated_at
                    FROM tournaments
                    ORDER BY created_at DESC
                    """
                )
                tournaments = cur.fetchall()
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code
    except psycopg.Error as exc:
        return db_exception_response(exc, fallback_error="tournament_list_failed", fallback_message="failed to list tournaments")

    return jsonify({"items": rows_to_json(tournaments)})


@APP.get("/tournaments/<tournament_id>")
def get_tournament(tournament_id: str):
    try:
        require_db()
        tournament_id = validate_db_id(tournament_id, "tournament_id")
        with db_connect() as conn:
            with conn.cursor() as cur:
                tournament = fetch_tournament(cur, tournament_id)
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code
    except psycopg.Error as exc:
        return db_exception_response(exc, fallback_error="tournament_read_failed", fallback_message="failed to read tournament")

    return jsonify(row_to_json(tournament))


@APP.get("/tournaments/<tournament_id>/summary")
def get_tournament_summary(tournament_id: str):
    try:
        require_db()
        tournament_id = validate_db_id(tournament_id, "tournament_id")
        with db_connect() as conn:
            with conn.cursor() as cur:
                recover_stale_tournament_match_allocations(cur, tournament_id)
                summary = build_tournament_summary(cur, tournament_id)
            conn.commit()
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code
    except psycopg.Error as exc:
        return db_exception_response(exc, fallback_error="tournament_summary_failed", fallback_message="failed to build tournament summary")

    return jsonify(summary)


@APP.post("/tournaments/<tournament_id>/finalize")
def finalize_tournament(tournament_id: str):
    try:
        require_db()
        tournament_id = validate_db_id(tournament_id, "tournament_id")

        with db_connect() as conn:
            with conn.cursor() as cur:
                summary = build_tournament_summary(cur, tournament_id)
                if not summary["can_finalize"]:
                    return jsonify(
                        {
                            "error": "tournament_finalize_blocked",
                            "message": summary["finalize_blockers"][0],
                            "finalize_blockers": summary["finalize_blockers"],
                            "summary": summary,
                        }
                    ), 409

                winner_team_id = summary["champion_team"]["id"]

        release_summary = release_active_tournament_server_assignments(tournament_id)
        if release_summary["failed_count"] > 0:
            return jsonify(
                {
                    "error": "tournament_finalize_release_failed",
                    "message": "failed to release one or more active match servers before finalizing tournament",
                    "release": release_summary,
                    "summary": summary,
                }
            ), 502

        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE tournaments
                    SET status = 'completed',
                        winner_team_id = %s,
                        completed_at = COALESCE(completed_at, now()),
                        updated_at = now()
                    WHERE id = %s
                    RETURNING
                      id, name, slug, description, status, format, winner_team_id, bracket_size,
                      bracket_generated_at, started_at, finished_at, completed_at, created_at, updated_at
                    """,
                    (winner_team_id, tournament_id),
                )
                tournament = cur.fetchone()
                summary = build_tournament_summary(cur, tournament_id)
            conn.commit()
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code
    except psycopg.Error as exc:
        return db_exception_response(exc, fallback_error="tournament_finalize_failed", fallback_message="failed to finalize tournament")

    return jsonify(
        {
            "tournament": row_to_json(tournament),
            "status": tournament["status"],
            "summary": summary,
            "released_count": release_summary["released_count"],
            "release_warnings": release_summary["release_warnings"],
            "release": release_summary,
        }
    )


@APP.post("/tournaments/<tournament_id>/teams")
def create_tournament_team(tournament_id: str):
    try:
        require_db()
        tournament_id = validate_db_id(tournament_id, "tournament_id")
        body = parse_json_body()
        team_id = new_db_id()
        name = validate_required_text(body.get("name"), "name")
        tag = validate_optional_text(body.get("tag"), "tag", max_length=20)
        seed = validate_optional_positive_int(body.get("seed"), "seed")

        with db_connect() as conn:
            with conn.cursor() as cur:
                fetch_tournament(cur, tournament_id)
                cur.execute(
                    """
                    INSERT INTO teams (id, tournament_id, name, tag, seed)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id, tournament_id, name, tag, seed, created_at, updated_at
                    """,
                    (team_id, tournament_id, name, tag, seed),
                )
                team = cur.fetchone()
            conn.commit()
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code
    except psycopg.Error as exc:
        return db_exception_response(exc, fallback_error="team_create_failed", fallback_message="failed to create team")

    return jsonify(row_to_json(team)), 201


@APP.get("/tournaments/<tournament_id>/teams")
def list_tournament_teams(tournament_id: str):
    try:
        require_db()
        tournament_id = validate_db_id(tournament_id, "tournament_id")
        with db_connect() as conn:
            with conn.cursor() as cur:
                fetch_tournament(cur, tournament_id)
                cur.execute(
                    """
                    SELECT id, tournament_id, name, tag, seed, created_at, updated_at
                    FROM teams
                    WHERE tournament_id = %s
                    ORDER BY seed NULLS LAST, name
                    """,
                    (tournament_id,),
                )
                teams = cur.fetchall()
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code
    except psycopg.Error as exc:
        return db_exception_response(exc, fallback_error="team_list_failed", fallback_message="failed to list teams")

    return jsonify({"items": rows_to_json(teams)})


@APP.post("/tournaments/<tournament_id>/rounds")
def create_tournament_round(tournament_id: str):
    try:
        require_db()
        tournament_id = validate_db_id(tournament_id, "tournament_id")
        body = parse_json_body()
        round_id = new_db_id()
        name = validate_required_text(body.get("name"), "name")
        status = validate_status(body.get("status"), "status", ROUND_STATUSES, "created")
        round_order = validate_optional_positive_int(body.get("round_order", body.get("order")), "round_order")

        with db_connect() as conn:
            with conn.cursor() as cur:
                fetch_tournament(cur, tournament_id)
                if round_order is None:
                    cur.execute("SELECT COALESCE(MAX(round_order), 0) + 1 AS next_order FROM rounds WHERE tournament_id = %s", (tournament_id,))
                    round_order = cur.fetchone()["next_order"]

                cur.execute(
                    """
                    INSERT INTO rounds (id, tournament_id, name, round_order, status)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id, tournament_id, name, round_order, status, created_at, updated_at
                    """,
                    (round_id, tournament_id, name, round_order, status),
                )
                round_row = cur.fetchone()
            conn.commit()
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code
    except psycopg.Error as exc:
        return db_exception_response(exc, fallback_error="round_create_failed", fallback_message="failed to create round")

    return jsonify(row_to_json(round_row)), 201


@APP.get("/tournaments/<tournament_id>/rounds")
def list_tournament_rounds(tournament_id: str):
    try:
        require_db()
        tournament_id = validate_db_id(tournament_id, "tournament_id")
        with db_connect() as conn:
            with conn.cursor() as cur:
                fetch_tournament(cur, tournament_id)
                cur.execute(
                    """
                    SELECT id, tournament_id, name, round_order, status, created_at, updated_at
                    FROM rounds
                    WHERE tournament_id = %s
                    ORDER BY round_order, created_at
                    """,
                    (tournament_id,),
                )
                rounds = cur.fetchall()
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code
    except psycopg.Error as exc:
        return db_exception_response(exc, fallback_error="round_list_failed", fallback_message="failed to list rounds")

    return jsonify({"items": rows_to_json(rounds)})


@APP.post("/tournaments/<tournament_id>/bracket/generate")
def generate_tournament_bracket(tournament_id: str):
    try:
        require_db()
        tournament_id = validate_db_id(tournament_id, "tournament_id")
        body = parse_json_body()
        replace_existing = validate_optional_bool(body.get("replace_existing"), "replace_existing")
        requested_map, requested_game_mode = validate_requested_game_config(
            body.get("requested_map", body.get("map")),
            body.get("requested_game_mode", body.get("game_mode")),
        )
        server_pool = resolve_server_pool(body.get("requested_server_pool_id"), body.get("requested_region"))
        pool_metadata = server_pool_metadata(server_pool)

        with db_connect() as conn:
            with conn.cursor() as cur:
                existing_tournament = fetch_tournament(cur, tournament_id)
                if replace_existing:
                    ensure_bracket_replacement_allowed(cur, existing_tournament)
                    cur.execute(
                        """
                        UPDATE matches
                        SET next_match_id = NULL,
                            next_match_slot = NULL,
                            updated_at = now()
                        WHERE tournament_id = %s
                        """,
                        (tournament_id,),
                    )
                    cur.execute("DELETE FROM matches WHERE tournament_id = %s", (tournament_id,))
                    cur.execute("DELETE FROM rounds WHERE tournament_id = %s", (tournament_id,))
                else:
                    ensure_bracket_generation_allowed(cur, tournament_id)

                bracket_size, teams_by_seed = fetch_seeded_bracket_teams(cur, tournament_id)

                round_rows = []
                round_id_by_order = {}
                for round_order, round_name in enumerate(BRACKET_ROUND_NAMES[bracket_size], start=1):
                    round_id = new_db_id()
                    round_id_by_order[round_order] = round_id
                    cur.execute(
                        """
                        INSERT INTO rounds (id, tournament_id, name, round_order, status)
                        VALUES (%s, %s, %s, %s, 'created')
                        RETURNING id, tournament_id, name, round_order, status, created_at, updated_at
                        """,
                        (round_id, tournament_id, round_name, round_order),
                    )
                    round_rows.append(cur.fetchone())

                bracket_match_rows = build_bracket_rows(
                    bracket_size=bracket_size,
                    teams_by_seed=teams_by_seed,
                    round_id_by_order=round_id_by_order,
                    requested_map=requested_map,
                    requested_game_mode=requested_game_mode,
                    requested_region=pool_metadata["region"],
                    requested_server_pool_id=pool_metadata["server_pool_id"],
                )
                for match_row in bracket_match_rows:
                    cur.execute(
                        """
                        INSERT INTO matches (
                          id, tournament_id, round_id, team_a_id, team_b_id,
                          requested_map, requested_game_mode, requested_region,
                          requested_server_pool_id, bracket_position, next_match_id,
                          next_match_slot
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING
                          id, tournament_id, round_id, team_a_id, team_b_id, status,
                          scheduled_at, started_at, finished_at, winner_team_id,
                          team_a_score, team_b_score, result_notes, requested_map,
                          requested_game_mode, requested_region, requested_server_pool_id,
                          bracket_position, next_match_id,
                          next_match_slot, created_at, updated_at
                        """,
                        (
                            match_row["id"],
                            tournament_id,
                            match_row["round_id"],
                            match_row["team_a_id"],
                            match_row["team_b_id"],
                            match_row["requested_map"],
                            match_row["requested_game_mode"],
                            match_row["requested_region"],
                            match_row["requested_server_pool_id"],
                            match_row["bracket_position"],
                            None,
                            None,
                        ),
                    )
                    cur.fetchone()

                for match_row in bracket_match_rows:
                    if match_row["next_match_id"]:
                        cur.execute(
                            """
                            UPDATE matches
                            SET next_match_id = %s,
                                next_match_slot = %s,
                                updated_at = now()
                            WHERE id = %s AND tournament_id = %s
                            """,
                            (match_row["next_match_id"], match_row["next_match_slot"], match_row["id"], tournament_id),
                        )

                cur.execute(
                    """
                    SELECT
                      m.id, m.tournament_id, m.round_id, m.team_a_id, m.team_b_id, m.status,
                      m.scheduled_at, m.started_at, m.finished_at, m.winner_team_id,
                      m.team_a_score, m.team_b_score, m.result_notes, m.requested_map,
                      m.requested_game_mode, m.requested_region, m.requested_server_pool_id,
                      m.bracket_position, m.next_match_id,
                      m.next_match_slot, m.created_at, m.updated_at
                    FROM matches m
                    LEFT JOIN rounds r ON r.id = m.round_id
                    WHERE m.tournament_id = %s
                    ORDER BY r.round_order, m.bracket_position
                    """,
                    (tournament_id,),
                )
                match_rows = cur.fetchall()

                cur.execute(
                    """
                    UPDATE tournaments
                    SET format = 'single_elimination',
                        bracket_size = %s,
                        bracket_generated_at = now(),
                        updated_at = now()
                    WHERE id = %s
                    RETURNING
                      id, name, slug, description, status, format, winner_team_id, bracket_size,
                      bracket_generated_at, started_at, finished_at, completed_at, created_at, updated_at
                    """,
                    (bracket_size, tournament_id),
                )
                tournament = cur.fetchone()
            conn.commit()
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code
    except psycopg.Error as exc:
        return db_exception_response(exc, fallback_error="bracket_generate_failed", fallback_message="failed to generate bracket")

    return jsonify(
        {
            "tournament": row_to_json(tournament),
            "rounds": rows_to_json(round_rows),
            "matches": tournament_match_rows_response(match_rows),
            "replaced_existing": replace_existing,
        }
    ), 200 if replace_existing else 201


@APP.post("/tournaments/<tournament_id>/matches")
def create_tournament_match(tournament_id: str):
    try:
        require_db()
        tournament_id = validate_db_id(tournament_id, "tournament_id")
        body = parse_json_body()
        match_id = new_db_id()
        round_id = validate_optional_db_id(body.get("round_id"), "round_id")
        team_a_id = validate_optional_db_id(body.get("team_a_id"), "team_a_id")
        team_b_id = validate_optional_db_id(body.get("team_b_id"), "team_b_id")
        if team_a_id and team_b_id and team_a_id == team_b_id:
            raise BackendApiError({"error": "invalid_match_teams", "message": "team_a_id and team_b_id must be different"}, 400)

        status = validate_status(body.get("status"), "status", TOURNAMENT_MATCH_STATUSES, "created")
        scheduled_at = validate_optional_timestamp(body.get("scheduled_at"), "scheduled_at")
        allow_experimental = experimental_game_config_allowed(body)
        requested_map, requested_game_mode = validate_requested_game_config(
            body.get("requested_map", body.get("map")),
            body.get("requested_game_mode", body.get("game_mode")),
            allow_experimental=allow_experimental,
        )
        server_pool = resolve_server_pool(body.get("requested_server_pool_id"), body.get("requested_region"))
        pool_metadata = server_pool_metadata(server_pool)

        with db_connect() as conn:
            with conn.cursor() as cur:
                fetch_tournament(cur, tournament_id)
                ensure_round_in_tournament(cur, tournament_id, round_id)
                ensure_team_in_tournament(cur, tournament_id, team_a_id, "team_a_id")
                ensure_team_in_tournament(cur, tournament_id, team_b_id, "team_b_id")
                cur.execute(
                    """
                    INSERT INTO matches (
                      id, tournament_id, round_id, team_a_id, team_b_id, status,
                      scheduled_at, requested_map, requested_game_mode,
                      requested_region, requested_server_pool_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING
                      id, tournament_id, round_id, team_a_id, team_b_id, status,
                      scheduled_at, started_at, finished_at, winner_team_id,
                      team_a_score, team_b_score, result_notes, requested_map,
                      requested_game_mode, requested_region, requested_server_pool_id,
                      bracket_position, next_match_id,
                      next_match_slot, created_at, updated_at
                    """,
                    (
                        match_id,
                        tournament_id,
                        round_id,
                        team_a_id,
                        team_b_id,
                        status,
                        scheduled_at,
                        requested_map,
                        requested_game_mode,
                        pool_metadata["region"],
                        pool_metadata["server_pool_id"],
                    ),
                )
                match_row = cur.fetchone()
            conn.commit()
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code
    except psycopg.Error as exc:
        return db_exception_response(exc, fallback_error="tournament_match_create_failed", fallback_message="failed to create tournament match")

    return jsonify(tournament_match_response(match_row)), 201


@APP.get("/tournaments/<tournament_id>/matches")
def list_tournament_matches(tournament_id: str):
    try:
        require_db()
        tournament_id = validate_db_id(tournament_id, "tournament_id")
        with db_connect() as conn:
            with conn.cursor() as cur:
                fetch_tournament(cur, tournament_id)
                recover_stale_tournament_match_allocations(cur, tournament_id)
                cur.execute(
                    """
                    SELECT
                      m.id, m.tournament_id, m.round_id, m.team_a_id, m.team_b_id, m.status,
                      m.scheduled_at, m.started_at, m.finished_at, m.winner_team_id,
                      m.team_a_score, m.team_b_score, m.result_notes, m.requested_map,
                      m.requested_game_mode, m.requested_region, m.requested_server_pool_id,
                      m.bracket_position, m.next_match_id,
                      m.next_match_slot, m.created_at, m.updated_at
                    FROM matches m
                    LEFT JOIN rounds r ON r.id = m.round_id
                    WHERE m.tournament_id = %s
                    ORDER BY r.round_order NULLS LAST, m.bracket_position NULLS LAST, m.scheduled_at NULLS LAST, m.created_at
                    """,
                    (tournament_id,),
                )
                matches = cur.fetchall()
                enriched_matches = attach_active_assignments_to_matches(cur, matches)
            conn.commit()
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code
    except psycopg.Error as exc:
        return db_exception_response(exc, fallback_error="tournament_match_list_failed", fallback_message="failed to list tournament matches")

    return jsonify({"items": enriched_matches})


@APP.post("/tournaments/<tournament_id>/matches/<match_id>/result")
def record_tournament_match_result(tournament_id: str, match_id: str):
    try:
        require_db()
        tournament_id = validate_db_id(tournament_id, "tournament_id")
        match_id = validate_db_id(match_id, "match_id")
        body = parse_json_body()
        team_a_score = validate_non_negative_int(body.get("team_a_score"), "team_a_score")
        team_b_score = validate_non_negative_int(body.get("team_b_score"), "team_b_score")
        result_notes = validate_optional_text(body.get("result_notes"), "result_notes", max_length=1000)

        with db_connect() as conn:
            with conn.cursor() as cur:
                fetch_tournament(cur, tournament_id)
                match = fetch_tournament_match(cur, tournament_id, match_id)
                winner_team_id = validate_match_winner(match, body.get("winner_team_id"))
                cur.execute(
                    """
                    UPDATE matches
                    SET
                      team_a_score = %s,
                      team_b_score = %s,
                      winner_team_id = %s,
                      result_notes = %s,
                      status = 'finished',
                      finished_at = now(),
                      updated_at = now()
                    WHERE id = %s AND tournament_id = %s
                    RETURNING
                      id, tournament_id, round_id, team_a_id, team_b_id, status,
                      scheduled_at, started_at, finished_at, winner_team_id,
                      team_a_score, team_b_score, result_notes, requested_map,
                      requested_game_mode, requested_region, requested_server_pool_id,
                      bracket_position, next_match_id,
                      next_match_slot, created_at, updated_at
                    """,
                    (team_a_score, team_b_score, winner_team_id, result_notes, match_id, tournament_id),
                )
                updated_match = cur.fetchone()
                active_assignment = fetch_active_server_assignment(cur, tournament_id, match_id)
                advanced_match = advance_bracket_winner(cur, tournament_id, updated_match, winner_team_id)
            conn.commit()
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code
    except psycopg.Error as exc:
        return db_exception_response(
            exc,
            fallback_error="tournament_match_result_failed",
            fallback_message="failed to record tournament match result",
        )

    release_info = release_match_server_assignment_after_result(tournament_id, match_id, active_assignment)
    response_match = release_info.get("match") or updated_match

    response = tournament_match_response(response_match)
    response["result_saved"] = True
    response["server_released"] = release_info.get("server_released", False)
    response["released_assignment_id"] = release_info.get("released_assignment_id")
    response["released_assignment"] = release_info.get("released_assignment")
    response["release_result"] = release_info.get("release_result")
    response["release_warning"] = release_info.get("release_warning")
    response["active_server_assignment"] = release_info.get("active_assignment")
    response["advanced_match"] = tournament_match_response(advanced_match) if advanced_match else None
    return jsonify(response)


@APP.get("/tournaments/<tournament_id>/matches/<match_id>/server-assignments")
def list_tournament_match_server_assignments(tournament_id: str, match_id: str):
    try:
        require_db()
        tournament_id = validate_db_id(tournament_id, "tournament_id")
        match_id = validate_db_id(match_id, "match_id")

        with db_connect() as conn:
            with conn.cursor() as cur:
                fetch_tournament(cur, tournament_id)
                fetch_tournament_match(cur, tournament_id, match_id)
                cur.execute(
                    """
                    SELECT
          id, tournament_id, match_id, allocated_game_server_name,
          allocation_request_name, address, port, server_pool_id, region,
          provider, gcp_region, cluster_name, agones_namespace, fleet_name,
          status, created_at, released_at, updated_at
                    FROM match_server_assignments
                    WHERE tournament_id = %s AND match_id = %s
                    ORDER BY created_at DESC
                    """,
                    (tournament_id, match_id),
                )
                assignments = cur.fetchall()
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code
    except psycopg.Error as exc:
        return db_exception_response(
            exc,
            fallback_error="server_assignment_list_failed",
            fallback_message="failed to list tournament match server assignments",
        )

    return jsonify({"items": [assignment_response(assignment, include_live_status=False) for assignment in assignments]})


@APP.post("/tournaments/<tournament_id>/matches/<match_id>/allocate-server")
def allocate_tournament_match_server(tournament_id: str, match_id: str):
    try:
        require_db()
        tournament_id = validate_db_id(tournament_id, "tournament_id")
        match_id = validate_db_id(match_id, "match_id")
        body = parse_json_body()
        force_replace = validate_optional_bool(body.get("force_replace"), "force_replace")
        allow_experimental = experimental_game_config_allowed(body)
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code

    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                fetch_tournament(cur, tournament_id)
                recover_stale_tournament_match_allocations(cur, tournament_id)
                match = fetch_tournament_match(cur, tournament_id, match_id)
                requested_map, requested_game_mode = validate_requested_game_config(
                    match.get("requested_map"),
                    match.get("requested_game_mode"),
                    allow_experimental=allow_experimental,
                )
                server_pool = resolve_server_pool(match.get("requested_server_pool_id"), match.get("requested_region"))
                active_assignment = fetch_active_server_assignment(cur, tournament_id, match_id)

                if active_assignment and not force_replace:
                    return jsonify(
                        {
                            "reused_existing_assignment": True,
                            "match": tournament_match_response(match),
                            "assignment": assignment_response(active_assignment),
                        }
                    )

                ensure_ready_gameserver_capacity(server_pool)

                if active_assignment and force_replace:
                    release_result = delete_gameserver(
                        active_assignment["allocated_game_server_name"],
                        agones_namespace=active_assignment.get("agones_namespace"),
                    )
                    cur.execute(
                        """
                        UPDATE match_server_assignments
                        SET status = 'released', released_at = now(), updated_at = now()
                        WHERE id = %s
                        """,
                        (active_assignment["id"],),
                    )
                    clear_status_cache(active_assignment.get("address"), active_assignment.get("port"))
                    APP.logger.info(
                        "Force-released existing tournament match server assignment id=%s match_id=%s release_result=%s",
                        active_assignment["id"],
                        match_id,
                        release_result,
                    )

                cur.execute(
                    """
                    UPDATE matches
                    SET status = 'server_allocating', updated_at = now()
                    WHERE id = %s AND tournament_id = %s
                    """,
                    (match_id, tournament_id),
                )
            conn.commit()
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code
    except psycopg.Error as exc:
        return db_exception_response(
            exc,
            fallback_error="server_assignment_prepare_failed",
            fallback_message="failed to prepare tournament match server assignment",
        )

    allocation = None
    try:
        allocation = allocate_gameserver(server_pool)
        assignment_id = new_db_id()
        with db_connect() as conn:
            with conn.cursor() as cur:
                fetch_tournament(cur, tournament_id)
                match = fetch_tournament_match(cur, tournament_id, match_id)
                active_assignment = fetch_active_server_assignment(cur, tournament_id, match_id)
                if active_assignment:
                    delete_gameserver(allocation["allocated_game_server_name"], agones_namespace=server_pool["agones_namespace"])
                    return jsonify(
                        {
                            "reused_existing_assignment": True,
                            "message": "another active assignment was created while this allocation was in progress",
                            "match": tournament_match_response(match),
                            "assignment": assignment_response(active_assignment),
                        }
                    ), 409

                cur.execute(
                    """
                    INSERT INTO match_server_assignments (
                      id, tournament_id, match_id, allocated_game_server_name,
                      allocation_request_name, address, port, server_pool_id, region,
                      provider, gcp_region, cluster_name, agones_namespace, fleet_name, status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')
                    RETURNING
                      id, tournament_id, match_id, allocated_game_server_name,
                      allocation_request_name, address, port, server_pool_id, region,
                      provider, gcp_region, cluster_name, agones_namespace, fleet_name,
                      status, created_at, released_at, updated_at
                    """,
                    (
                        assignment_id,
                        tournament_id,
                        match_id,
                        allocation["allocated_game_server_name"],
                        allocation.get("allocation_request_name"),
                        allocation["address"],
                        allocation["port"],
                        server_pool["id"],
                        server_pool["region"],
                        server_pool["provider"],
                        server_pool["gcp_region"],
                        server_pool["cluster_name"],
                        server_pool["agones_namespace"],
                        server_pool["fleet_name"],
                    ),
                )
                assignment = cur.fetchone()
            conn.commit()
    except BackendApiError as exc:
        if allocation:
            try:
                delete_gameserver(allocation["allocated_game_server_name"], agones_namespace=server_pool["agones_namespace"])
            except BackendApiError:
                APP.logger.warning("allocated GameServer cleanup failed after tournament assignment error", exc_info=True)
        mark_tournament_match_allocation_failed(tournament_id, match_id)
        return jsonify(exc.payload), exc.status_code
    except psycopg.Error as exc:
        if allocation:
            try:
                delete_gameserver(allocation["allocated_game_server_name"], agones_namespace=server_pool["agones_namespace"])
            except BackendApiError:
                APP.logger.warning("allocated GameServer cleanup failed after tournament assignment DB error", exc_info=True)
        mark_tournament_match_allocation_failed(tournament_id, match_id)
        return db_exception_response(
            exc,
            fallback_error="server_assignment_create_failed",
            fallback_message="failed to store tournament match server assignment",
        )

    try:
        config_result = configure_allocated_server(
            {
                "allocated_game_server_name": allocation["allocated_game_server_name"],
                "address": allocation["address"],
                "port": allocation["port"],
            },
            requested_map=requested_map,
            requested_game_mode=requested_game_mode,
        )
    except BackendApiError as exc:
        config_result = {
            "ok": False,
            "rcon_sent": False,
            "verified": False,
            "requested_map": requested_map,
            "requested_game_mode": requested_game_mode,
            "error": exc.payload.get("error", "server_configuration_failed"),
            "message": exc.payload.get("message", "server assignment was persisted, but configuration failed"),
            "details": exc.payload,
            "live_status": get_cached_xonotic_status(allocation.get("address"), allocation.get("port")),
        }
    except Exception as exc:
        APP.logger.exception(
            "Unexpected tournament match server configuration failure tournament_id=%s match_id=%s allocation=%s",
            tournament_id,
            match_id,
            allocation,
        )
        config_result = {
            "ok": False,
            "rcon_sent": False,
            "verified": False,
            "requested_map": requested_map,
            "requested_game_mode": requested_game_mode,
            "error": "server_configuration_failed",
            "message": "server assignment was persisted, but configuration failed unexpectedly",
            "details": {"error_type": type(exc).__name__, "message": str(exc)},
            "live_status": get_cached_xonotic_status(allocation.get("address"), allocation.get("port")),
        }

    next_status = "server_ready" if config_result.get("verified") else "failed"
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE matches
                    SET status = %s, updated_at = now()
                    WHERE id = %s AND tournament_id = %s
                    RETURNING
                      id, tournament_id, round_id, team_a_id, team_b_id, status,
                      scheduled_at, started_at, finished_at, winner_team_id,
                      team_a_score, team_b_score, result_notes, requested_map,
                      requested_game_mode, requested_region, requested_server_pool_id,
                      bracket_position, next_match_id,
                      next_match_slot, created_at, updated_at
                    """,
                    (next_status, match_id, tournament_id),
                )
                match = cur.fetchone()
                assignment = fetch_active_server_assignment(cur, tournament_id, match_id)
            conn.commit()
    except psycopg.Error as exc:
        return db_exception_response(
            exc,
            fallback_error="server_assignment_status_update_failed",
            fallback_message="server assignment was stored, but match status update failed",
        )

    response_status = 201 if config_result.get("verified") else 202
    return jsonify(
        {
            "match": tournament_match_response(match),
            "assignment": assignment_response(assignment),
            "configuration": config_result,
            "warning": None
            if config_result.get("verified")
            else "server assignment was persisted, but requested map/mode verification failed",
        }
    ), response_status


@APP.post("/tournaments/<tournament_id>/matches/<match_id>/release-server")
def release_tournament_match_server(tournament_id: str, match_id: str):
    try:
        require_db()
        tournament_id = validate_db_id(tournament_id, "tournament_id")
        match_id = validate_db_id(match_id, "match_id")

        with db_connect() as conn:
            with conn.cursor() as cur:
                fetch_tournament(cur, tournament_id)
                fetch_tournament_match(cur, tournament_id, match_id)
                active_assignment = fetch_active_server_assignment(cur, tournament_id, match_id)
                if not active_assignment:
                    raise BackendApiError(
                        {
                            "error": "server_assignment_not_found",
                            "message": f"match {match_id} does not have an active server assignment",
                        },
                        404,
                    )

        release_result = delete_gameserver(
            active_assignment["allocated_game_server_name"],
            agones_namespace=active_assignment.get("agones_namespace"),
        )
        clear_status_cache(active_assignment.get("address"), active_assignment.get("port"))

        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE match_server_assignments
                    SET status = 'released', released_at = now(), updated_at = now()
                    WHERE id = %s
                    RETURNING
                      id, tournament_id, match_id, allocated_game_server_name,
                      allocation_request_name, address, port, server_pool_id, region,
                      provider, gcp_region, cluster_name, agones_namespace, fleet_name,
                      status, created_at, released_at, updated_at
                    """,
                    (active_assignment["id"],),
                )
                assignment = cur.fetchone()
                cur.execute(
                    """
                    UPDATE matches
                    SET status = 'released', finished_at = COALESCE(finished_at, now()), updated_at = now()
                    WHERE id = %s AND tournament_id = %s
                    RETURNING
                      id, tournament_id, round_id, team_a_id, team_b_id, status,
                      scheduled_at, started_at, finished_at, winner_team_id,
                      team_a_score, team_b_score, result_notes, requested_map,
                      requested_game_mode, requested_region, requested_server_pool_id,
                      bracket_position, next_match_id,
                      next_match_slot, created_at, updated_at
                    """,
                    (match_id, tournament_id),
                )
                match = cur.fetchone()
            conn.commit()
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code
    except psycopg.Error as exc:
        return db_exception_response(
            exc,
            fallback_error="server_assignment_release_failed",
            fallback_message="failed to release tournament match server assignment",
        )

    return jsonify({"match": tournament_match_response(match), "assignment": assignment_response(assignment, include_live_status=False), "release_result": release_result})


@APP.post("/tournaments/<tournament_id>/server-assignments/release-all")
def release_all_tournament_match_servers(tournament_id: str):
    try:
        require_db()
        tournament_id = validate_db_id(tournament_id, "tournament_id")

        with db_connect() as conn:
            with conn.cursor() as cur:
                fetch_tournament(cur, tournament_id)
                active_assignments = fetch_active_tournament_server_assignments(cur, tournament_id)
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code
    except psycopg.Error as exc:
        return db_exception_response(
            exc,
            fallback_error="server_assignment_release_all_read_failed",
            fallback_message="failed to read active tournament server assignments",
        )

    released_attempts = []
    failed = []
    for assignment in active_assignments:
        assignment_json = assignment_response(assignment, include_live_status=False)
        try:
            release_result = delete_gameserver(
                assignment["allocated_game_server_name"],
                agones_namespace=assignment.get("agones_namespace"),
            )
            clear_status_cache(assignment.get("address"), assignment.get("port"))
            released_attempts.append({"assignment": assignment, "release_result": release_result})
        except BackendApiError as exc:
            failed.append(
                {
                    "assignment": assignment_json,
                    "error": exc.payload,
                    "message": exc.payload.get("message", "failed to release server assignment"),
                }
            )

    released_assignments = []
    released_matches = []
    if released_attempts:
        assignment_ids = [attempt["assignment"]["id"] for attempt in released_attempts]
        match_ids = list({attempt["assignment"]["match_id"] for attempt in released_attempts})
        assignment_placeholders = ", ".join(["%s"] * len(assignment_ids))
        match_placeholders = ", ".join(["%s"] * len(match_ids))

        try:
            with db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        UPDATE match_server_assignments
                        SET status = 'released', released_at = now(), updated_at = now()
                        WHERE id IN ({assignment_placeholders})
                        RETURNING
                          id, tournament_id, match_id, allocated_game_server_name,
                          allocation_request_name, address, port, server_pool_id, region,
                          provider, gcp_region, cluster_name, agones_namespace, fleet_name,
                          status, created_at, released_at, updated_at
                        """,
                        assignment_ids,
                    )
                    released_assignments = cur.fetchall()
                    cur.execute(
                        f"""
                        UPDATE matches
                        SET status = 'released', finished_at = COALESCE(finished_at, now()), updated_at = now()
                        WHERE tournament_id = %s AND id IN ({match_placeholders})
                        RETURNING
                          id, tournament_id, round_id, team_a_id, team_b_id, status,
                          scheduled_at, started_at, finished_at, winner_team_id,
                          team_a_score, team_b_score, result_notes, requested_map,
                          requested_game_mode, requested_region, requested_server_pool_id,
                          bracket_position, next_match_id,
                          next_match_slot, created_at, updated_at
                        """,
                        [tournament_id, *match_ids],
                    )
                    released_matches = cur.fetchall()
                conn.commit()
        except psycopg.Error as exc:
            return db_exception_response(
                exc,
                fallback_error="server_assignment_release_all_update_failed",
                fallback_message="servers were deleted, but failed to update tournament assignment release state",
            )

    release_result_by_assignment_id = {
        str(attempt["assignment"]["id"]): attempt["release_result"]
        for attempt in released_attempts
    }
    match_by_id = {str(match["id"]): match for match in released_matches}
    released = [
        {
            "assignment": assignment_response(assignment, include_live_status=False),
            "match": tournament_match_response(match_by_id.get(str(assignment["match_id"]))),
            "release_result": release_result_by_assignment_id.get(str(assignment["id"])),
        }
        for assignment in released_assignments
    ]

    return jsonify(
        {
            "active_count_before": len(active_assignments),
            "released_count": len(released),
            "failed_count": len(failed),
            "released": released,
            "failed": failed,
        }
    )


@APP.post("/tournaments/<tournament_id>/matches/<match_id>/admin/broadcast")
def tournament_match_admin_broadcast(tournament_id: str, match_id: str):
    try:
        tournament_id = validate_db_id(tournament_id, "tournament_id")
        match_id = validate_db_id(match_id, "match_id")
        body = parse_json_body()
        message = validate_admin_broadcast_message(body.get("message"))
        result = run_tournament_admin_broadcast(tournament_id, match_id, message)
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code
    except psycopg.Error as exc:
        return db_exception_response(
            exc,
            fallback_error="tournament_match_broadcast_failed",
            fallback_message="failed to send tournament match broadcast",
        )

    return jsonify(result)


@APP.post("/tournaments/<tournament_id>/matches/<match_id>/admin/change-map")
def tournament_match_admin_change_map(tournament_id: str, match_id: str):
    try:
        tournament_id = validate_db_id(tournament_id, "tournament_id")
        match_id = validate_db_id(match_id, "match_id")
        body = parse_json_body()
        map_name = validate_admin_map(body.get("map"))
        result = run_tournament_admin_change_map(tournament_id, match_id, map_name)
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code
    except psycopg.Error as exc:
        return db_exception_response(
            exc,
            fallback_error="tournament_match_change_map_failed",
            fallback_message="failed to change tournament match map",
        )

    return jsonify(result)


@APP.get("/healthz")
def healthz():
    if db_configured() and not DB_MIGRATIONS_READY:
        try:
            ensure_db_ready()
        except BackendApiError:
            pass

    db_status = "not_configured"
    if db_configured():
        db_status = "ok" if DB_MIGRATIONS_READY else "unavailable"

    response = {"status": "ok", "database": {"configured": db_configured(), "status": db_status}}
    if DB_MIGRATION_ERROR:
        response["database"]["last_error"] = DB_MIGRATION_ERROR
    return jsonify(response)


@APP.get("/metrics")
def metrics():
    return Response(generate_latest(), content_type=CONTENT_TYPE_LATEST)


@APP.get("/game-config/options")
def game_config_options():
    return jsonify(game_config_options_response())


@APP.get("/server-pools")
def server_pools():
    pools = [server_pool_response(pool) for pool in configured_server_pools()]
    default_pool = default_server_pool()
    return jsonify(
        {
            "items": pools,
            "default_server_pool_id": default_pool["id"],
            "default_region": default_pool["region"],
            "note": "Server pools include the active South America pool plus planned simulated regions for future multi-region allocation.",
        }
    )


@APP.get("/server-pools/capacity")
def server_pool_capacity():
    pools = configured_server_pools()
    default_pool = default_server_pool()
    return jsonify(
        {
            "items": [server_pool_capacity_response(pool) for pool in pools],
            "default_server_pool_id": default_pool["id"],
            "default_region": default_pool["region"],
            "note": "Capacity is read from the configured Agones Fleet for each enabled server pool.",
        }
    )


@APP.get("/fleet-status")
def fleet_status():
    pool = default_server_pool()
    try:
        fleet = read_fleet_status(pool)
    except ApiException as exc:
        return kubernetes_api_error_response(
            operation="get",
            resource_type=FLEET_RESOURCE_KIND,
            namespace=pool["agones_namespace"],
            name=pool["fleet_name"],
            request_context={"server_pool": server_pool_response(pool)},
            exc=exc,
        )
    except Exception as exc:
        return jsonify({"error": "fleet_status_read_failed", "message": str(exc)}), 500

    return jsonify(fleet)


@APP.get("/gameservers")
def gameservers():
    pool = default_server_pool()
    label_selector = f"agones.dev/fleet={pool['fleet_name']},game={GAME_LABEL}"
    try:
        response = custom_objects_api.list_namespaced_custom_object(
            group="agones.dev",
            version="v1",
            namespace=pool["agones_namespace"],
            plural="gameservers",
            label_selector=label_selector,
        )
    except ApiException as exc:
        return kubernetes_api_error_response(
            operation="list",
            resource_type=GAMESERVER_RESOURCE_KIND,
            namespace=pool["agones_namespace"],
            name=None,
            request_context={"server_pool": server_pool_response(pool), "label_selector": label_selector},
            exc=exc,
        )
    except Exception as exc:
        return jsonify({"error": "gameserver_list_failed", "message": str(exc)}), 500

    items = response.get("items", [])
    items.sort(key=lambda item: item.get("metadata", {}).get("name", ""))
    return jsonify({"items": [extract_gameserver_summary(item) for item in items]})


@APP.post("/matches")
def create_match():
    try:
        body = parse_json_body()
        match = build_match(body)
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code

    with MATCHES_LOCK:
        MATCHES[match["match_id"]] = match

    return jsonify(match_response(match)), 201


@APP.get("/matches")
def list_matches():
    with MATCHES_LOCK:
        matches = [match.copy() for match in sorted(MATCHES.values(), key=lambda match: match["created_at"], reverse=True)]

    return jsonify({"items": [match_response(match) for match in matches]})


@APP.get("/matches/<match_id>")
def get_match(match_id: str):
    with MATCHES_LOCK:
        match = MATCHES.get(match_id)
        if not match:
            return jsonify({"error": "match_not_found", "message": f"match {match_id} was not found"}), 404
        match_snapshot = match.copy()

    return jsonify(match_response(match_snapshot))


@APP.patch("/matches/<match_id>")
def update_match(match_id: str):
    try:
        body = parse_json_body()
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code

    with MATCHES_LOCK:
        match = MATCHES.get(match_id)
        if not match:
            return jsonify({"error": "match_not_found", "message": f"match {match_id} was not found"}), 404
        if match["status"] in FINISHED_MATCH_STATUSES:
            return jsonify({"error": "match_finished", "message": f"match {match_id} has already been released"}), 409
        if match.get("allocated_server") or match["status"] in {"allocating", "configuring"}:
            return jsonify({"error": "match_already_allocated", "message": "requested config can only be edited before allocation"}), 409

        try:
            apply_match_config_fields(match, body)
        except BackendApiError as exc:
            return jsonify(exc.payload), exc.status_code
        match_snapshot = match.copy()

    return jsonify(match_response(match_snapshot))


@APP.post("/matches/<match_id>/allocate")
def allocate_match(match_id: str):
    try:
        body = parse_json_body()
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code

    with MATCHES_LOCK:
        match = MATCHES.get(match_id)
        if not match:
            return jsonify({"error": "match_not_found", "message": f"match {match_id} was not found"}), 404

        if match["status"] in FINISHED_MATCH_STATUSES:
            match_snapshot = match.copy()
            return jsonify(match_response(match_snapshot))

        if match["allocated_server"]:
            match_snapshot = match.copy()
            return jsonify(match_response(match_snapshot))

        if match["status"] == "allocating":
            return jsonify({"error": "allocation_in_progress", "message": f"match {match_id} is already allocating a server"}), 409

        try:
            apply_match_config_fields(match, body)
        except BackendApiError as exc:
            return jsonify(exc.payload), exc.status_code

        requested_map = match["requested_map"]
        requested_game_mode = match["requested_game_mode"]
        match["status"] = "allocating"
        match["allocation_config_result"] = None

    try:
        allocation = allocate_gameserver()
    except BackendApiError as exc:
        with MATCHES_LOCK:
            match = MATCHES.get(match_id)
            if match and not match["allocated_server"]:
                match["status"] = "waiting_for_server"
        return jsonify(exc.payload), exc.status_code

    allocated_server = {
        "address": allocation["address"],
        "port": allocation["port"],
        "allocated_game_server_name": allocation["allocated_game_server_name"],
        "allocation_request_name": allocation.get("allocation_request_name"),
    }

    with MATCHES_LOCK:
        match = MATCHES.get(match_id)
        if not match:
            return jsonify({"error": "match_not_found", "message": f"match {match_id} was not found"}), 404
        if match["status"] in FINISHED_MATCH_STATUSES:
            match_snapshot = match.copy()
            should_cleanup_allocation = True
        else:
            should_cleanup_allocation = False

    if should_cleanup_allocation:
        try:
            delete_gameserver(allocation["allocated_game_server_name"])
        except BackendApiError:
            APP.logger.warning("allocated GameServer cleanup failed after finished match allocation race", exc_info=True)
        return jsonify(match_response(match_snapshot))

    with MATCHES_LOCK:
        match = MATCHES.get(match_id)
        if not match:
            return jsonify({"error": "match_not_found", "message": f"match {match_id} was not found"}), 404
        if not match["allocated_server"]:
            match["allocated_server"] = allocated_server
            match["allocated_at"] = utc_now()
            match["status"] = "configuring"
        match_snapshot = match.copy()

    try:
        config_result = configure_allocated_server(
            allocated_server,
            requested_map=requested_map,
            requested_game_mode=requested_game_mode,
        )
    except BackendApiError as exc:
        config_result = {
            "ok": False,
            "rcon_sent": False,
            "verified": False,
            "requested_map": requested_map,
            "requested_game_mode": requested_game_mode,
            "error": exc.payload.get("error"),
            "message": exc.payload.get("message"),
            "details": exc.payload,
        }

    with MATCHES_LOCK:
        match = MATCHES.get(match_id)
        if not match:
            return jsonify({"error": "match_not_found", "message": f"match {match_id} was not found"}), 404
        if match["status"] in FINISHED_MATCH_STATUSES:
            match_snapshot = match.copy()
            return jsonify(match_response(match_snapshot))

        match["allocation_config_result"] = config_result
        live_status = config_result.get("live_status")
        if live_status and live_status.get("ok"):
            apply_successful_live_status(match, live_status)

        if config_result.get("verified") is True:
            match["status"] = "allocated"
        else:
            match["status"] = "allocated_needs_attention"
            verification_error = (config_result.get("verification") or {}).get("error")
            if verification_error:
                apply_live_status_error(match, verification_error)
        match_snapshot = match.copy()

    return jsonify(match_response(match_snapshot))


@APP.post("/matches/<match_id>/release")
def release_match(match_id: str):
    with MATCHES_LOCK:
        match = MATCHES.get(match_id)
        if not match:
            return jsonify({"error": "match_not_found", "message": f"match {match_id} was not found"}), 404

        if match["status"] in FINISHED_MATCH_STATUSES:
            match_snapshot = match.copy()
            return jsonify(match_response(match_snapshot))

        if match["status"] == "allocating":
            return jsonify({"error": "allocation_in_progress", "message": f"match {match_id} is still allocating a server"}), 409

        allocated_server = match.get("allocated_server")
        if not allocated_server:
            match["status"] = "released"
            match["released_at"] = utc_now()
            match_snapshot = match.copy()
            return jsonify(match_response(match_snapshot))

        gameserver_name = allocated_server.get("allocated_game_server_name")
        if not gameserver_name:
            return jsonify({"error": "release_failed", "message": "allocated GameServer name is missing"}), 500

        match["status"] = "releasing"

    try:
        release_result = delete_gameserver(gameserver_name)
    except BackendApiError as exc:
        with MATCHES_LOCK:
            match = MATCHES.get(match_id)
            if match and match["status"] == "releasing":
                match["status"] = "allocated"
        return jsonify(exc.payload), exc.status_code

    with MATCHES_LOCK:
        match = MATCHES.get(match_id)
        if not match:
            return jsonify({"error": "match_not_found", "message": f"match {match_id} was not found"}), 404

        released_server = match.get("allocated_server")
        match["released_server"] = released_server
        match["release_result"] = release_result
        match["allocated_server"] = None
        match["released_at"] = utc_now()
        match["status"] = "released"
        match_snapshot = match.copy()

    if released_server:
        clear_status_cache(released_server.get("address"), released_server.get("port"))

    return jsonify(match_response(match_snapshot))


@APP.post("/matches/<match_id>/rcon-smoke-test")
def rcon_smoke_test(match_id: str):
    try:
        body = parse_json_body()
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code

    include_say = body.get("include_say") is True or body.get("say") is True

    with MATCHES_LOCK:
        match = MATCHES.get(match_id)
        if not match:
            return jsonify({"error": "match_not_found", "message": f"match {match_id} was not found"}), 404
        if match["status"] in FINISHED_MATCH_STATUSES:
            return jsonify({"error": "match_finished", "message": f"match {match_id} has already been released"}), 409

        allocated_server = match.get("allocated_server")
        if not allocated_server:
            return jsonify({"error": "match_not_allocated", "message": f"match {match_id} does not have an allocated server"}), 409

        allocated_server_snapshot = allocated_server.copy()

    try:
        result = run_rcon_smoke_test(allocated_server_snapshot, include_say=include_say)
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code

    return jsonify(result)


@APP.post("/matches/<match_id>/admin/broadcast")
def admin_broadcast(match_id: str):
    try:
        body = parse_json_body()
        message = validate_admin_broadcast_message(body.get("message"))
        result = run_admin_broadcast(match_id, message)
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code

    return jsonify(result)


@APP.post("/matches/<match_id>/admin/change-map")
def admin_change_map(match_id: str):
    try:
        body = parse_json_body()
        map_name = validate_admin_map(body.get("map"))
        result = run_admin_change_map(match_id, map_name)
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code

    return jsonify(result)


@APP.post("/allocated-servers/<gameserver_name>/terminate")
def terminate_allocated_server(gameserver_name: str):
    try:
        result = terminate_allocated_gameserver(gameserver_name)
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code

    return jsonify(result)


@APP.post("/allocate")
def allocate():
    try:
        body = parse_json_body()
        server_pool = resolve_server_pool(body.get("requested_server_pool_id"), body.get("requested_region"))
        return jsonify(allocate_gameserver(server_pool))
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code


if __name__ == "__main__":
    APP.run(host="0.0.0.0", port=8080)
