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
  const server = allocation?.allocated_server || allocation;

  if (!server?.address || !server?.port) {
    return "";
  }

  return `${server.address}:${server.port}`;
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

function unknown(value) {
  return value === null || value === undefined || value === "" ? "unknown" : value;
}

export default function App() {
  const [backendHealthy, setBackendHealthy] = useState(false);
  const [fleetStatus, setFleetStatus] = useState(EMPTY_FLEET);
  const [gameservers, setGameservers] = useState([]);
  const [matches, setMatches] = useState([]);
  const [matchForm, setMatchForm] = useState({ name: "", max_players: "8", game_mode: "dm" });
  const [latestAllocation, setLatestAllocation] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [allocating, setAllocating] = useState(false);
  const [creatingMatch, setCreatingMatch] = useState(false);
  const [allocatingMatches, setAllocatingMatches] = useState({});
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
      const [health, fleet, gameserverResponse, matchResponse] = await Promise.all([
        fetchJson("/api/healthz"),
        fetchJson("/api/fleet-status"),
        fetchJson("/api/gameservers"),
        fetchJson("/api/matches"),
      ]);

      setBackendHealthy(health.status === "ok");
      setFleetStatus(fleet);
      setGameservers(gameserverResponse.items || []);
      setMatches(matchResponse.items || []);
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

  async function createMatch(event) {
    event.preventDefault();
    setCreatingMatch(true);
    setError(null);

    try {
      const payload = {
        name: matchForm.name.trim() || undefined,
        max_players: matchForm.max_players ? Number(matchForm.max_players) : undefined,
        game_mode: matchForm.game_mode.trim() || undefined,
      };
      const match = await fetchJson("/api/matches", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      setMatches((current) => [match, ...current]);
      setMatchForm({ name: "", max_players: "8", game_mode: "dm" });
      setLastUpdated(new Date().toLocaleTimeString());
    } catch (err) {
      setError({
        title: "Create match failed",
        message: err.message,
      });
    } finally {
      setCreatingMatch(false);
    }
  }

  async function allocateMatch(matchId) {
    setAllocatingMatches((current) => ({ ...current, [matchId]: true }));
    setError(null);

    try {
      const match = await fetchJson(`/api/matches/${matchId}/allocate`, { method: "POST" });
      setMatches((current) => current.map((item) => (item.match_id === match.match_id ? match : item)));
      await loadDashboard({ silent: true, source: "Match allocation refresh" });
    } catch (err) {
      setError({
        title: "Match allocation failed",
        message: err.message,
      });
    } finally {
      setAllocatingMatches((current) => {
        const next = { ...current };
        delete next[matchId];
        return next;
      });
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
            Create admin-facing Match Rooms, assign Agones-backed Xonotic servers, and keep standby capacity visible without treating it as joinable.
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
        Match Rooms are the admin-facing sessions. Allocated GameServers back those rooms. Ready servers remain standby/internal capacity.
        Auto-refresh checks every 7 seconds when enabled.
      </section>

      <section className="panel match-rooms-panel">
        <div className="panel-header">
          <h2>Match Rooms</h2>
          <span className="panel-meta">{matches.length} in-memory rooms</span>
        </div>

        <form className="match-form" onSubmit={(event) => void createMatch(event)}>
          <label>
            <span>Match name</span>
            <input
              value={matchForm.name}
              onChange={(event) => setMatchForm((current) => ({ ...current, name: event.target.value }))}
              placeholder="Quarterfinal 1"
            />
          </label>
          <label>
            <span>Max players</span>
            <input
              min="1"
              max="64"
              type="number"
              value={matchForm.max_players}
              onChange={(event) => setMatchForm((current) => ({ ...current, max_players: event.target.value }))}
            />
          </label>
          <label>
            <span>Game mode</span>
            <input
              value={matchForm.game_mode}
              onChange={(event) => setMatchForm((current) => ({ ...current, game_mode: event.target.value }))}
              placeholder="dm"
            />
          </label>
          <button className="primary" type="submit" disabled={creatingMatch || loading}>
            {creatingMatch ? "Creating..." : "Create Match Room"}
          </button>
        </form>

        {loading ? (
          <p className="empty-state">Loading match rooms...</p>
        ) : matches.length === 0 ? (
          <p className="empty-state">No Match Rooms yet. Create one first, then allocate a server into it.</p>
        ) : (
          <div className="match-grid">
            {matches.map((match) => {
              const endpoint = allocationEndpoint(match);
              const command = connectCommand(endpoint);
              const isAllocated = Boolean(match.allocated_server);
              const isAllocating = Boolean(allocatingMatches[match.match_id]) || match.status === "allocating";

              return (
                <article className={`match-card ${isAllocated ? "match-card-allocated" : ""}`} key={match.match_id}>
                  <div className="match-card-header">
                    <div>
                      <h3>{match.name}</h3>
                      <p>{match.match_id}</p>
                    </div>
                    <span className="state-badge">{match.status}</span>
                  </div>
                  <dl className="match-details">
                    <div>
                      <dt>Players</dt>
                      <dd>
                        {unknown(match.current_players)} / {match.max_players}
                      </dd>
                    </div>
                    <div>
                      <dt>Mode</dt>
                      <dd>{unknown(match.game_mode)}</dd>
                    </div>
                    <div>
                      <dt>Map</dt>
                      <dd>{unknown(match.map)}</dd>
                    </div>
                    <div>
                      <dt>Created</dt>
                      <dd>{match.created_at}</dd>
                    </div>
                  </dl>

                  {isAllocated ? (
                    <div className="assigned-server">
                      <span>Assigned server</span>
                      <strong className="join-endpoint">{endpoint}</strong>
                      <code>{command}</code>
                      <div className="button-row">
                        <CopyButton text={endpoint} label="Endpoint" onCopy={copyText} />
                        <CopyButton text={command} label="Command" onCopy={copyText} />
                      </div>
                    </div>
                  ) : (
                    <button className="primary" type="button" onClick={() => void allocateMatch(match.match_id)} disabled={isAllocating}>
                      {isAllocating ? "Allocating..." : "Allocate Server"}
                    </button>
                  )}
                </article>
              );
            })}
          </div>
        )}
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
            <h2>Manual Direct Allocation</h2>
            <span className="panel-meta">debug path</span>
          </div>
          <p className="empty-state debug-copy">
            Use this only for lower-level allocator testing. Normal operator flow should allocate servers through Match Rooms.
          </p>
          <button className="secondary debug-action" onClick={() => void allocateServer()} disabled={allocating || loading}>
            {allocating ? "Allocating..." : "Allocate Direct Server"}
          </button>
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
          <span className="panel-meta">{allocatedServers.length} infrastructure allocations</span>
        </div>
        {loading ? (
          <p className="empty-state">Loading allocated servers...</p>
        ) : allocatedServers.length === 0 ? (
          <p className="empty-state">No allocated GameServers yet. Match Room allocation will assign one from standby capacity.</p>
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
          <h2>Manual Allocation History</h2>
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
