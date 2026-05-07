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

from flask import Flask, jsonify, request
from kubernetes import client, config
from kubernetes.client import ApiException
from kubernetes.config.config_exception import ConfigException
from werkzeug.exceptions import BadRequest, UnsupportedMediaType


APP = Flask(__name__)

AGONES_NAMESPACE = os.environ.get("AGONES_NAMESPACE", "xonotic-agones")
FLEET_NAME = os.environ.get("FLEET_NAME", "xonotic-fleet")
GAME_LABEL = os.environ.get("GAME_LABEL", "xonotic")
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
ADMIN_ALLOWED_MAPS = ("xoylent", "stormkeep", "implosion", "drain", "darkzone", "solarium")

RCON_PACKET_PREFIX = b"\xff\xff\xff\xff"


class BackendApiError(Exception):
    def __init__(self, payload: dict[str, Any], status_code: int):
        super().__init__(payload.get("message", "backend API error"))
        self.payload = payload
        self.status_code = status_code


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


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


def build_allocation_manifest() -> dict:
    return {
        "apiVersion": f"{ALLOCATION_GROUP}/{ALLOCATION_VERSION}",
        "kind": "GameServerAllocation",
        "metadata": {
            "generateName": "xonotic-allocation-",
            "namespace": AGONES_NAMESPACE,
        },
        "spec": {
            "scheduling": "Packed",
            "selectors": [
                {
                    "matchLabels": {
                        "agones.dev/fleet": FLEET_NAME,
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


def send_rcon_command(
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


def verify_change_map_status(address: str, port: int, expected_map: str) -> dict[str, Any]:
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

            if live_status.get("map") == expected_map:
                return {
                    "ok": True,
                    "verified": True,
                    "expected_map": expected_map,
                    "actual_map": live_status.get("map"),
                    "live_status": live_status,
                    "error": None,
                }

            last_error = {
                "ok": False,
                "source": "getstatus",
                "queried_at": utc_now(),
                "error": "change_map_verification_failed",
                "message": f"expected map {expected_map}, got {live_status.get('map') or 'unknown'}",
                "expected_map": expected_map,
                "actual_map": live_status.get("map"),
            }

        time.sleep(XONOTIC_RCON_CHANGE_MAP_VERIFY_INTERVAL_SECONDS)

    return {
        "ok": False,
        "verified": False,
        "expected_map": expected_map,
        "actual_map": last_live_status.get("map") if last_live_status else None,
        "live_status": last_live_status,
        "error": last_error
        or {
            "ok": False,
            "source": "getstatus",
            "queried_at": utc_now(),
            "error": "change_map_verification_failed",
            "message": "live status verification is temporarily unavailable",
            "expected_map": expected_map,
            "actual_map": None,
        },
    }


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


def wait_for_allocation(name: str) -> dict:
    deadline = time.time() + ALLOCATION_TIMEOUT_SECONDS

    while time.time() < deadline:
        allocation = custom_objects_api.get_namespaced_custom_object(
            group=ALLOCATION_GROUP,
            version=ALLOCATION_VERSION,
            namespace=AGONES_NAMESPACE,
            plural=ALLOCATION_PLURAL,
            name=name,
        )

        try:
            return extract_allocation_response(allocation)
        except ValueError:
            time.sleep(ALLOCATION_POLL_INTERVAL_SECONDS)

    raise TimeoutError(f"allocation {name} did not return address/port before timeout")


def allocate_gameserver() -> dict:
    request_body = build_allocation_manifest()
    try:
        allocation = custom_objects_api.create_namespaced_custom_object(
            group=ALLOCATION_GROUP,
            version=ALLOCATION_VERSION,
            namespace=AGONES_NAMESPACE,
            plural=ALLOCATION_PLURAL,
            body=request_body,
        )
    except ApiException as exc:
        raise_kubernetes_api_error(
            operation="create",
            resource_type=ALLOCATION_RESOURCE_KIND,
            namespace=AGONES_NAMESPACE,
            name=request_body.get("metadata", {}).get("name") or request_body.get("metadata", {}).get("generateName"),
            request_context=request_body,
            exc=exc,
        )
    except Exception as exc:
        raise BackendApiError({"error": "allocation_create_failed", "message": str(exc)}, 500)

    allocation_name = allocation.get("metadata", {}).get("name")
    try:
        return extract_allocation_response(allocation)
    except ValueError:
        pass

    if not allocation_name:
        raise BackendApiError(
            {
                "error": "allocation_create_failed",
                "message": "allocation request name missing from create response",
                "resource_type": ALLOCATION_RESOURCE_KIND,
                "namespace": AGONES_NAMESPACE,
                "request_context": request_body,
            },
            500,
        )

    try:
        return wait_for_allocation(allocation_name)
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
            namespace=AGONES_NAMESPACE,
            name=allocation_name,
            request_context={"allocation_request_name": allocation_name},
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


def delete_gameserver(name: str) -> dict[str, Any]:
    try:
        custom_objects_api.delete_namespaced_custom_object(
            group="agones.dev",
            version="v1",
            namespace=AGONES_NAMESPACE,
            plural="gameservers",
            name=name,
        )
    except ApiException as exc:
        if exc.status == 404:
            return {"deleted": False, "already_missing": True}
        raise_kubernetes_api_error(
            operation="delete",
            resource_type=GAMESERVER_RESOURCE_KIND,
            namespace=AGONES_NAMESPACE,
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
        "current_players": match["current_players"],
        "game_mode": match["game_mode"],
        "map": match["map"],
        "live_max_players": match.get("live_max_players"),
        "last_status_error": match.get("last_status_error"),
        "last_status_error_at": match.get("last_status_error_at"),
        "change_map_verification": match.get("change_map_verification"),
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

    return {
        "match_id": match_id,
        "name": name,
        "status": "waiting_for_server",
        "created_at": utc_now(),
        "allocated_at": None,
        "released_at": None,
        "max_players": validate_max_players(body.get("max_players")),
        "current_players": None,
        "live_max_players": None,
        "game_mode": None,
        "map": None,
        "live_status": None,
        "last_status_error": None,
        "last_status_error_at": None,
        "change_map_verification": None,
        "allocated_server": None,
        "released_server": None,
        "release_result": None,
    }


@APP.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@APP.get("/fleet-status")
def fleet_status():
    try:
        fleet = custom_objects_api.get_namespaced_custom_object(
            group="agones.dev",
            version="v1",
            namespace=AGONES_NAMESPACE,
            plural="fleets",
            name=FLEET_NAME,
        )
    except ApiException as exc:
        return kubernetes_api_error_response(
            operation="get",
            resource_type=FLEET_RESOURCE_KIND,
            namespace=AGONES_NAMESPACE,
            name=FLEET_NAME,
            request_context={"fleet_name": FLEET_NAME},
            exc=exc,
        )
    except Exception as exc:
        return jsonify({"error": "fleet_status_read_failed", "message": str(exc)}), 500

    return jsonify(extract_fleet_status(fleet))


@APP.get("/gameservers")
def gameservers():
    label_selector = f"agones.dev/fleet={FLEET_NAME},game={GAME_LABEL}"
    try:
        response = custom_objects_api.list_namespaced_custom_object(
            group="agones.dev",
            version="v1",
            namespace=AGONES_NAMESPACE,
            plural="gameservers",
            label_selector=label_selector,
        )
    except ApiException as exc:
        return kubernetes_api_error_response(
            operation="list",
            resource_type=GAMESERVER_RESOURCE_KIND,
            namespace=AGONES_NAMESPACE,
            name=None,
            request_context={"fleet_name": FLEET_NAME, "label_selector": label_selector},
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


@APP.post("/matches/<match_id>/allocate")
def allocate_match(match_id: str):
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

        match["status"] = "allocating"

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
            match["status"] = "allocated"
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
        return jsonify(allocate_gameserver())
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code


if __name__ == "__main__":
    APP.run(host="0.0.0.0", port=8080)
