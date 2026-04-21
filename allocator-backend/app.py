#!/usr/bin/env python3
import os
import time
from typing import Any

from flask import Flask, jsonify
from kubernetes import client, config
from kubernetes.client import ApiException
from kubernetes.config.config_exception import ConfigException


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
    return jsonify(response), 502


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


@APP.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@APP.post("/allocate")
def allocate():
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
        return kubernetes_api_error_response(
            operation="create",
            resource_type=ALLOCATION_RESOURCE_KIND,
            namespace=AGONES_NAMESPACE,
            name=request_body.get("metadata", {}).get("name") or request_body.get("metadata", {}).get("generateName"),
            request_context=request_body,
            exc=exc,
        )
    except Exception as exc:
        return jsonify({"error": "allocation_create_failed", "message": str(exc)}), 500

    allocation_name = allocation.get("metadata", {}).get("name")
    try:
        return jsonify(extract_allocation_response(allocation))
    except ValueError:
        pass

    if not allocation_name:
        return (
            jsonify(
                {
                    "error": "allocation_create_failed",
                    "message": "allocation request name missing from create response",
                    "resource_type": ALLOCATION_RESOURCE_KIND,
                    "namespace": AGONES_NAMESPACE,
                    "request_context": request_body,
                }
            ),
            500,
        )

    try:
        response = wait_for_allocation(allocation_name)
    except TimeoutError as exc:
        return (
            jsonify(
                {
                    "error": "allocation_timeout",
                    "message": str(exc),
                    "allocation_request_name": allocation_name,
                }
            ),
            504,
        )
    except ApiException as exc:
        return kubernetes_api_error_response(
            operation="get",
            resource_type=ALLOCATION_RESOURCE_KIND,
            namespace=AGONES_NAMESPACE,
            name=allocation_name,
            request_context={"allocation_request_name": allocation_name},
            exc=exc,
            allocation_request_name=allocation_name,
        )
    except ValueError as exc:
        return (
            jsonify(
                {
                    "error": "allocation_invalid",
                    "message": str(exc),
                    "allocation_request_name": allocation_name,
                }
            ),
            502,
        )
    except Exception as exc:
        return (
            jsonify(
                {
                    "error": "allocation_read_failed",
                    "message": str(exc),
                    "allocation_request_name": allocation_name,
                }
            ),
            500,
        )

    return jsonify(response)


if __name__ == "__main__":
    APP.run(host="0.0.0.0", port=8080)
