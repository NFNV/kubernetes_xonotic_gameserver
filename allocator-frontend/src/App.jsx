import { useEffect, useState } from "react";

const EMPTY_FLEET = {
  name: "xonotic-fleet",
  desired_replicas: 0,
  replicas: 0,
  ready_replicas: 0,
  allocated_replicas: 0,
  reserved_replicas: 0,
};

async function fetchJson(path, options) {
  const response = await fetch(path, options);
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : null;

  if (!response.ok) {
    const message = data?.message || `${response.status} ${response.statusText}`;
    throw new Error(message);
  }

  return data;
}

function StatusPill({ ok, label }) {
  return <span className={`status-pill ${ok ? "ok" : "error"}`}>{label}</span>;
}

function MetricCard({ label, value }) {
  return (
    <div className="metric-card">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
    </div>
  );
}

export default function App() {
  const [backendHealthy, setBackendHealthy] = useState(false);
  const [fleetStatus, setFleetStatus] = useState(EMPTY_FLEET);
  const [gameservers, setGameservers] = useState([]);
  const [latestAllocation, setLatestAllocation] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [allocating, setAllocating] = useState(false);
  const [error, setError] = useState("");
  const [lastUpdated, setLastUpdated] = useState("");

  async function loadDashboard({ silent = false } = {}) {
    if (silent) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }

    setError("");

    try {
      const [health, fleet, gameserverResponse] = await Promise.all([
        fetchJson("/api/healthz"),
        fetchJson("/api/fleet-status"),
        fetchJson("/api/gameservers"),
      ]);

      setBackendHealthy(health.status === "ok");
      setFleetStatus(fleet);
      setGameservers(gameserverResponse.items || []);
      setLastUpdated(new Date().toLocaleTimeString());
    } catch (err) {
      setError(err.message);
      setBackendHealthy(false);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  async function allocateServer() {
    setAllocating(true);
    setError("");

    try {
      const result = await fetchJson("/api/allocate", { method: "POST" });
      setLatestAllocation(result);
      await loadDashboard({ silent: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setAllocating(false);
    }
  }

  useEffect(() => {
    void loadDashboard();
  }, []);

  return (
    <main className="page">
      <section className="hero">
        <div>
          <p className="eyebrow">Xonotic Operator Console</p>
          <h1>Allocator Admin Dashboard</h1>
          <p className="subtitle">
            Inspect allocator health, review Fleet capacity, and allocate a fresh Xonotic server from the current standby pool.
          </p>
        </div>
        <div className="hero-actions">
          <StatusPill ok={backendHealthy} label={backendHealthy ? "Backend Healthy" : "Backend Unhealthy"} />
          <button className="secondary" onClick={() => void loadDashboard({ silent: true })} disabled={refreshing || loading}>
            {refreshing ? "Refreshing..." : "Refresh"}
          </button>
          <button className="primary" onClick={() => void allocateServer()} disabled={allocating || loading}>
            {allocating ? "Allocating..." : "Allocate Server"}
          </button>
        </div>
      </section>

      {error && <section className="error-banner">Request failed: {error}</section>}

      <section className="grid metrics">
        <MetricCard label="Fleet Desired" value={fleetStatus.desired_replicas} />
        <MetricCard label="Fleet Total" value={fleetStatus.replicas} />
        <MetricCard label="Ready" value={fleetStatus.ready_replicas} />
        <MetricCard label="Allocated" value={fleetStatus.allocated_replicas} />
        <MetricCard label="Reserved" value={fleetStatus.reserved_replicas} />
      </section>

      <section className="grid panels">
        <article className="panel">
          <div className="panel-header">
            <h2>Fleet Summary</h2>
            <span className="panel-meta">{lastUpdated ? `Updated ${lastUpdated}` : "Waiting for first refresh"}</span>
          </div>
          <dl className="summary-list">
            <div>
              <dt>Name</dt>
              <dd>{fleetStatus.name || "xonotic-fleet"}</dd>
            </div>
            <div>
              <dt>Namespace</dt>
              <dd>{fleetStatus.namespace || "xonotic-agones"}</dd>
            </div>
            <div>
              <dt>Standby Buffer</dt>
              <dd>{fleetStatus.ready_replicas} / 3 ready</dd>
            </div>
          </dl>
        </article>

        <article className="panel">
          <div className="panel-header">
            <h2>Latest Allocation</h2>
          </div>
          {latestAllocation ? (
            <dl className="summary-list">
              <div>
                <dt>GameServer</dt>
                <dd>{latestAllocation.allocated_game_server_name}</dd>
              </div>
              <div>
                <dt>Endpoint</dt>
                <dd>
                  {latestAllocation.address}:{latestAllocation.port}
                </dd>
              </div>
              <div>
                <dt>Request Object</dt>
                <dd>{latestAllocation.allocation_request_name || "Inline create response"}</dd>
              </div>
            </dl>
          ) : (
            <p className="empty-state">No server allocated yet in this session.</p>
          )}
        </article>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2>Current GameServers</h2>
          <span className="panel-meta">{gameservers.length} visible</span>
        </div>
        {loading ? (
          <p className="empty-state">Loading dashboard...</p>
        ) : gameservers.length === 0 ? (
          <p className="empty-state">No GameServers returned by the backend.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>State</th>
                  <th>Address</th>
                  <th>Port</th>
                  <th>Node</th>
                </tr>
              </thead>
              <tbody>
                {gameservers.map((server) => (
                  <tr key={server.name}>
                    <td>{server.name}</td>
                    <td>{server.state || "-"}</td>
                    <td>{server.address || "-"}</td>
                    <td>{server.port || "-"}</td>
                    <td>{server.node_name || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}
