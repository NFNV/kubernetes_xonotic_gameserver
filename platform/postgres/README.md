# Dev PostgreSQL

This directory contains the simple PostgreSQL deployment used by the tournament persistence MVP.

It is intentionally a dev/local-cluster setup:

- single PostgreSQL Pod
- one ClusterIP Service
- one PVC for persistence while the cluster is up
- credentials from a Kubernetes Secret created by `./scripts/up.sh`
- no HA, backups, managed database, or production hardening yet

The database runs in the existing `xonotic-allocator-backend` namespace so the allocator backend can read the same namespace-scoped Secret and connect to:

```text
xonotic-postgres.xonotic-allocator-backend.svc.cluster.local:5432
```

Required local env vars:

```bash
export XONOTIC_POSTGRES_DB="xonotic"
export XONOTIC_POSTGRES_USER="xonotic"
export XONOTIC_POSTGRES_PASSWORD="change-me-local-dev-db-password"
```

Do not commit real database passwords. Use `scripts/env.sh` for local values.
