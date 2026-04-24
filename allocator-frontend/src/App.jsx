import { useEffect, useState } from "react";

const EMPTY_FLEET = {
  name: "xonotic-fleet",
  desired_replicas: 0,
  replicas: 0,
  ready_replicas: 0,
  allocated_replicas: 0,
  reserved_replicas: 0,
};

const AUTO_REFRESH_MS = 7000;
const HISTORY_LIMIT = 8;

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

function allocationEndpoint(allocation) {
  if (!allocation?.address || !allocation?.port) {
    return "";
  }

  return `${allocation.address}:${allocation.port}`;
}

function connectCommand(endpoint) {
  return endpoint ? `connect ${endpoint}` : "";
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

function serverEndpoint(server) {
  if (server.state !== "Allocated") {
    return "Not user-facing";
  }

  if (!server.address || !server.port) {
    return "Missing endpoint";
  }

  return `${server.address}:${server.port}`;
}

function CopyButton({ text, label, onCopy }) {
  return (
    <button className="copy-button" type="button" onClick={() => void onCopy(text, label)} disabled={!text}>
      {label}
    </button>
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
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [allocationHistory, setAllocationHistory] = useState([]);
  const [copyMessage, setCopyMessage] = useState("");
  const [error, setError] = useState(null);
  const [lastUpdated, setLastUpdated] = useState("");

  async function copyText(text, label) {
    if (!text) {
      return;
    }

    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const textArea = document.createElement("textarea");
        textArea.value = text;
        textArea.style.position = "fixed";
        textArea.style.opacity = "0";
        document.body.appendChild(textArea);
        textArea.select();
        document.execCommand("copy");
        document.body.removeChild(textArea);
      }
      setCopyMessage(`${label} copied`);
    } catch {
      setError({
        title: "Copy failed",
        message: "The browser could not copy this value automatically. Select the endpoint text manually.",
      });
      return;
    }

    window.setTimeout(() => setCopyMessage(""), 1800);
  }

  async function loadDashboard({ silent = false, source = "Refresh" } = {}) {
    if (silent) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }

    setError(null);

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
      setError({
        title: `${source} failed`,
        message: err.message,
      });
      setBackendHealthy(false);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  async function allocateServer() {
    setAllocating(true);
    setError(null);

    try {
      const result = await fetchJson("/api/allocate", { method: "POST" });
      const endpoint = allocationEndpoint(result);
      setLatestAllocation(result);
      setAllocationHistory((current) => [
        {
          ...result,
          endpoint,
          timestamp: new Date().toLocaleTimeString(),
        },
        ...current,
      ].slice(0, HISTORY_LIMIT));
      await loadDashboard({ silent: true });
    } catch (err) {
      setError({
        title: "Allocation failed",
        message: err.message,
      });
    } finally {
      setAllocating(false);
    }
  }

  useEffect(() => {
    void loadDashboard();
  }, []);

  useEffect(() => {
    if (!autoRefresh) {
      return undefined;
    }

    const intervalId = window.setInterval(() => {
      void loadDashboard({ silent: true, source: "Auto-refresh" });
    }, AUTO_REFRESH_MS);

    return () => window.clearInterval(intervalId);
  }, [autoRefresh]);

  const allocatedServers = gameservers.filter((server) => server.state === "Allocated");
  const internalServers = gameservers.filter((server) => server.state !== "Allocated");
  const latestEndpoint = allocationEndpoint(latestAllocation);
  const latestCommand = connectCommand(latestEndpoint);

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
          <button
            className={`secondary ${autoRefresh ? "toggle-active" : ""}`}
            onClick={() => setAutoRefresh((enabled) => !enabled)}
            type="button"
          >
            {autoRefresh ? "Auto-refresh on" : "Auto-refresh off"}
          </button>
          <button className="secondary" onClick={() => void loadDashboard({ silent: true })} disabled={refreshing || loading}>
            {refreshing ? "Refreshing..." : "Refresh"}
          </button>
          <button className="primary" onClick={() => void allocateServer()} disabled={allocating || loading}>
            {allocating ? "Allocating..." : "Allocate Server"}
          </button>
        </div>
      </section>

      {error && (
        <section className="error-banner">
          <strong>{error.title}</strong>
          <span>{error.message}</span>
        </section>
      )}

      {copyMessage && <section className="copy-banner">{copyMessage}</section>}

      <section className="notice">
        Only allocated servers are valid join targets. Ready servers are standby capacity. Auto-refresh checks every 7 seconds when enabled.
      </section>

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
                <dd className="join-endpoint">{latestEndpoint}</dd>
              </div>
              <div>
                <dt>Connection Helper</dt>
                <dd className="connection-command">{latestCommand}</dd>
              </div>
              <div>
                <dt>Request Object</dt>
                <dd>{latestAllocation.allocation_request_name || "Inline create response"}</dd>
              </div>
              <div className="button-row">
                <CopyButton text={latestEndpoint} label="Endpoint" onCopy={copyText} />
                <CopyButton text={latestCommand} label="Command" onCopy={copyText} />
              </div>
            </dl>
          ) : (
            <p className="empty-state">No server allocated yet in this session.</p>
          )}
        </article>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2>Allocated Servers</h2>
          <span className="panel-meta">{allocatedServers.length} join targets</span>
        </div>
        {loading ? (
          <p className="empty-state">Loading allocated servers...</p>
        ) : allocatedServers.length === 0 ? (
          <p className="empty-state">No allocated servers yet. Use Allocate Server to create a user-facing join target.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Join Endpoint</th>
                  <th>Connection Helper</th>
                  <th>Node</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {allocatedServers.map((server) => {
                  const endpoint = serverEndpoint(server);
                  const command = connectCommand(endpoint);

                  return (
                    <tr key={server.name}>
                      <td>{server.name}</td>
                      <td className="join-endpoint">{endpoint}</td>
                      <td className="connection-command">{command}</td>
                      <td>{server.node_name || "-"}</td>
                      <td>
                        <div className="table-actions">
                          <CopyButton text={endpoint} label="Endpoint" onCopy={copyText} />
                          <CopyButton text={command} label="Command" onCopy={copyText} />
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2>Allocation History</h2>
          <span className="panel-meta">{allocationHistory.length} recent successful allocations</span>
        </div>
        {allocationHistory.length === 0 ? (
          <p className="empty-state">Successful allocations will appear here for this browser session.</p>
        ) : (
          <div className="history-list">
            {allocationHistory.map((allocation) => {
              const command = connectCommand(allocation.endpoint);

              return (
                <div className="history-item" key={`${allocation.allocated_game_server_name}-${allocation.timestamp}`}>
                  <div>
                    <strong>{allocation.allocated_game_server_name}</strong>
                    <span>{allocation.timestamp}</span>
                  </div>
                  <code>{command}</code>
                  <CopyButton text={command} label="Copy command" onCopy={copyText} />
                </div>
              );
            })}
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2>Standby / Internal Servers</h2>
          <span className="panel-meta">{internalServers.length} infrastructure servers</span>
        </div>
        {loading ? (
          <p className="empty-state">Loading dashboard...</p>
        ) : internalServers.length === 0 ? (
          <p className="empty-state">No standby or internal servers returned by the backend.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>State</th>
                  <th>Endpoint</th>
                  <th>Node</th>
                </tr>
              </thead>
              <tbody>
                {internalServers.map((server) => (
                  <tr key={server.name}>
                    <td>{server.name}</td>
                    <td>
                      <span className="state-badge">{server.state === "Ready" ? "Standby" : server.state || "Unknown"}</span>
                    </td>
                    <td className="muted-endpoint">Not user-facing</td>
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
