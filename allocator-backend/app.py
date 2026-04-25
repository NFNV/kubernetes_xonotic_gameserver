#!/usr/bin/env python3
import os
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

ALLOCATION_GROUP = "allocation.agones.dev"
ALLOCATION_VERSION = "v1"
ALLOCATION_PLURAL = "gameserverallocations"
ALLOCATION_RESOURCE_KIND = "GameServerAllocation"
GAMESERVER_RESOURCE_KIND = "GameServer"
FLEET_RESOURCE_KIND = "Fleet"

MATCHES: dict[str, dict[str, Any]] = {}
MATCHES_LOCK = Lock()
DEFAULT_MAX_PLAYERS = int(os.environ.get("DEFAULT_MATCH_MAX_PLAYERS", "8"))
DEFAULT_GAME_MODE = os.environ.get("DEFAULT_MATCH_GAME_MODE", "dm")


class BackendApiError(Exception):
    def __init__(self, payload: dict[str, Any], status_code: int):
        super().__init__(payload.get("message", "backend API error"))
        self.payload = payload
        self.status_code = status_code


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


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

    if max_players < 1 or max_players > 64:
        raise BackendApiError({"error": "invalid_max_players", "message": "max_players must be between 1 and 64"}, 400)

    return max_players


def match_response(match: dict[str, Any]) -> dict[str, Any]:
    return {
        "match_id": match["match_id"],
        "name": match["name"],
        "status": match["status"],
        "created_at": match["created_at"],
        "allocated_at": match["allocated_at"],
        "max_players": match["max_players"],
        "current_players": match["current_players"],
        "game_mode": match["game_mode"],
        "map": match["map"],
        "allocated_server": match["allocated_server"],
    }


def build_match(body: dict[str, Any]) -> dict[str, Any]:
    match_id = uuid.uuid4().hex[:12]
    name = str(body.get("name") or f"Match {len(MATCHES) + 1}").strip()
    if not name:
        name = f"Match {len(MATCHES) + 1}"

    game_mode = str(body.get("game_mode") or DEFAULT_GAME_MODE).strip() or DEFAULT_GAME_MODE

    return {
        "match_id": match_id,
        "name": name,
        "status": "waiting_for_server",
        "created_at": utc_now(),
        "allocated_at": None,
        "max_players": validate_max_players(body.get("max_players")),
        "current_players": None,
        "game_mode": game_mode,
        "map": None,
        "allocated_server": None,
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
        matches = sorted(MATCHES.values(), key=lambda match: match["created_at"], reverse=True)
        return jsonify({"items": [match_response(match) for match in matches]})


@APP.get("/matches/<match_id>")
def get_match(match_id: str):
    with MATCHES_LOCK:
        match = MATCHES.get(match_id)
        if not match:
            return jsonify({"error": "match_not_found", "message": f"match {match_id} was not found"}), 404
        return jsonify(match_response(match))


@APP.post("/matches/<match_id>/allocate")
def allocate_match(match_id: str):
    with MATCHES_LOCK:
        match = MATCHES.get(match_id)
        if not match:
            return jsonify({"error": "match_not_found", "message": f"match {match_id} was not found"}), 404

        if match["allocated_server"]:
            return jsonify(match_response(match))

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
        if not match["allocated_server"]:
            match["allocated_server"] = allocated_server
            match["allocated_at"] = utc_now()
            match["status"] = "allocated"
        return jsonify(match_response(match))


@APP.post("/allocate")
def allocate():
    try:
        return jsonify(allocate_gameserver())
    except BackendApiError as exc:
        return jsonify(exc.payload), exc.status_code


if __name__ == "__main__":
    APP.run(host="0.0.0.0", port=8080)
