import os
import unittest
from unittest.mock import patch

os.environ.setdefault("ADMIN_AUTH_INSECURE_DEV", "1")

from app import APP  # noqa: E402


RELEASE_ENVIRONMENT_KEYS = (
    "APP_VERSION",
    "GIT_SHA",
    "BUILD_TIME",
    "DEPLOYED_AT",
    "DEPLOYMENT_ENVIRONMENT",
    "CLUSTER_NAME",
)


class VersionEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = APP.test_client()

    def test_version_reflects_release_environment(self):
        revision = "8e628f2edf6f9d9a341789a77194bcccacb14825"
        release_environment = {
            "APP_VERSION": "1.2.0",
            "GIT_SHA": revision,
            "BUILD_TIME": "2026-08-06T18:30:00Z",
            "DEPLOYED_AT": "2026-08-06T18:35:00Z",
            "DEPLOYMENT_ENVIRONMENT": "control-plane-dev",
            "CLUSTER_NAME": "xonotic-mvp",
        }

        with patch.dict(os.environ, release_environment, clear=False):
            response = self.client.get("/version")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "service": "xonotic-allocator-backend",
                "version": "1.2.0",
                "revision": revision,
                "revision_short": "8e628f2",
                "built_at": "2026-08-06T18:30:00Z",
                "deployed_at": "2026-08-06T18:35:00Z",
                "environment": "control-plane-dev",
                "cluster": "xonotic-mvp",
            },
        )

    def test_version_uses_stable_local_fallbacks(self):
        fallback_environment = {
            key: ""
            for key in RELEASE_ENVIRONMENT_KEYS
        }

        with patch.dict(os.environ, fallback_environment, clear=False):
            response = self.client.get("/version")

        self.assertEqual(
            response.get_json(),
            {
                "service": "xonotic-allocator-backend",
                "version": "local-dev",
                "revision": "unknown",
                "revision_short": "unknown",
                "built_at": "unknown",
                "deployed_at": "unknown",
                "environment": "local",
                "cluster": "local",
            },
        )

    def test_version_does_not_expose_sensitive_environment(self):
        sensitive_values = {
            "ADMIN_PASSWORD_HASH": "sensitive-admin-hash",
            "ADMIN_SESSION_SECRET": "sensitive-session-secret",
            "POSTGRES_PASSWORD": "sensitive-postgres-password",
            "XONOTIC_RCON_PASSWORD": "sensitive-rcon-password",
        }

        with patch.dict(os.environ, sensitive_values, clear=False):
            response = self.client.get("/version")

        payload = response.get_json()
        serialized_payload = response.get_data(as_text=True)
        self.assertEqual(
            set(payload),
            {
                "service",
                "version",
                "revision",
                "revision_short",
                "built_at",
                "deployed_at",
                "environment",
                "cluster",
            },
        )
        for value in sensitive_values.values():
            self.assertNotIn(value, serialized_payload)

    def test_revision_short_requires_a_full_git_sha(self):
        with patch.dict(os.environ, {"GIT_SHA": "not-a-git-sha"}, clear=False):
            response = self.client.get("/version")

        self.assertEqual(response.get_json()["revision_short"], "unknown")

    def test_metrics_expose_only_stable_build_identity(self):
        response = self.client.get("/metrics")
        build_info_line = next(
            line
            for line in response.get_data(as_text=True).splitlines()
            if line.startswith("allocator_backend_build_info{")
        )

        self.assertIn('version="local-dev"', build_info_line)
        self.assertIn('revision="unknown"', build_info_line)
        self.assertNotIn("deployed_at", build_info_line)
        self.assertNotIn("environment", build_info_line)
        self.assertNotIn("cluster", build_info_line)


if __name__ == "__main__":
    unittest.main()
