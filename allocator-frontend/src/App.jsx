import { Fragment, useEffect, useRef, useState } from "react";

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
const ADMIN_MAPS = ["xoylent", "stormkeep", "implosion", "drain", "darkzone", "solarium"];
const BROADCAST_MAX_LENGTH = 160;
const BULK_ALLOCATION_READY_POLL_MS = 5000;
const BULK_ALLOCATION_READY_TIMEOUT_MS = 120000;
const BULK_ALLOCATION_REQUEST_RETRY_LIMIT = 4;
const BULK_ALLOCATION_BACKEND_RETRY_MS = 5000;
const VERIFIED_CONFIG_NOTE = "Only verified map/mode combinations are shown.";
const SUPPORTED_BRACKET_SIZES = [2, 4, 8];
const FALLBACK_GAME_CONFIG_OPTIONS = {
  default: {
    requested_game_mode: "dm",
    requested_map: "xoylent",
  },
  supported_modes: ["dm", "tdm", "ctf", "duel", "ca"],
  valid_maps_by_mode: {
    dm: ["xoylent", "stormkeep", "solarium"],
    tdm: ["stormkeep"],
    ctf: ["runningmanctf"],
    duel: ["xoylent"],
    ca: ["stormkeep", "xoylent"],
  },
  modes: [
    {
      mode: "dm",
      label: "Deathmatch",
      selectable: true,
      verified_maps: ["xoylent", "stormkeep", "solarium"],
      experimental_maps: ["drain", "darkzone", "runningman", "warfare"],
    },
    {
      mode: "tdm",
      label: "Team Deathmatch",
      selectable: true,
      verified_maps: ["stormkeep"],
      experimental_maps: ["xoylent", "solarium", "darkzone", "implosion", "runningman", "silentsiege"],
    },
    {
      mode: "ctf",
      label: "Capture The Flag",
      selectable: true,
      verified_maps: ["runningmanctf"],
      experimental_maps: ["catharsis", "courtfun", "dance", "go", "implosion", "space-elevator", "vorix"],
    },
    {
      mode: "duel",
      label: "Duel",
      selectable: true,
      verified_maps: ["xoylent"],
      experimental_maps: ["darkzone", "fuse", "stormkeep", "warfare"],
    },
    {
      mode: "ca",
      label: "Clan Arena",
      selectable: true,
      verified_maps: ["stormkeep", "xoylent"],
      experimental_maps: ["darkzone", "implosion", "runningman", "solarium"],
    },
    {
      mode: "dom",
      label: "Domination",
      selectable: false,
      verified_maps: [],
      experimental_maps: ["afterslime", "geoplanetary", "glowplant", "implosion", "runningmanctf", "stormkeep"],
      disabled_reason: "deferred until Domination map/mode combinations are verified",
    },
    {
      mode: "kh",
      label: "Key Hunt",
      selectable: false,
      verified_maps: [],
      experimental_maps: ["implosion", "runningman", "runningmanctf", "solarium", "stormkeep"],
      disabled_reason: "deferred until Key Hunt map/mode combinations are verified",
    },
  ],
  experimental_probe_enabled: false,
  note: VERIFIED_CONFIG_NOTE,
};

class ApiError extends Error {
  constructor(message, { status = 0, data = null, path = "" } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
    this.path = path;
  }
}

async function fetchJson(path, options) {
  let response;
  try {
    response = await fetch(path, options);
  } catch (err) {
    const message = err?.message || "Network request failed";
    throw new ApiError(message, {
      status: 0,
      data: { error: "network_error", message },
      path,
    });
  }

  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : null;

  if (!response.ok) {
    const message = data?.message || `${response.status} ${response.statusText}`;
    throw new ApiError(message, { status: response.status, data, path });
  }

  return data;
}

function sleep(ms) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function isRetryableAllocationError(err) {
  if (err?.data?.error === "no_ready_servers") {
    return true;
  }

  if ([502, 503, 504].includes(err?.status)) {
    return true;
  }

  const message = String(err?.message || "").toLowerCase();
  return message.includes("bad gateway")
    || message.includes("failed to fetch")
    || message.includes("load failed")
    || message.includes("networkerror")
    || message.includes("timed out");
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

function playerScore(player) {
  if (player.scores && Object.keys(player.scores).length > 0) {
    return Object.entries(player.scores)
      .map(([label, value]) => `${label}: ${value}`)
      .join(", ");
  }

  return player.score ?? player.score_raw ?? "unknown";
}

function liveStatusLabel(liveStatus) {
  if (!liveStatus) {
    return "live status pending";
  }

  return liveStatus.ok ? `live ${liveStatus.queried_at}` : "live status unavailable";
}

function requestedConfigDiffers(match) {
  const mapDiffers = match.requested_map && match.map && match.requested_map !== match.map;
  const modeDiffers = match.requested_game_mode && match.game_mode && match.requested_game_mode !== match.game_mode;
  return Boolean(mapDiffers || modeDiffers);
}

function shortId(id) {
  return id ? id.slice(0, 8) : "unknown";
}

function numericValue(value, fallback = Number.MAX_SAFE_INTEGER) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function compareByBracketOrder(left, right) {
  const leftPosition = numericValue(left.bracket_position);
  const rightPosition = numericValue(right.bracket_position);
  if (leftPosition !== rightPosition) {
    return leftPosition - rightPosition;
  }

  return String(left.created_at || left.id).localeCompare(String(right.created_at || right.id));
}

function bracketGenerationBlocker(teams, rounds, matches) {
  if (rounds.length > 0 || matches.length > 0) {
    return "Bracket generation requires a tournament with no rounds or matches.";
  }

  if (!SUPPORTED_BRACKET_SIZES.includes(teams.length)) {
    return "Add exactly 2, 4, or 8 teams before generating a bracket.";
  }

  const expectedSeeds = Array.from({ length: teams.length }, (_item, index) => index + 1);
  const actualSeeds = teams.map((team) => Number(team.seed)).filter((seed) => Number.isInteger(seed)).sort((left, right) => left - right);
  const seedsAreComplete = actualSeeds.length === expectedSeeds.length
    && actualSeeds.every((seed, index) => seed === expectedSeeds[index]);

  if (!seedsAreComplete) {
    return `Set unique seeds 1 through ${teams.length} before generating a bracket.`;
  }

  return "";
}

function bracketRoundColumns(rounds, matches) {
  const matchesByRoundId = matches.reduce((groups, match) => {
    const key = match.round_id || "unassigned";
    return {
      ...groups,
      [key]: [...(groups[key] || []), match],
    };
  }, {});

  return [...rounds]
    .sort((left, right) => numericValue(left.round_order) - numericValue(right.round_order))
    .map((round) => ({
      round,
      matches: [...(matchesByRoundId[round.id] || [])].sort(compareByBracketOrder),
    }))
    .filter((column) => column.matches.length > 0);
}

function tournamentRoundColumns(rounds, matches) {
  const knownRoundIds = new Set(rounds.map((round) => round.id));
  const columns = [...rounds]
    .sort((left, right) => numericValue(left.round_order) - numericValue(right.round_order))
    .map((round) => ({
      round,
      matches: matches
        .filter((match) => match.round_id === round.id)
        .sort(compareByBracketOrder),
    }))
    .filter((column) => column.matches.length > 0);

  const unassignedMatches = matches
    .filter((match) => !match.round_id || !knownRoundIds.has(match.round_id))
    .sort(compareByBracketOrder);

  if (unassignedMatches.length > 0) {
    columns.push({
      round: {
        id: "unassigned",
        name: "Unassigned Round",
        round_order: "-",
      },
      matches: unassignedMatches,
    });
  }

  return columns;
}

function assignmentEndpoint(assignment) {
  if (!assignment) {
    return "";
  }

  return assignment.endpoint || (assignment.address && assignment.port ? `${assignment.address}:${assignment.port}` : "");
}

const TOURNAMENT_MATCH_STATUS_LABELS = {
  created: "Created",
  scheduled: "Scheduled",
  server_allocating: "Allocating",
  server_ready: "Server Ready",
  running: "Running",
  finished: "Finished",
  released: "Released",
  failed: "Failed",
};

const TERMINAL_TOURNAMENT_MATCH_STATUSES = new Set(["finished", "released"]);
const REGENERABLE_TOURNAMENT_MATCH_STATUSES = new Set(["created", "scheduled"]);
const SERVER_HISTORY_TOURNAMENT_MATCH_STATUSES = new Set(["server_allocating", "server_ready", "failed", "released"]);

function tournamentMatchStatusLabel(status) {
  return TOURNAMENT_MATCH_STATUS_LABELS[status] || status || "Unknown";
}

function tournamentMatchStatusClass(status) {
  const normalizedStatus = (status || "unknown").replace(/[^a-z0-9_-]/gi, "-").toLowerCase();
  return `state-badge state-badge-${normalizedStatus}`;
}

function tournamentMatchHasResult(match) {
  return match.team_a_score !== null && match.team_a_score !== undefined
    && match.team_b_score !== null && match.team_b_score !== undefined
    && Boolean(match.winner_team_id);
}

function tournamentMatchCanShowResultForm(match) {
  return !tournamentMatchHasResult(match)
    && !TERMINAL_TOURNAMENT_MATCH_STATUSES.has(match.status)
    && match.status !== "server_allocating";
}

function tournamentMatchCanRecordResult(match) {
  return tournamentMatchCanShowResultForm(match) && Boolean(match.team_a_id && match.team_b_id);
}

function tournamentMatchCanAllocateServer(match) {
  const status = match.status || "created";
  return !match.active_server_assignment
    && !TERMINAL_TOURNAMENT_MATCH_STATUSES.has(status)
    && status !== "server_allocating";
}

function isGeneratedBracketMatch(match) {
  return match.bracket_position !== null && match.bracket_position !== undefined;
}

function tournamentMatchCanBulkAllocate(match) {
  return isGeneratedBracketMatch(match)
    && Boolean(match.team_a_id && match.team_b_id)
    && !tournamentMatchHasResult(match)
    && tournamentMatchCanAllocateServer(match);
}

function bracketRegenerationBlocker(tournament, matches) {
  if (!tournament) {
    return "Select a tournament first.";
  }

  if (["completed", "finished"].includes(tournament.status)) {
    return "Completed tournaments cannot regenerate brackets.";
  }

  if (matches.some((match) => tournamentMatchHasResult(match) || match.status === "finished")) {
    return "Bracket regeneration is blocked after a match result is recorded.";
  }

  if (matches.some((match) => match.active_server_assignment || SERVER_HISTORY_TOURNAMENT_MATCH_STATUSES.has(match.status || "created"))) {
    return "Bracket regeneration is blocked after a server has been allocated.";
  }

  if (matches.some((match) => !REGENERABLE_TOURNAMENT_MATCH_STATUSES.has(match.status || "created"))) {
    return "Bracket regeneration is only available before matches start.";
  }

  return "";
}

function formatTimestamp(value) {
  if (!value) {
    return "not set";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleString();
}

function humanizeIdentifier(value) {
  return (value || "manual")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function gameModeLabel(gameConfigOptions, modeName) {
  if (!modeName) {
    return "mode unset";
  }

  const mode = (gameConfigOptions.modes || []).find((candidate) => candidate.mode === modeName);
  return mode?.label || humanizeIdentifier(modeName);
}

function selectableGameModes(gameConfigOptions) {
  const validMapsByMode = gameConfigOptions.valid_maps_by_mode || {};
  const modesByName = new Map();

  (gameConfigOptions.modes || []).forEach((mode) => {
    const verifiedMaps = validMapsByMode[mode.mode] || mode.verified_maps || [];
    if (mode.selectable && verifiedMaps.length > 0) {
      modesByName.set(mode.mode, {
        ...mode,
        verified_maps: verifiedMaps,
      });
    }
  });

  Object.entries(validMapsByMode).forEach(([modeName, maps]) => {
    if (maps.length > 0 && !modesByName.has(modeName)) {
      modesByName.set(modeName, {
        mode: modeName,
        label: modeName,
        selectable: true,
        verified_maps: maps,
      });
    }
  });

  return Array.from(modesByName.values());
}

function defaultGameConfig(gameConfigOptions = FALLBACK_GAME_CONFIG_OPTIONS) {
  return gameConfigOptions.default || FALLBACK_GAME_CONFIG_OPTIONS.default;
}

function validMapsForMode(gameConfigOptions, modeName) {
  if (!modeName) {
    return [];
  }

  const mode = selectableGameModes(gameConfigOptions).find((candidate) => candidate.mode === modeName);
  return mode ? mode.verified_maps : [];
}

function normalizeGameConfig(values, gameConfigOptions = FALLBACK_GAME_CONFIG_OPTIONS) {
  const fallback = defaultGameConfig(gameConfigOptions);
  const firstMode = selectableGameModes(gameConfigOptions)[0];
  const mode = validMapsForMode(gameConfigOptions, values?.requested_game_mode).length > 0
    ? values.requested_game_mode
    : firstMode?.mode || fallback.requested_game_mode;
  const maps = validMapsForMode(gameConfigOptions, mode);
  const map = maps.includes(values?.requested_map) ? values.requested_map : maps[0] || fallback.requested_map;

  return {
    requested_game_mode: mode,
    requested_map: map,
  };
}

function emptyMatchRoomForm(gameConfigOptions = FALLBACK_GAME_CONFIG_OPTIONS) {
  return {
    name: "",
    ...normalizeGameConfig({}, gameConfigOptions),
  };
}

function emptyTournamentMatchForm(gameConfigOptions = FALLBACK_GAME_CONFIG_OPTIONS) {
  return {
    name: "",
    round_id: "",
    team_a_id: "",
    team_b_id: "",
    ...normalizeGameConfig({}, gameConfigOptions),
  };
}

function PlayerTournamentView({
  tournaments,
  selectedTournamentId,
  selectedTournament,
  tournamentLoading,
  tournamentDetailLoading,
  tournamentRounds,
  tournamentMatches,
  selectedTournamentSummary,
  teamNameById,
  gameConfigOptions,
  onSelectTournament,
  onRefreshTournaments,
  onCopy,
}) {
  const roundColumns = tournamentRoundColumns(tournamentRounds, tournamentMatches);
  const summaryCounts = selectedTournamentSummary?.counts || {};
  const championName = selectedTournamentSummary?.winner_team?.name || selectedTournamentSummary?.champion_team?.name || "";

  function teamName(teamId) {
    return teamId ? teamNameById[teamId] || `Team ${shortId(teamId)}` : "TBD";
  }

  return (
    <section className="player-view">
      <aside className="player-tournament-rail">
        <div className="player-section-header">
          <div>
            <p className="eyebrow">Tournament</p>
            <h2>Choose Event</h2>
          </div>
          <button className="copy-button" type="button" onClick={onRefreshTournaments} disabled={tournamentLoading}>
            {tournamentLoading ? "Loading..." : "Refresh"}
          </button>
        </div>

        {tournamentLoading ? (
          <p className="empty-state">Loading tournaments...</p>
        ) : tournaments.length === 0 ? (
          <p className="empty-state">No tournaments are published yet.</p>
        ) : (
          <div className="player-tournament-list">
            {tournaments.map((tournament) => (
              <button
                className={`player-tournament-option ${tournament.id === selectedTournamentId ? "player-tournament-option-active" : ""}`}
                key={tournament.id}
                type="button"
                onClick={() => onSelectTournament(tournament.id)}
              >
                <strong>{tournament.name}</strong>
                <span>{humanizeIdentifier(tournament.status)} · {shortId(tournament.id)}</span>
              </button>
            ))}
          </div>
        )}
      </aside>

      <section className="player-main">
        {!selectedTournament ? (
          <div className="player-empty-hero">
            <p className="eyebrow">Player View</p>
            <h2>Select a tournament</h2>
            <p>Published matches, scores, winners, and live server endpoints will appear here.</p>
          </div>
        ) : (
          <>
            <article className="player-tournament-hero">
              <div>
                <p className="eyebrow">Player Tournament View</p>
                <h2>{selectedTournament.name}</h2>
                <p>{selectedTournament.description || "Match information and join targets for players and spectators."}</p>
              </div>
              <div className="player-hero-status">
                <span className="state-badge">{humanizeIdentifier(selectedTournament.status)}</span>
                <strong>{championName || "Champion pending"}</strong>
                <span>{selectedTournamentSummary?.completed_at ? `Completed ${formatTimestamp(selectedTournamentSummary.completed_at)}` : "Live bracket"}</span>
              </div>
            </article>

            <dl className="player-summary-grid">
              <div>
                <dt>Champion</dt>
                <dd>{championName || "pending"}</dd>
              </div>
              <div>
                <dt>Status</dt>
                <dd>{humanizeIdentifier(selectedTournament.status)}</dd>
              </div>
              <div>
                <dt>Progress</dt>
                <dd>{summaryCounts.finished_matches || 0}/{summaryCounts.matches || tournamentMatches.length} matches</dd>
              </div>
              <div>
                <dt>Active Servers</dt>
                <dd>{tournamentMatches.filter((match) => match.active_server_assignment).length}</dd>
              </div>
            </dl>

            {tournamentDetailLoading ? (
              <p className="empty-state">Loading match list...</p>
            ) : roundColumns.length === 0 ? (
              <p className="empty-state">No matches have been published for this tournament yet.</p>
            ) : (
              <div className="player-round-grid">
                {roundColumns.map(({ round, matches: roundMatches }) => (
                  <section className="player-round-column" key={round.id}>
                    <div className="player-round-header">
                      <div>
                        <h3>{round.name}</h3>
                        <span>{roundMatches.length} match{roundMatches.length === 1 ? "" : "es"}</span>
                      </div>
                    </div>
                    <div className="player-match-stack">
                      {roundMatches.map((match) => {
                        const activeAssignment = match.active_server_assignment;
                        const endpoint = assignmentEndpoint(activeAssignment);
                        const command = connectCommand(endpoint);
                        const status = match.status || "created";
                        const hasRecordedResult = tournamentMatchHasResult(match);
                        const winnerName = match.winner_team_id ? teamName(match.winner_team_id) : "";
                        const teamAName = teamName(match.team_a_id);
                        const teamBName = teamName(match.team_b_id);
                        const matchLabel = match.name || (match.bracket_position ? `Match ${match.bracket_position}` : `Match ${shortId(match.id)}`);
                        const isReleased = status === "released";

                        return (
                          <article className={`player-match-card ${isReleased ? "player-match-card-released" : ""}`} key={match.id}>
                            <div className="player-match-header">
                              <strong>{matchLabel}</strong>
                              <span className={tournamentMatchStatusClass(status)}>{tournamentMatchStatusLabel(status)}</span>
                            </div>

                            <div className="player-versus">
                              <span className={`player-team ${match.winner_team_id === match.team_a_id ? "player-team-winner" : ""}`}>{teamAName}</span>
                              <span>vs</span>
                              <span className={`player-team ${match.winner_team_id === match.team_b_id ? "player-team-winner" : ""}`}>{teamBName}</span>
                            </div>

                            <dl className="player-match-details">
                              <div>
                                <dt>Map / Mode</dt>
                                <dd>{match.requested_map || "map unset"} / {gameModeLabel(gameConfigOptions, match.requested_game_mode)}</dd>
                              </div>
                              <div>
                                <dt>Result</dt>
                                <dd>
                                  {hasRecordedResult ? (
                                    <span className="player-score">{match.team_a_score} - {match.team_b_score}{winnerName ? ` · Winner: ${winnerName}` : ""}</span>
                                  ) : (
                                    <span className="muted-endpoint">Not recorded</span>
                                  )}
                                </dd>
                              </div>
                            </dl>

                            {endpoint ? (
                              <div className="player-connect-box">
                                <span>Active server</span>
                                <strong>{endpoint}</strong>
                                <code>{command}</code>
                                <CopyButton text={command} label="Copy connect" onCopy={onCopy} />
                              </div>
                            ) : (
                              <p className="player-server-muted">{isReleased ? "Server released." : "No active server assigned."}</p>
                            )}

                            {match.result_notes && <p className="player-note">{match.result_notes}</p>}
                          </article>
                        );
                      })}
                    </div>
                  </section>
                ))}
              </div>
            )}
          </>
        )}
      </section>
    </section>
  );
}

export default function App() {
  const [backendHealthy, setBackendHealthy] = useState(false);
  const [fleetStatus, setFleetStatus] = useState(EMPTY_FLEET);
  const [gameservers, setGameservers] = useState([]);
  const [matches, setMatches] = useState([]);
  const [gameConfigOptions, setGameConfigOptions] = useState(FALLBACK_GAME_CONFIG_OPTIONS);
  const [matchForm, setMatchForm] = useState(emptyMatchRoomForm());
  const [latestAllocation, setLatestAllocation] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [allocating, setAllocating] = useState(false);
  const [creatingMatch, setCreatingMatch] = useState(false);
  const [allocatingMatches, setAllocatingMatches] = useState({});
  const [releasingMatches, setReleasingMatches] = useState({});
  const [adminActions, setAdminActions] = useState({});
  const [adminFeedback, setAdminFeedback] = useState({});
  const [broadcastForms, setBroadcastForms] = useState({});
  const [changeMapForms, setChangeMapForms] = useState({});
  const [matchConfigForms, setMatchConfigForms] = useState({});
  const [commandPanelServerName, setCommandPanelServerName] = useState("");
  const [terminatingServers, setTerminatingServers] = useState({});
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [allocationHistory, setAllocationHistory] = useState([]);
  const [copyMessage, setCopyMessage] = useState("");
  const [error, setError] = useState(null);
  const [lastUpdated, setLastUpdated] = useState("");
  const [viewMode, setViewMode] = useState("admin");
  const [tournaments, setTournaments] = useState([]);
  const [selectedTournamentId, setSelectedTournamentId] = useState("");
  const [tournamentTeams, setTournamentTeams] = useState([]);
  const [tournamentRounds, setTournamentRounds] = useState([]);
  const [tournamentMatches, setTournamentMatches] = useState([]);
  const [tournamentSummaryData, setTournamentSummaryData] = useState(null);
  const [tournamentLoading, setTournamentLoading] = useState(false);
  const [tournamentDetailLoading, setTournamentDetailLoading] = useState(false);
  const [tournamentError, setTournamentError] = useState(null);
  const [creatingTournament, setCreatingTournament] = useState(false);
  const [creatingTeam, setCreatingTeam] = useState(false);
  const [creatingRound, setCreatingRound] = useState(false);
  const [generatingBracket, setGeneratingBracket] = useState(false);
  const [finalizingTournament, setFinalizingTournament] = useState(false);
  const [releasingAllTournamentServers, setReleasingAllTournamentServers] = useState(false);
  const [creatingTournamentMatch, setCreatingTournamentMatch] = useState(false);
  const [allocatingPlayableMatches, setAllocatingPlayableMatches] = useState(false);
  const [bulkAllocationProgress, setBulkAllocationProgress] = useState("");
  const [allocatingTournamentServers, setAllocatingTournamentServers] = useState({});
  const [releasingTournamentServers, setReleasingTournamentServers] = useState({});
  const [recordingTournamentResults, setRecordingTournamentResults] = useState({});
  const [tournamentResultForms, setTournamentResultForms] = useState({});
  const [tournamentForm, setTournamentForm] = useState({ name: "", description: "" });
  const [teamForm, setTeamForm] = useState({ name: "", tag: "", seed: "" });
  const [roundForm, setRoundForm] = useState({ name: "", round_order: "" });
  const [tournamentMatchForm, setTournamentMatchForm] = useState(emptyTournamentMatchForm());
  const bulkAllocationActiveRef = useRef(false);

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

  function modeOptions() {
    return selectableGameModes(gameConfigOptions);
  }

  function mapsForSelectedMode(modeName) {
    return validMapsForMode(gameConfigOptions, modeName);
  }

  function setMatchRoomMode(modeName) {
    setMatchForm((current) => ({
      ...current,
      ...normalizeGameConfig({ ...current, requested_game_mode: modeName }, gameConfigOptions),
    }));
  }

  function setTournamentMatchMode(modeName) {
    setTournamentMatchForm((current) => ({
      ...current,
      ...normalizeGameConfig({ ...current, requested_game_mode: modeName }, gameConfigOptions),
    }));
  }

  async function loadGameConfigOptions() {
    try {
      const options = await fetchJson("/api/game-config/options");
      setGameConfigOptions(options);
      setMatchForm((current) => ({
        ...current,
        ...normalizeGameConfig(current, options),
      }));
      setTournamentMatchForm((current) => ({
        ...current,
        ...normalizeGameConfig(current, options),
      }));
      setMatchConfigForms((current) => Object.fromEntries(
        Object.entries(current).map(([matchId, config]) => [
          matchId,
          {
            ...config,
            ...normalizeGameConfig(config, options),
          },
        ])
      ));
    } catch (err) {
      setError({
        title: "Game config options failed",
        message: err.message,
      });
    }
  }

  async function loadDashboard({ silent = false, source = "Refresh", suppressError = false } = {}) {
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
      if (!suppressError && !(bulkAllocationActiveRef.current && silent)) {
        setError({
          title: `${source} failed`,
          message: err.message,
        });
        setBackendHealthy(false);
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  async function waitForReadyServerCapacity({ matchNumber, totalMatches }) {
    const startedAt = Date.now();

    while (Date.now() - startedAt < BULK_ALLOCATION_READY_TIMEOUT_MS) {
      let fleet = null;
      try {
        fleet = await fetchJson("/api/fleet-status");
        setFleetStatus(fleet);
      } catch (err) {
        if (!isRetryableAllocationError(err)) {
          throw err;
        }

        setBulkAllocationProgress(`Backend unavailable; retrying ${matchNumber} / ${totalMatches}`);
        await sleep(BULK_ALLOCATION_BACKEND_RETRY_MS);
        continue;
      }

      if ((fleet.ready_replicas || 0) > 0) {
        return fleet;
      }

      setBulkAllocationProgress(`Waiting for ready server ${matchNumber} / ${totalMatches}`);
      await sleep(BULK_ALLOCATION_READY_POLL_MS);
    }

    throw new Error("Timed out waiting for a Ready server. FleetAutoscaler may still be replenishing capacity.");
  }

  async function loadTournaments({ silent = false } = {}) {
    if (!silent) {
      setTournamentLoading(true);
    }
    setTournamentError(null);

    try {
      const response = await fetchJson("/api/tournaments");
      const items = response.items || [];
      setTournaments(items);
      setSelectedTournamentId((current) => {
        if (current && items.some((tournament) => tournament.id === current)) {
          return current;
        }
        return "";
      });
    } catch (err) {
      setTournamentError({
        title: "Tournament refresh failed",
        message: err.message,
      });
    } finally {
      setTournamentLoading(false);
    }
  }

  async function loadTournamentDetails(tournamentId, { silent = false } = {}) {
    if (!tournamentId) {
      setTournamentTeams([]);
      setTournamentRounds([]);
      setTournamentMatches([]);
      setTournamentSummaryData(null);
      return;
    }

    if (!silent) {
      setTournamentDetailLoading(true);
    }
    setTournamentError(null);

    try {
      const detailRequests = [
        {
          label: "Teams",
          request: fetchJson(`/api/tournaments/${tournamentId}/teams`),
          apply: (response) => setTournamentTeams(response.items || []),
        },
        {
          label: "Rounds",
          request: fetchJson(`/api/tournaments/${tournamentId}/rounds`),
          apply: (response) => setTournamentRounds(response.items || []),
        },
        {
          label: "Matches",
          request: fetchJson(`/api/tournaments/${tournamentId}/matches`),
          apply: (response) => setTournamentMatches(response.items || []),
        },
        {
          label: "Summary",
          request: fetchJson(`/api/tournaments/${tournamentId}/summary`),
          apply: (response) => setTournamentSummaryData(response),
        },
      ];
      const results = await Promise.allSettled(detailRequests.map((item) => item.request));
      const failures = [];

      results.forEach((result, index) => {
        const detail = detailRequests[index];
        if (result.status === "fulfilled") {
          detail.apply(result.value);
        } else {
          failures.push(`${detail.label}: ${result.reason?.message || "load failed"}`);
        }
      });

      if (failures.length > 0) {
        setTournamentError({
          title: "Tournament detail refresh partially failed",
          message: failures.join(" "),
        });
      }
    } catch (err) {
      setTournamentError({
        title: "Tournament detail refresh failed",
        message: err.message,
      });
    } finally {
      setTournamentDetailLoading(false);
    }
  }

  async function refreshTournamentMatchesSnapshot(tournamentId) {
    const response = await fetchJson(`/api/tournaments/${tournamentId}/matches`);
    const items = response.items || [];
    setTournamentMatches(items);
    return items;
  }

  async function createTournament(event) {
    event.preventDefault();
    setCreatingTournament(true);
    setTournamentError(null);

    try {
      const tournament = await fetchJson("/api/tournaments", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          name: tournamentForm.name.trim(),
          description: tournamentForm.description.trim() || undefined,
        }),
      });
      setTournamentForm({ name: "", description: "" });
      setTournaments((current) => [tournament, ...current]);
      setSelectedTournamentId(tournament.id);
      setTournamentTeams([]);
      setTournamentRounds([]);
      setTournamentMatches([]);
      setTournamentSummaryData(null);
      setTeamForm({ name: "", tag: "", seed: "" });
      setRoundForm({ name: "", round_order: "" });
      setTournamentMatchForm(emptyTournamentMatchForm(gameConfigOptions));
      await loadTournamentDetails(tournament.id, { silent: true });
      setLastUpdated(new Date().toLocaleTimeString());
    } catch (err) {
      setTournamentError({
        title: "Create tournament failed",
        message: err.message,
      });
    } finally {
      setCreatingTournament(false);
    }
  }

  async function createTournamentTeam(event) {
    event.preventDefault();
    if (!selectedTournamentId) {
      return;
    }

    setCreatingTeam(true);
    setTournamentError(null);

    try {
      await fetchJson(`/api/tournaments/${selectedTournamentId}/teams`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          name: teamForm.name.trim(),
          tag: teamForm.tag.trim() || undefined,
          seed: teamForm.seed ? Number(teamForm.seed) : undefined,
        }),
      });
      setTeamForm({ name: "", tag: "", seed: "" });
      await loadTournamentDetails(selectedTournamentId, { silent: true });
    } catch (err) {
      setTournamentError({
        title: "Create team failed",
        message: err.message,
      });
    } finally {
      setCreatingTeam(false);
    }
  }

  async function createTournamentRound(event) {
    event.preventDefault();
    if (!selectedTournamentId) {
      return;
    }

    setCreatingRound(true);
    setTournamentError(null);

    try {
      await fetchJson(`/api/tournaments/${selectedTournamentId}/rounds`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          name: roundForm.name.trim(),
          round_order: roundForm.round_order ? Number(roundForm.round_order) : undefined,
        }),
      });
      setRoundForm({ name: "", round_order: "" });
      await loadTournamentDetails(selectedTournamentId, { silent: true });
    } catch (err) {
      setTournamentError({
        title: "Create round failed",
        message: err.message,
      });
    } finally {
      setCreatingRound(false);
    }
  }

  async function generateTournamentBracket({ replaceExisting = false } = {}) {
    if (!selectedTournamentId) {
      return;
    }

    const blocker = replaceExisting
      ? bracketRegenerationBlocker(selectedTournament, tournamentMatches)
      : bracketGenerationBlocker(tournamentTeams, tournamentRounds, tournamentMatches);
    if (blocker) {
      setTournamentError({
        title: replaceExisting ? "Regenerate bracket unavailable" : "Generate bracket unavailable",
        message: blocker,
      });
      return;
    }

    if (replaceExisting) {
      const confirmed = window.confirm("Regenerate this bracket? Current rounds and matches will be replaced. This is only allowed before server allocation or result recording.");
      if (!confirmed) {
        return;
      }
    }

    setGeneratingBracket(true);
    setTournamentError(null);

    try {
      const config = normalizeGameConfig(tournamentMatchForm, gameConfigOptions);
      const result = await fetchJson(`/api/tournaments/${selectedTournamentId}/bracket/generate`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          ...config,
          replace_existing: replaceExisting || undefined,
        }),
      });
      if (result.tournament) {
        setTournaments((current) => current.map((tournament) => (
          tournament.id === result.tournament.id ? result.tournament : tournament
        )));
      }
      setTournamentRounds(result.rounds || []);
      setTournamentMatches(result.matches || []);
      await loadTournamentDetails(selectedTournamentId, { silent: true });
      setCopyMessage(`${result.tournament?.bracket_size || tournamentTeams.length}-team bracket ${replaceExisting ? "regenerated" : "generated"}.`);
      window.setTimeout(() => setCopyMessage(""), 2400);
      setLastUpdated(new Date().toLocaleTimeString());
    } catch (err) {
      setTournamentError({
        title: replaceExisting ? "Regenerate bracket failed" : "Generate bracket failed",
        message: err.message,
      });
    } finally {
      setGeneratingBracket(false);
    }
  }

  async function finalizeTournament() {
    if (!selectedTournamentId || !tournamentSummaryData?.can_finalize) {
      return;
    }

    const championName = tournamentSummaryData.champion_team?.name || "the recorded winner";
    const confirmed = window.confirm(`Finalize this tournament with ${championName} as winner? Match results will remain visible and server release stays separate.`);
    if (!confirmed) {
      return;
    }

    setFinalizingTournament(true);
    setTournamentError(null);

    try {
      const result = await fetchJson(`/api/tournaments/${selectedTournamentId}/finalize`, { method: "POST" });
      if (result.tournament) {
        setTournaments((current) => current.map((tournament) => (
          tournament.id === result.tournament.id ? result.tournament : tournament
        )));
      }
      if (result.summary) {
        setTournamentSummaryData(result.summary);
      }
      setCopyMessage(`${result.summary?.winner_team?.name || championName} finalized as tournament winner.`);
      window.setTimeout(() => setCopyMessage(""), 2400);
      setLastUpdated(new Date().toLocaleTimeString());
    } catch (err) {
      setTournamentError({
        title: "Finalize tournament failed",
        message: err.message,
      });
    } finally {
      setFinalizingTournament(false);
    }
  }

  function tournamentMatchPayload() {
    return {
      name: tournamentMatchForm.name.trim() || undefined,
      round_id: tournamentMatchForm.round_id || undefined,
      team_a_id: tournamentMatchForm.team_a_id || undefined,
      team_b_id: tournamentMatchForm.team_b_id || undefined,
      requested_map: tournamentMatchForm.requested_map,
      requested_game_mode: tournamentMatchForm.requested_game_mode,
    };
  }

  async function createTournamentMatch({ allocate = false } = {}) {
    if (!selectedTournamentId) {
      return;
    }

    setCreatingTournamentMatch(true);
    setTournamentError(null);

    try {
      const match = await fetchJson(`/api/tournaments/${selectedTournamentId}/matches`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(tournamentMatchPayload()),
      });
      let endpoint = "";
      if (allocate) {
        const allocationResult = await fetchJson(`/api/tournaments/${selectedTournamentId}/matches/${match.id}/allocate-server`, {
          method: "POST",
        });
        endpoint = assignmentEndpoint(allocationResult.assignment);
        if (allocationResult.warning || allocationResult.configuration?.verified === false) {
          throw new Error(allocationResult.warning || allocationResult.configuration?.message || "Server allocation finished, but requested map/mode verification failed.");
        }
      }
      setTournamentMatchForm(emptyTournamentMatchForm(gameConfigOptions));
      await loadTournamentDetails(selectedTournamentId, { silent: true });
      await loadDashboard({ silent: true, source: "Tournament match refresh" });
      setCopyMessage(allocate && endpoint ? `Tournament match created and server assigned: ${endpoint}` : "Tournament match created.");
      window.setTimeout(() => setCopyMessage(""), 2400);
    } catch (err) {
      setTournamentError({
        title: allocate ? "Create and allocate tournament match failed" : "Create tournament match failed",
        message: err.message,
      });
    } finally {
      setCreatingTournamentMatch(false);
    }
  }

  async function allocateTournamentMatchServer(match) {
    if (!selectedTournamentId) {
      return;
    }

    setAllocatingTournamentServers((current) => ({ ...current, [match.id]: true }));
    setTournamentError(null);

    try {
      const result = await fetchJson(`/api/tournaments/${selectedTournamentId}/matches/${match.id}/allocate-server`, {
        method: "POST",
      });
      const endpoint = assignmentEndpoint(result.assignment);
      setCopyMessage(endpoint ? `Tournament match server assigned: ${endpoint}` : "Tournament match server assigned.");
      window.setTimeout(() => setCopyMessage(""), 2400);
      await loadTournamentDetails(selectedTournamentId, { silent: true });
      await loadDashboard({ silent: true, source: "Tournament assignment refresh" });
    } catch (err) {
      setTournamentError({
        title: "Allocate tournament server failed",
        message: err.message,
      });
    } finally {
      setAllocatingTournamentServers((current) => {
        const next = { ...current };
        delete next[match.id];
        return next;
      });
    }
  }

  async function allocatePlayableTournamentMatches(matchesToAllocate) {
    if (!selectedTournamentId) {
      return;
    }

    if (matchesToAllocate.length === 0) {
      setTournamentError({
        title: "No playable matches to allocate",
        message: "Only generated bracket matches with both teams assigned and no active server are allocated in bulk.",
      });
      return;
    }

    const capacityWarning = fleetStatus.ready_replicas < matchesToAllocate.length
      ? ` Ready capacity is currently ${fleetStatus.ready_replicas}, so this will pause while FleetAutoscaler replenishes warm servers.`
      : "";
    const confirmed = window.confirm(`Allocate servers one at a time for ${matchesToAllocate.length} playable bracket match${matchesToAllocate.length === 1 ? "" : "es"}?${capacityWarning}`);
    if (!confirmed) {
      return;
    }

    setAllocatingPlayableMatches(true);
    setTournamentError(null);
    setBulkAllocationProgress(`Preparing 0 / ${matchesToAllocate.length}`);
    bulkAllocationActiveRef.current = true;

    const failures = [];
    const successes = [];
    const warnings = [];

    try {
      for (const [index, match] of matchesToAllocate.entries()) {
        const matchLabel = match.bracket_position ? `Match ${match.bracket_position}` : `Match ${shortId(match.id)}`;
        let assigned = false;
        let lastError = null;

        for (let attempt = 1; attempt <= BULK_ALLOCATION_REQUEST_RETRY_LIMIT; attempt += 1) {
          try {
            await waitForReadyServerCapacity({ matchNumber: index + 1, totalMatches: matchesToAllocate.length });
            setBulkAllocationProgress(`Allocating ${index + 1} / ${matchesToAllocate.length}`);
            setAllocatingTournamentServers((current) => ({ ...current, [match.id]: true }));

            const result = await fetchJson(`/api/tournaments/${selectedTournamentId}/matches/${match.id}/allocate-server`, {
              method: "POST",
            });
            if (!result.assignment) {
              throw new Error("server allocation response did not include an assignment");
            }

            successes.push({ match, endpoint: assignmentEndpoint(result.assignment) });
            if (result.warning || result.configuration?.verified === false) {
              warnings.push(`${matchLabel}: ${result.warning || result.configuration?.message || "assigned but map/mode verification needs attention"}`);
            }
            assigned = true;
            break;
          } catch (err) {
            lastError = err;
            try {
              const refreshedMatches = await refreshTournamentMatchesSnapshot(selectedTournamentId);
              const refreshedMatch = refreshedMatches.find((item) => item.id === match.id);
              if (refreshedMatch?.active_server_assignment) {
                successes.push({ match: refreshedMatch, endpoint: assignmentEndpoint(refreshedMatch.active_server_assignment) });
                warnings.push(`${matchLabel}: assignment exists after a lost or failed response; refreshed from PostgreSQL.`);
                assigned = true;
                break;
              }
            } catch (refreshErr) {
              if (!isRetryableAllocationError(refreshErr)) {
                lastError = refreshErr;
                break;
              }
            }

            if (!isRetryableAllocationError(err) || attempt === BULK_ALLOCATION_REQUEST_RETRY_LIMIT) {
              break;
            }

            setBulkAllocationProgress(`Retrying ${index + 1} / ${matchesToAllocate.length}`);
            await sleep(BULK_ALLOCATION_BACKEND_RETRY_MS);
          } finally {
            setAllocatingTournamentServers((current) => {
              const next = { ...current };
              delete next[match.id];
              return next;
            });
          }
        }

        if (!assigned) {
          failures.push({ label: matchLabel, message: lastError?.message || "allocation did not complete" });
        }
      }

      await loadTournamentDetails(selectedTournamentId, { silent: true });
      await loadDashboard({ silent: true, source: "Playable tournament assignment refresh", suppressError: true });

      if (failures.length > 0) {
        setTournamentError({
          title: "Some playable matches failed to allocate",
          message: `${successes.length} assigned, ${failures.length} failed. ${failures.map((failure) => `${failure.label}: ${failure.message}`).join(" ")}`,
        });
      } else if (warnings.length > 0) {
        setTournamentError({
          title: "Playable matches assigned with warnings",
          message: warnings.join(" "),
        });
      }

      if (successes.length > 0) {
        setCopyMessage(`${successes.length} playable bracket match${successes.length === 1 ? "" : "es"} assigned.`);
        window.setTimeout(() => setCopyMessage(""), 2400);
      }
    } catch (err) {
      setTournamentError({
        title: "Allocate playable matches failed",
        message: err.message,
      });
    } finally {
      bulkAllocationActiveRef.current = false;
      setAllocatingPlayableMatches(false);
      setBulkAllocationProgress("");
      setAllocatingTournamentServers((current) => {
        const next = { ...current };
        for (const match of matchesToAllocate) {
          delete next[match.id];
        }
        return next;
      });
    }
  }

  async function releaseTournamentMatchServer(match) {
    if (!selectedTournamentId || !match.active_server_assignment) {
      return;
    }

    const endpoint = assignmentEndpoint(match.active_server_assignment);
    const confirmed = window.confirm(
      `Release the persisted server assignment for Match ${shortId(match.id)}${endpoint ? ` (${endpoint})` : ""}? This deletes the allocated Agones GameServer and keeps assignment history in PostgreSQL.`
    );
    if (!confirmed) {
      return;
    }

    setReleasingTournamentServers((current) => ({ ...current, [match.id]: true }));
    setTournamentError(null);

    try {
      await fetchJson(`/api/tournaments/${selectedTournamentId}/matches/${match.id}/release-server`, { method: "POST" });
      setCopyMessage("Tournament match server assignment released.");
      window.setTimeout(() => setCopyMessage(""), 2400);
      await loadTournamentDetails(selectedTournamentId, { silent: true });
      await loadDashboard({ silent: true, source: "Tournament release refresh" });
    } catch (err) {
      setTournamentError({
        title: "Release tournament server failed",
        message: err.message,
      });
    } finally {
      setReleasingTournamentServers((current) => {
        const next = { ...current };
        delete next[match.id];
        return next;
      });
    }
  }

  async function releaseAllTournamentServers() {
    if (!selectedTournamentId) {
      return;
    }

    const activeCount = tournamentActiveServerCount;
    if (activeCount === 0) {
      setTournamentError({
        title: "No active tournament servers",
        message: "This tournament does not have any active server assignments to release.",
      });
      return;
    }

    const confirmed = window.confirm(
      `Release ${activeCount} active server assignment${activeCount === 1 ? "" : "s"} for this tournament? This deletes the allocated Agones GameServers and keeps match history in PostgreSQL.`
    );
    if (!confirmed) {
      return;
    }

    setReleasingAllTournamentServers(true);
    setTournamentError(null);

    try {
      const result = await fetchJson(`/api/tournaments/${selectedTournamentId}/server-assignments/release-all`, {
        method: "POST",
      });
      await loadTournamentDetails(selectedTournamentId, { silent: true });
      await loadDashboard({ silent: true, source: "Tournament release-all refresh", suppressError: true });

      if (result.failed_count > 0) {
        setTournamentError({
          title: "Some tournament servers failed to release",
          message: `${result.released_count} released, ${result.failed_count} failed. ${result.failed.map((failure) => failure.message).join(" ")}`,
        });
      } else {
        setCopyMessage(`${result.released_count} tournament server assignment${result.released_count === 1 ? "" : "s"} released.`);
        window.setTimeout(() => setCopyMessage(""), 2400);
      }
    } catch (err) {
      setTournamentError({
        title: "Release all tournament servers failed",
        message: err.message,
      });
    } finally {
      setReleasingAllTournamentServers(false);
    }
  }

  function tournamentResultForm(match) {
    const current = tournamentResultForms[match.id] || {};
    return {
      team_a_score: current.team_a_score ?? match.team_a_score ?? "",
      team_b_score: current.team_b_score ?? match.team_b_score ?? "",
      winner_team_id: current.winner_team_id ?? match.winner_team_id ?? match.team_a_id ?? "",
      result_notes: current.result_notes ?? match.result_notes ?? "",
    };
  }

  function updateTournamentResultForm(matchId, field, value) {
    setTournamentResultForms((current) => ({
      ...current,
      [matchId]: {
        ...current[matchId],
        [field]: value,
      },
    }));
  }

  async function recordTournamentMatchResult(match) {
    if (!selectedTournamentId) {
      return;
    }

    const form = tournamentResultForm(match);
    setRecordingTournamentResults((current) => ({ ...current, [match.id]: true }));
    setTournamentError(null);

    try {
      await fetchJson(`/api/tournaments/${selectedTournamentId}/matches/${match.id}/result`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          team_a_score: Number(form.team_a_score),
          team_b_score: Number(form.team_b_score),
          winner_team_id: form.winner_team_id,
          result_notes: form.result_notes.trim() || undefined,
        }),
      });
      setCopyMessage("Tournament match result recorded.");
      window.setTimeout(() => setCopyMessage(""), 2400);
      setTournamentResultForms((current) => {
        const next = { ...current };
        delete next[match.id];
        return next;
      });
      await loadTournamentDetails(selectedTournamentId, { silent: true });
    } catch (err) {
      setTournamentError({
        title: "Record result failed",
        message: err.message,
      });
    } finally {
      setRecordingTournamentResults((current) => {
        const next = { ...current };
        delete next[match.id];
        return next;
      });
    }
  }

  async function createMatch(event) {
    event.preventDefault();
    setCreatingMatch(true);
    setError(null);

    try {
      const payload = {
        name: matchForm.name.trim() || undefined,
        requested_map: matchForm.requested_map,
        requested_game_mode: matchForm.requested_game_mode,
      };
      const match = await fetchJson("/api/matches", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      setMatches((current) => [match, ...current]);
      setMatchForm(emptyMatchRoomForm(gameConfigOptions));
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

  function matchRequestedConfig(match) {
    return normalizeGameConfig({
      requested_map: matchConfigForms[match.match_id]?.requested_map || match.requested_map || defaultGameConfig(gameConfigOptions).requested_map,
      requested_game_mode: matchConfigForms[match.match_id]?.requested_game_mode || match.requested_game_mode || defaultGameConfig(gameConfigOptions).requested_game_mode,
    }, gameConfigOptions);
  }

  function updateMatchRequestedConfig(matchId, field, value) {
    setMatchConfigForms((current) => ({
      ...current,
      [matchId]: {
        ...current[matchId],
        ...(field === "requested_game_mode"
          ? normalizeGameConfig({ ...current[matchId], requested_game_mode: value }, gameConfigOptions)
          : { [field]: value }),
      },
    }));
  }

  async function allocateMatch(match) {
    const matchId = match.match_id;
    const requestedConfig = matchRequestedConfig(match);
    setAllocatingMatches((current) => ({ ...current, [matchId]: true }));
    setError(null);

    try {
      const updatedMatch = await fetchJson(`/api/matches/${matchId}/allocate`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(requestedConfig),
      });
      setMatches((current) => current.map((item) => (item.match_id === updatedMatch.match_id ? updatedMatch : item)));
      if (updatedMatch.joinable) {
        setAdminFeedback((current) => ({
          ...current,
          [updatedMatch.match_id]: { type: "success", message: "Server allocated and requested map/mode verified." },
        }));
      } else if (updatedMatch.status === "allocated_needs_attention") {
        setAdminFeedback((current) => ({
          ...current,
          [updatedMatch.match_id]: {
            type: "warning",
            message: updatedMatch.allocation_config_result?.message || "Server allocated, but requested config was not verified.",
          },
        }));
      }
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

  async function releaseMatch(match) {
    const confirmed = window.confirm(`End match "${match.name}" and release its allocated server?`);
    if (!confirmed) {
      return;
    }

    setReleasingMatches((current) => ({ ...current, [match.match_id]: true }));
    setError(null);

    try {
      const updatedMatch = await fetchJson(`/api/matches/${match.match_id}/release`, { method: "POST" });
      setMatches((current) => current.map((item) => (item.match_id === updatedMatch.match_id ? updatedMatch : item)));
      await loadDashboard({ silent: true, source: "Release refresh" });
    } catch (err) {
      setError({
        title: "Release failed",
        message: err.message,
      });
    } finally {
      setReleasingMatches((current) => {
        const next = { ...current };
        delete next[match.match_id];
        return next;
      });
    }
  }

  function adminActionKey(matchId, action) {
    return `${matchId}:${action}`;
  }

  function tournamentAdminId(matchId) {
    return `tournament:${matchId}`;
  }

  function setAdminActionLoading(matchId, action, isBusy) {
    setAdminActions((current) => {
      const next = { ...current };
      const key = adminActionKey(matchId, action);
      if (isBusy) {
        next[key] = true;
      } else {
        delete next[key];
      }
      return next;
    });
  }

  async function broadcastToTournamentMatch(match) {
    if (!selectedTournamentId) {
      return;
    }

    const adminId = tournamentAdminId(match.id);
    const message = (broadcastForms[adminId] || "").trim();
    if (!message) {
      setTournamentError({
        title: "Tournament broadcast failed",
        message: "Enter a message before sending a broadcast.",
      });
      return;
    }

    setAdminActionLoading(adminId, "broadcast", true);
    setTournamentError(null);

    try {
      const result = await fetchJson(`/api/tournaments/${selectedTournamentId}/matches/${match.id}/admin/broadcast`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ message }),
      });
      if (result.match) {
        setTournamentMatches((current) => current.map((item) => (item.id === result.match.id ? result.match : item)));
      }
      setBroadcastForms((current) => ({ ...current, [adminId]: "" }));
      setAdminFeedback((current) => ({
        ...current,
        [adminId]: { type: "success", message: `Broadcast sent: "${result.message}"` },
      }));
    } catch (err) {
      setTournamentError({
        title: "Tournament broadcast failed",
        message: err.message,
      });
    } finally {
      setAdminActionLoading(adminId, "broadcast", false);
    }
  }

  async function changeTournamentMatchMap(match) {
    if (!selectedTournamentId) {
      return;
    }

    const adminId = tournamentAdminId(match.id);
    const map = changeMapForms[adminId] || (ADMIN_MAPS.includes(match.requested_map) ? match.requested_map : ADMIN_MAPS[0]);

    setAdminActionLoading(adminId, "change-map", true);
    setTournamentError(null);

    try {
      const result = await fetchJson(`/api/tournaments/${selectedTournamentId}/matches/${match.id}/admin/change-map`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ map }),
      });
      if (result.match) {
        setTournamentMatches((current) => current.map((item) => (item.id === result.match.id ? result.match : item)));
      }
      setAdminFeedback((current) => ({
        ...current,
        [adminId]: {
          type: result.verified ? "success" : "warning",
          message: result.verified
            ? `Map change verified: ${result.map}`
            : result.message || "Map change command sent, but live status verification is temporarily unavailable.",
        },
      }));
      await loadDashboard({ silent: true, source: "Tournament map change refresh" });
    } catch (err) {
      setTournamentError({
        title: "Tournament map change failed",
        message: err.message,
      });
    } finally {
      setAdminActionLoading(adminId, "change-map", false);
    }
  }

  async function broadcastToMatch(match) {
    const message = (broadcastForms[match.match_id] || "").trim();
    if (!message) {
      setError({
        title: "Broadcast failed",
        message: "Enter a message before sending a broadcast.",
      });
      return;
    }

    setAdminActionLoading(match.match_id, "broadcast", true);
    setError(null);

    try {
      const result = await fetchJson(`/api/matches/${match.match_id}/admin/broadcast`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ message }),
      });
      if (result.match) {
        setMatches((current) => current.map((item) => (item.match_id === result.match.match_id ? result.match : item)));
      }
      setBroadcastForms((current) => ({ ...current, [match.match_id]: "" }));
      setAdminFeedback((current) => ({
        ...current,
        [match.match_id]: { type: "success", message: `Broadcast sent: "${result.message}"` },
      }));
    } catch (err) {
      setError({
        title: "Broadcast failed",
        message: err.message,
      });
    } finally {
      setAdminActionLoading(match.match_id, "broadcast", false);
    }
  }

  async function changeMatchMap(match) {
    const map = changeMapForms[match.match_id] || (ADMIN_MAPS.includes(match.map) ? match.map : ADMIN_MAPS[0]);

    setAdminActionLoading(match.match_id, "change-map", true);
    setError(null);

    try {
      const result = await fetchJson(`/api/matches/${match.match_id}/admin/change-map`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ map }),
      });
      if (result.match) {
        setMatches((current) => current.map((item) => (item.match_id === result.match.match_id ? result.match : item)));
      }
      setAdminFeedback((current) => ({
        ...current,
        [match.match_id]: {
          type: result.verified ? "success" : "warning",
          message: result.verified
            ? `Map change verified: ${result.map}`
            : result.message || "Map change command sent, but live status verification is temporarily unavailable.",
        },
      }));
      await loadDashboard({ silent: true, source: "Map change refresh" });
    } catch (err) {
      setError({
        title: "Change map failed",
        message: err.message,
      });
    } finally {
      setAdminActionLoading(match.match_id, "change-map", false);
    }
  }

  function linkedMatchForServer(serverName) {
    return matches.find((match) => match.allocated_server?.allocated_game_server_name === serverName) || null;
  }

  async function terminateAllocatedServer(server) {
    const linkedMatch = linkedMatchForServer(server.name);
    const scope = linkedMatch ? `linked to "${linkedMatch.name}"` : "not linked to a Match Room";
    const confirmed = window.confirm(
      `Terminate allocated GameServer "${server.name}" (${scope})? This deletes the backing server and Fleet/FleetAutoscaler should replenish capacity.`
    );
    if (!confirmed) {
      return;
    }

    setTerminatingServers((current) => ({ ...current, [server.name]: true }));
    setError(null);

    try {
      const result = await fetchJson(`/api/allocated-servers/${encodeURIComponent(server.name)}/terminate`, { method: "POST" });
      if (result.linked_match) {
        setMatches((current) => current.map((item) => (item.match_id === result.linked_match.match_id ? result.linked_match : item)));
      }
      setCopyMessage(`Terminated ${server.name}; standby capacity should replenish.`);
      window.setTimeout(() => setCopyMessage(""), 2400);
      setCommandPanelServerName("");
      await loadDashboard({ silent: true, source: "Terminate refresh" });
    } catch (err) {
      setError({
        title: "Terminate failed",
        message: err.message,
      });
    } finally {
      setTerminatingServers((current) => {
        const next = { ...current };
        delete next[server.name];
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
    void loadGameConfigOptions();
    void loadDashboard();
    void loadTournaments();
  }, []);

  useEffect(() => {
    setTeamForm({ name: "", tag: "", seed: "" });
    setRoundForm({ name: "", round_order: "" });
    setTournamentMatchForm(emptyTournamentMatchForm(gameConfigOptions));
    void loadTournamentDetails(selectedTournamentId);
  }, [selectedTournamentId]);

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
  const selectedTournament = tournaments.find((tournament) => tournament.id === selectedTournamentId) || null;
  const teamNameById = Object.fromEntries(tournamentTeams.map((team) => [team.id, team.name]));
  const roundNameById = Object.fromEntries(tournamentRounds.map((round) => [round.id, round.name]));
  const bracketConfig = normalizeGameConfig(tournamentMatchForm, gameConfigOptions);
  const bracketColumns = bracketRoundColumns(tournamentRounds, tournamentMatches);
  const hasBracketMatches = tournamentMatches.some((match) => match.bracket_position !== null && match.bracket_position !== undefined);
  const playableBracketMatches = tournamentMatches.filter(tournamentMatchCanBulkAllocate);
  const bracketCreateBlocker = selectedTournament ? bracketGenerationBlocker(tournamentTeams, tournamentRounds, tournamentMatches) : "";
  const bracketReplaceBlocker = selectedTournament && hasBracketMatches ? bracketRegenerationBlocker(selectedTournament, tournamentMatches) : "";
  const bracketActionBlocker = hasBracketMatches ? bracketReplaceBlocker : bracketCreateBlocker;
  const canGenerateBracket = Boolean(selectedTournament && !bracketActionBlocker);
  const selectedTournamentSummary = selectedTournament ? tournamentSummaryData : null;
  const summaryCounts = selectedTournamentSummary?.counts || {};
  const summaryBracket = selectedTournamentSummary?.bracket || {};
  const summaryWinnerName = selectedTournamentSummary?.winner_team?.name || selectedTournamentSummary?.champion_team?.name || "";
  const summaryFinalizeBlocker = selectedTournamentSummary?.finalize_blockers?.[0] || "";
  const summaryActiveServerCount = selectedTournamentSummary?.active_server_assignment_count || 0;
  const tournamentActiveServerCount = Math.max(
    summaryActiveServerCount,
    tournamentMatches.filter((match) => match.active_server_assignment).length
  );

  function refreshPlayerTournamentView() {
    void loadTournaments({ silent: true });
    if (selectedTournamentId) {
      void loadTournamentDetails(selectedTournamentId, { silent: true });
    }
  }

  if (viewMode === "player") {
    return (
      <main className="page player-page">
        <section className="hero player-hero">
          <div>
            <p className="eyebrow">Xonotic Tournament</p>
            <h1>Player View</h1>
            <p className="subtitle">
              Follow tournament rounds, results, winners, and active join targets without admin controls or infrastructure debug panels.
            </p>
          </div>
          <div className="hero-actions">
            <div className="view-toggle" aria-label="Dashboard view">
              <button className="secondary" type="button" onClick={() => setViewMode("admin")}>
                Admin View
              </button>
              <button className="secondary toggle-active" type="button" aria-current="page">
                Player View
              </button>
            </div>
            <button className="secondary" type="button" onClick={refreshPlayerTournamentView} disabled={tournamentLoading || tournamentDetailLoading}>
              {tournamentLoading || tournamentDetailLoading ? "Refreshing..." : "Refresh"}
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

        {tournamentError && (
          <section className="error-banner">
            <strong>{tournamentError.title}</strong>
            <span>{tournamentError.message}</span>
          </section>
        )}

        <PlayerTournamentView
          tournaments={tournaments}
          selectedTournamentId={selectedTournamentId}
          selectedTournament={selectedTournament}
          tournamentLoading={tournamentLoading}
          tournamentDetailLoading={tournamentDetailLoading}
          tournamentRounds={tournamentRounds}
          tournamentMatches={tournamentMatches}
          selectedTournamentSummary={selectedTournamentSummary}
          teamNameById={teamNameById}
          gameConfigOptions={gameConfigOptions}
          onSelectTournament={setSelectedTournamentId}
          onRefreshTournaments={() => void loadTournaments({ silent: true })}
          onCopy={copyText}
        />
      </main>
    );
  }

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
          <div className="view-toggle" aria-label="Dashboard view">
            <button className="secondary toggle-active" type="button" aria-current="page">
              Admin View
            </button>
            <button className="secondary" type="button" onClick={() => setViewMode("player")}>
              Player View
            </button>
          </div>
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

      <div className="dashboard-layout">
      <section className="panel tournament-panel">
        <div className="panel-header">
          <h2>Tournament Management</h2>
          <span className="panel-meta">{tournaments.length} persisted tournaments</span>
        </div>

        {tournamentError && (
          <div className="inline-error">
            <strong>{tournamentError.title}</strong>
            <span>{tournamentError.message}</span>
          </div>
        )}

        <div className="tournament-layout">
          <aside className="tournament-sidebar-stack">
            <article className="tournament-create-card">
              <div className="subsection-header">
                <div>
                  <h3>Create Tournament</h3>
                  <p>Start a persisted tournament record.</p>
                </div>
                {selectedTournamentId && (
                  <button className="copy-button" type="button" onClick={() => setSelectedTournamentId("")}>
                    New
                  </button>
                )}
              </div>
              <form className="tournament-form" onSubmit={(event) => void createTournament(event)}>
                <label>
                  <span>Tournament name</span>
                  <input
                    value={tournamentForm.name}
                    onChange={(event) => setTournamentForm((current) => ({ ...current, name: event.target.value }))}
                    placeholder="Spring Arena Cup"
                    required
                  />
                </label>
                <label>
                  <span>Description</span>
                  <input
                    value={tournamentForm.description}
                    onChange={(event) => setTournamentForm((current) => ({ ...current, description: event.target.value }))}
                    placeholder="Optional notes"
                  />
                </label>
                <button className="primary" type="submit" disabled={creatingTournament || !tournamentForm.name.trim()}>
                  {creatingTournament ? "Creating..." : "Create Tournament"}
                </button>
              </form>
            </article>

          <div className="tournament-list">
            <div className="subsection-header">
              <h3>Existing Tournaments</h3>
              <button className="copy-button" type="button" onClick={() => void loadTournaments({ silent: true })} disabled={tournamentLoading}>
                {tournamentLoading ? "Loading..." : "Refresh"}
              </button>
            </div>
            {tournamentLoading ? (
              <p className="empty-state">Loading tournaments...</p>
            ) : tournaments.length === 0 ? (
              <p className="empty-state">No persisted tournaments yet.</p>
            ) : (
              <div className="tournament-selector-list">
                {tournaments.map((tournament) => (
                  <button
                    className={`tournament-selector ${tournament.id === selectedTournamentId ? "tournament-selector-active" : ""}`}
                    key={tournament.id}
                    type="button"
                    onClick={() => setSelectedTournamentId(tournament.id)}
                  >
                    <strong>{tournament.name}</strong>
                    <span>{tournament.status} · {shortId(tournament.id)}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          </aside>

          <div className="tournament-detail">
            {!selectedTournament ? (
              <div className="no-selection-state">
                <p className="eyebrow">Selected Tournament Details</p>
                <h3>No tournament selected</h3>
                <p>Create a new tournament above, or select an existing tournament from the list to manage teams, rounds, and matches.</p>
                <div className="empty-detail-grid">
                  <div className="empty-detail-card">
                    <strong>Teams</strong>
                    <span>Waiting for a selected tournament.</span>
                  </div>
                  <div className="empty-detail-card">
                    <strong>Rounds</strong>
                    <span>Waiting for a selected tournament.</span>
                  </div>
                  <div className="empty-detail-card">
                    <strong>Matches</strong>
                    <span>Waiting for a selected tournament.</span>
                  </div>
                </div>
              </div>
            ) : (
              <>
                <div className="selected-tournament-card">
                  <div>
                    <p className="eyebrow">Selected Tournament</p>
                    <h3>{selectedTournament.name}</h3>
                    <p>{selectedTournament.description || "No description yet."}</p>
                  </div>
                  <span className="state-badge">{selectedTournament.status}</span>
                </div>

                {selectedTournamentSummary && (
                  <article className="tournament-summary-card">
                    <div className="tournament-summary-main">
                      <p className="eyebrow">Tournament Summary</p>
                      <h3>{summaryWinnerName || "Champion pending"}</h3>
                      <p className="summary-story">{selectedTournamentSummary.story}</p>
                      {tournamentActiveServerCount > 0 && (
                        <p className="summary-note">
                          {tournamentActiveServerCount} active server assignment{tournamentActiveServerCount === 1 ? "" : "s"} still need{tournamentActiveServerCount === 1 ? "s" : ""} to be released.
                        </p>
                      )}
                      {summaryFinalizeBlocker && !selectedTournamentSummary.completed && (
                        <p className="summary-note">{summaryFinalizeBlocker}</p>
                      )}
                      <div className="tournament-summary-actions">
                        <button
                          className="primary"
                          type="button"
                          onClick={() => void finalizeTournament()}
                          disabled={!selectedTournamentSummary.can_finalize || finalizingTournament}
                        >
                          {finalizingTournament ? "Finalizing..." : selectedTournamentSummary.completed ? "Tournament Finalized" : "Finalize Tournament"}
                        </button>
                        <button
                          className="danger-button"
                          type="button"
                          onClick={() => void releaseAllTournamentServers()}
                          disabled={releasingAllTournamentServers || tournamentActiveServerCount === 0}
                        >
                          {releasingAllTournamentServers ? "Releasing..." : "Release All Tournament Servers"}
                        </button>
                      </div>
                    </div>
                    <dl className="tournament-summary-grid">
                      <div>
                        <dt>Champion</dt>
                        <dd>{summaryWinnerName || "pending"}</dd>
                      </div>
                      <div>
                        <dt>Progress</dt>
                        <dd>{summaryCounts.finished_matches || 0}/{summaryCounts.matches || 0} matches</dd>
                      </div>
                      <div>
                        <dt>Remaining</dt>
                        <dd>{summaryCounts.remaining_matches || 0}</dd>
                      </div>
                      <div>
                        <dt>Active Servers</dt>
                        <dd>{tournamentActiveServerCount}</dd>
                      </div>
                      <div>
                        <dt>Final Status</dt>
                        <dd>{selectedTournamentSummary.completed ? `Completed ${formatTimestamp(selectedTournamentSummary.completed_at)}` : "Pending finalization"}</dd>
                      </div>
                      <div>
                        <dt>Final</dt>
                        <dd>{selectedTournamentSummary.final_teams_label || "No final match yet"} · {selectedTournamentSummary.final_score?.label || "score pending"}</dd>
                      </div>
                      <div className="tournament-summary-wide">
                        <dt>Notes</dt>
                        <dd>{selectedTournamentSummary.final_notes || "No result notes yet."}</dd>
                      </div>
                    </dl>
                  </article>
                )}

                <p className="deferred-note">
                  Tournament Matches are persisted planning records. They can now hold a persisted server assignment to one allocated Agones GameServer.
                  Single-elimination brackets can generate persisted rounds and matches; Match Rooms remain available below as the lower-level/manual workflow.
                </p>

                <article className="bracket-generator-card">
                  <div>
                    <p className="eyebrow">Single Elimination</p>
                    <h3>{hasBracketMatches ? "Regenerate Bracket" : "Generate Bracket"}</h3>
                    <p>
                      {selectedTournament.bracket_generated_at
                        ? bracketActionBlocker || `Generated ${selectedTournament.bracket_size}-team bracket. Regeneration is available until servers or results exist.`
                        : bracketActionBlocker || `${tournamentTeams.length}-team bracket ready.`}
                    </p>
                    <span>Match config: {bracketConfig.requested_map} / {bracketConfig.requested_game_mode}</span>
                  </div>
                  <button
                    className={hasBracketMatches ? "secondary" : "primary"}
                    type="button"
                    onClick={() => void generateTournamentBracket({ replaceExisting: hasBracketMatches })}
                    disabled={!canGenerateBracket || generatingBracket}
                  >
                    {generatingBracket ? "Working..." : hasBracketMatches ? "Regenerate Bracket" : "Generate Bracket"}
                  </button>
                </article>

                {tournamentDetailLoading ? (
                  <p className="empty-state">Loading tournament details...</p>
                ) : (
                  <>
                    <div className="tournament-columns">
                      <article className="data-card">
                        <div className="subsection-header">
                          <h3>Teams</h3>
                          <span>{tournamentTeams.length}</span>
                        </div>
                        <form className="compact-form" onSubmit={(event) => void createTournamentTeam(event)}>
                          <label>
                            <span>Team name</span>
                            <input
                              value={teamForm.name}
                              onChange={(event) => setTeamForm((current) => ({ ...current, name: event.target.value }))}
                              placeholder="Blue Rockets"
                              required
                            />
                          </label>
                          <label>
                            <span>Tag</span>
                            <input
                              value={teamForm.tag}
                              onChange={(event) => setTeamForm((current) => ({ ...current, tag: event.target.value }))}
                              placeholder="BLUE"
                            />
                          </label>
                          <label>
                            <span>Seed</span>
                            <input
                              min="1"
                              type="number"
                              value={teamForm.seed}
                              onChange={(event) => setTeamForm((current) => ({ ...current, seed: event.target.value }))}
                              placeholder="1"
                            />
                          </label>
                          <button className="secondary" type="submit" disabled={creatingTeam || !teamForm.name.trim()}>
                            {creatingTeam ? "Adding..." : "Add Team"}
                          </button>
                        </form>
                        {tournamentTeams.length === 0 ? (
                          <p className="empty-state">No teams yet.</p>
                        ) : (
                          <div className="record-list">
                            {tournamentTeams.map((team) => (
                              <div className="record-item" key={team.id}>
                                <strong>{team.name}</strong>
                                <span>{team.tag || "no tag"} · seed {team.seed ?? "unset"}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </article>

                      <article className="data-card">
                        <div className="subsection-header">
                          <h3>Rounds</h3>
                          <span>{tournamentRounds.length}</span>
                        </div>
                        <form className="compact-form" onSubmit={(event) => void createTournamentRound(event)}>
                          <label>
                            <span>Round name</span>
                            <input
                              value={roundForm.name}
                              onChange={(event) => setRoundForm((current) => ({ ...current, name: event.target.value }))}
                              placeholder="Round 1"
                              required
                            />
                          </label>
                          <label>
                            <span>Order</span>
                            <input
                              min="1"
                              type="number"
                              value={roundForm.round_order}
                              onChange={(event) => setRoundForm((current) => ({ ...current, round_order: event.target.value }))}
                              placeholder="1"
                            />
                          </label>
                          <button className="secondary" type="submit" disabled={creatingRound || !roundForm.name.trim()}>
                            {creatingRound ? "Adding..." : "Add Round"}
                          </button>
                        </form>
                        {tournamentRounds.length === 0 ? (
                          <p className="empty-state">No rounds yet.</p>
                        ) : (
                          <div className="record-list">
                            {tournamentRounds.map((round) => (
                              <div className="record-item" key={round.id}>
                                <strong>{round.name}</strong>
                                <span>order {round.round_order} · {round.status}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </article>
                    </div>

                    {hasBracketMatches && (
                      <article className="data-card bracket-board-card">
                        <div className="subsection-header">
                          <div>
                            <h3>Bracket</h3>
                            <p>Round columns show generated matches in bracket order.</p>
                          </div>
                          <div className="subsection-actions">
                            <span>{bracketColumns.reduce((count, column) => count + column.matches.length, 0)} matches</span>
                            <button
                              className="primary"
                              type="button"
                              onClick={() => void allocatePlayableTournamentMatches(playableBracketMatches)}
                              disabled={allocatingPlayableMatches || playableBracketMatches.length === 0}
                            >
                              {allocatingPlayableMatches ? bulkAllocationProgress || "Allocating..." : "Allocate Playable Matches"}
                            </button>
                            <small>
                              {playableBracketMatches.length} playable now · Ready capacity: {fleetStatus.ready_replicas} · allocates one at a time
                            </small>
                          </div>
                        </div>
                        <div className="bracket-columns">
                          {bracketColumns.map(({ round, matches: roundMatches }) => (
                            <section className="bracket-round-column" key={round.id}>
                              <div className="bracket-round-header">
                                <h4>{round.name}</h4>
                                <span>order {round.round_order}</span>
                              </div>
                              <div className="bracket-match-stack">
                                {roundMatches.map((match) => {
                                  const teamAName = match.team_a_id ? teamNameById[match.team_a_id] || shortId(match.team_a_id) : "TBD";
                                  const teamBName = match.team_b_id ? teamNameById[match.team_b_id] || shortId(match.team_b_id) : "TBD";
                                  const winnerName = match.winner_team_id ? teamNameById[match.winner_team_id] || shortId(match.winner_team_id) : "";
                                  const hasScore = tournamentMatchHasResult(match);

                                  return (
                                    <article className="bracket-match-card" key={match.id}>
                                      <div className="bracket-match-card-header">
                                        <strong>Match {match.bracket_position ?? shortId(match.id)}</strong>
                                        <span className={tournamentMatchStatusClass(match.status || "created")}>{tournamentMatchStatusLabel(match.status || "created")}</span>
                                      </div>
                                      <div className={`bracket-team-row ${match.winner_team_id === match.team_a_id ? "bracket-team-winner" : ""}`}>
                                        <span>{teamAName}</span>
                                        {hasScore && <strong>{match.team_a_score}</strong>}
                                      </div>
                                      <div className={`bracket-team-row ${match.winner_team_id === match.team_b_id ? "bracket-team-winner" : ""}`}>
                                        <span>{teamBName}</span>
                                        {hasScore && <strong>{match.team_b_score}</strong>}
                                      </div>
                                      {winnerName && <p>Winner: {winnerName}</p>}
                                    </article>
                                  );
                                })}
                              </div>
                            </section>
                          ))}
                        </div>
                      </article>
                    )}

                    <article className="data-card tournament-matches-card">
                      <div className="subsection-header">
                        <div>
                          <h3>Tournament Matches</h3>
                          <p>{tournamentMatches.length} persisted match{tournamentMatches.length === 1 ? "" : "es"}</p>
                        </div>
                        <div className="subsection-actions">
                          <button
                            className="secondary"
                            type="button"
                            onClick={() => void loadTournamentDetails(selectedTournamentId, { silent: true })}
                            disabled={tournamentDetailLoading}
                          >
                            {tournamentDetailLoading ? "Refreshing..." : "Refresh Tournament"}
                          </button>
                          <button
                            className="danger-button"
                            type="button"
                            onClick={() => void releaseAllTournamentServers()}
                            disabled={releasingAllTournamentServers || tournamentActiveServerCount === 0}
                          >
                            {releasingAllTournamentServers ? "Releasing..." : "Release All Servers"}
                          </button>
                          <small>{tournamentActiveServerCount} active server{tournamentActiveServerCount === 1 ? "" : "s"}</small>
                        </div>
                      </div>
                      <form className="tournament-match-form" onSubmit={(event) => {
                        event.preventDefault();
                        void createTournamentMatch();
                      }}>
                        <label>
                          <span>Match name</span>
                          <input
                            value={tournamentMatchForm.name}
                            onChange={(event) => setTournamentMatchForm((current) => ({ ...current, name: event.target.value }))}
                            placeholder="Operator label, backend name persistence deferred"
                          />
                        </label>
                        <label>
                          <span>Round</span>
                          <select
                            value={tournamentMatchForm.round_id}
                            onChange={(event) => setTournamentMatchForm((current) => ({ ...current, round_id: event.target.value }))}
                          >
                            <option value="">Unassigned</option>
                            {tournamentRounds.map((round) => (
                              <option key={round.id} value={round.id}>
                                {round.name}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          <span>Team A</span>
                          <select
                            value={tournamentMatchForm.team_a_id}
                            onChange={(event) => setTournamentMatchForm((current) => ({ ...current, team_a_id: event.target.value }))}
                          >
                            <option value="">TBD</option>
                            {tournamentTeams.map((team) => (
                              <option key={team.id} value={team.id}>
                                {team.name}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          <span>Team B</span>
                          <select
                            value={tournamentMatchForm.team_b_id}
                            onChange={(event) => setTournamentMatchForm((current) => ({ ...current, team_b_id: event.target.value }))}
                          >
                            <option value="">TBD</option>
                            {tournamentTeams.map((team) => (
                              <option key={team.id} value={team.id}>
                                {team.name}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          <span>Mode</span>
                          <select
                            value={tournamentMatchForm.requested_game_mode}
                            onChange={(event) => setTournamentMatchMode(event.target.value)}
                          >
                            {modeOptions().map((mode) => (
                              <option key={mode.mode} value={mode.mode}>
                                {mode.label}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          <span>Map</span>
                          <select
                            value={tournamentMatchForm.requested_map}
                            onChange={(event) => setTournamentMatchForm((current) => ({ ...current, requested_map: event.target.value }))}
                          >
                            {mapsForSelectedMode(tournamentMatchForm.requested_game_mode).map((mapName) => (
                              <option key={mapName} value={mapName}>
                                {mapName}
                              </option>
                            ))}
                          </select>
                        </label>
                        <div className="tournament-match-actions">
                          <button className="secondary" type="submit" disabled={creatingTournamentMatch}>
                            {creatingTournamentMatch ? "Working..." : "Create Match"}
                          </button>
                          <button
                            className={hasBracketMatches ? "secondary" : "primary"}
                            type="button"
                            onClick={() => void createTournamentMatch({ allocate: true })}
                            disabled={creatingTournamentMatch}
                          >
                            {creatingTournamentMatch ? "Working..." : hasBracketMatches ? "Create Ad Hoc & Allocate" : "Create & Allocate Server"}
                          </button>
                          <span className="capacity-hint">Ready capacity: {fleetStatus.ready_replicas}</span>
                        </div>
                        <p className="deferred-note">
                          {hasBracketMatches
                            ? `Use Allocate Playable Matches for generated bracket matches. Manual creation here is for ad hoc matches only. ${VERIFIED_CONFIG_NOTE}`
                            : VERIFIED_CONFIG_NOTE}
                        </p>
                      </form>

                      {tournamentMatches.length === 0 ? (
                        <p className="empty-state">No tournament matches yet.</p>
                      ) : (
                        <div className="tournament-match-grid">
                          {tournamentMatches.map((match) => {
                            const activeAssignment = match.active_server_assignment;
                            const endpoint = assignmentEndpoint(activeAssignment);
                            const command = connectCommand(endpoint);
                            const status = match.status || "created";
                            const isFinished = status === "finished";
                            const isReleased = status === "released";
                            const isAllocatingServer = Boolean(allocatingTournamentServers[match.id]);
                            const isReleasingServer = Boolean(releasingTournamentServers[match.id]);
                            const isRecordingResult = Boolean(recordingTournamentResults[match.id]);
                            const assignmentVerified = status !== "failed";
                            const resultForm = tournamentResultForm(match);
                            const teamAName = match.team_a_id ? teamNameById[match.team_a_id] || shortId(match.team_a_id) : "TBD";
                            const teamBName = match.team_b_id ? teamNameById[match.team_b_id] || shortId(match.team_b_id) : "TBD";
                            const winnerName = match.winner_team_id ? teamNameById[match.winner_team_id] || `Team ${shortId(match.winner_team_id)}` : "";
                            const hasRecordedResult = tournamentMatchHasResult(match);
                            const canShowResultForm = tournamentMatchCanShowResultForm(match);
                            const canRecordResult = tournamentMatchCanRecordResult(match);
                            const canAllocateServer = tournamentMatchCanAllocateServer(match);
                            const adminId = tournamentAdminId(match.id);
                            const broadcastValue = broadcastForms[adminId] || "";
                            const selectedMap = changeMapForms[adminId] || (ADMIN_MAPS.includes(match.requested_map) ? match.requested_map : ADMIN_MAPS[0]);
                            const isBroadcasting = Boolean(adminActions[adminActionKey(adminId, "broadcast")]);
                            const isChangingMap = Boolean(adminActions[adminActionKey(adminId, "change-map")]);
                            const feedback = adminFeedback[adminId];
                            const resultActionMessage = hasRecordedResult
                              ? "Result recorded."
                              : TERMINAL_TOURNAMENT_MATCH_STATUSES.has(status)
                                ? "No result recorded."
                                : "Assign both teams before recording a result.";
                            const matchLabel = match.bracket_position
                              ? `Match ${match.bracket_position}`
                              : `Match ${shortId(match.id)}`;

                            return (
                              <article className={`tournament-match-card ${isReleased ? "tournament-match-card-released" : ""}`} key={match.id}>
                                <div className="tournament-match-card-header">
                                  <div>
                                    <p className="eyebrow">{match.round_id ? roundNameById[match.round_id] || shortId(match.round_id) : "Unassigned round"}</p>
                                    <h4>{matchLabel}</h4>
                                  </div>
                                  <span className={tournamentMatchStatusClass(status)}>{tournamentMatchStatusLabel(status)}</span>
                                </div>

                                <div className="tournament-match-teams">
                                  <strong>{teamAName}</strong>
                                  <span>vs</span>
                                  <strong>{teamBName}</strong>
                                </div>

                                <dl className="tournament-match-facts">
                                  <div>
                                    <dt>Map / Mode</dt>
                                    <dd>{match.requested_map || "map unset"} / {match.requested_game_mode || "mode unset"}</dd>
                                  </div>
                                  <div>
                                    <dt>Endpoint</dt>
                                    <dd>
                                      {activeAssignment ? (
                                        <span className={assignmentVerified ? "join-endpoint" : "warning-text"}>{endpoint || "Endpoint pending"}</span>
                                      ) : isReleased ? (
                                        <span className="muted-endpoint">Server released</span>
                                      ) : (
                                        <span className="muted-endpoint">No active assignment</span>
                                      )}
                                    </dd>
                                  </div>
                                  <div>
                                    <dt>Result</dt>
                                    <dd>
                                      {hasRecordedResult ? (
                                        <span>{match.team_a_score} - {match.team_b_score}{winnerName ? ` · ${winnerName}` : ""}</span>
                                      ) : (
                                        <span className="muted-endpoint">Not recorded</span>
                                      )}
                                    </dd>
                                  </div>
                                </dl>

                                {activeAssignment && (
                                  <div className={`tournament-server-cell ${assignmentVerified ? "" : "tournament-server-cell-warning"}`}>
                                    <span>{activeAssignment.allocated_game_server_name}</span>
                                    {!assignmentVerified && <span>Config not verified; do not treat as ready.</span>}
                                    {command && <code className="connection-command">{command}</code>}
                                    <div className="button-row">
                                      <CopyButton text={endpoint} label="Endpoint" onCopy={copyText} />
                                      <CopyButton text={command} label="Connect" onCopy={copyText} />
                                    </div>
                                  </div>
                                )}

                                {match.result_notes && <p className="match-note">{match.result_notes}</p>}

                                <div className="tournament-action-groups">
                                  <div className="action-group">
                                    <span>Server</span>
                                    {activeAssignment ? (
                                      <>
                                        {isFinished && <p className="active-server-note">Result recorded. Server still active.</p>}
                                        <button
                                          className="danger-button"
                                          type="button"
                                          onClick={() => void releaseTournamentMatchServer(match)}
                                          disabled={isReleasingServer}
                                        >
                                          {isReleasingServer ? "Releasing..." : "Release Server"}
                                        </button>
                                      </>
                                    ) : canAllocateServer ? (
                                      <>
                                        <button
                                          className="secondary"
                                          type="button"
                                          onClick={() => void allocateTournamentMatchServer(match)}
                                          disabled={isAllocatingServer}
                                        >
                                          {isAllocatingServer ? "Allocating..." : "Allocate Server"}
                                        </button>
                                        <small>Ready capacity: {fleetStatus.ready_replicas}</small>
                                      </>
                                    ) : (
                                      <small>{isReleased ? "Server assignment released." : "Server allocation unavailable."}</small>
                                    )}
                                  </div>

                                  <div className="action-group">
                                    <span>Result</span>
                                    {canShowResultForm ? (
                                      <details className="match-action-panel">
                                        <summary>Record Result</summary>
                                        <form className="result-form" onSubmit={(event) => {
                                          event.preventDefault();
                                          void recordTournamentMatchResult(match);
                                        }}>
                                          <div className="result-score-row">
                                            <label>
                                              <span>{teamAName}</span>
                                              <input
                                                min="0"
                                                type="number"
                                                value={resultForm.team_a_score}
                                                onChange={(event) => updateTournamentResultForm(match.id, "team_a_score", event.target.value)}
                                                disabled={!canRecordResult || isRecordingResult}
                                              />
                                            </label>
                                            <label>
                                              <span>{teamBName}</span>
                                              <input
                                                min="0"
                                                type="number"
                                                value={resultForm.team_b_score}
                                                onChange={(event) => updateTournamentResultForm(match.id, "team_b_score", event.target.value)}
                                                disabled={!canRecordResult || isRecordingResult}
                                              />
                                            </label>
                                          </div>
                                          <label>
                                            <span>Winner</span>
                                            <select
                                              value={resultForm.winner_team_id}
                                              onChange={(event) => updateTournamentResultForm(match.id, "winner_team_id", event.target.value)}
                                              disabled={!canRecordResult || isRecordingResult}
                                            >
                                              {match.team_a_id && <option value={match.team_a_id}>{teamAName}</option>}
                                              {match.team_b_id && <option value={match.team_b_id}>{teamBName}</option>}
                                            </select>
                                          </label>
                                          <label>
                                            <span>Notes</span>
                                            <textarea
                                              value={resultForm.result_notes}
                                              onChange={(event) => updateTournamentResultForm(match.id, "result_notes", event.target.value)}
                                              placeholder="Optional result notes"
                                              disabled={!canRecordResult || isRecordingResult}
                                            />
                                          </label>
                                          <button
                                            className="secondary"
                                            type="submit"
                                            disabled={
                                              !canRecordResult ||
                                              isRecordingResult ||
                                              resultForm.team_a_score === "" ||
                                              resultForm.team_b_score === "" ||
                                              !resultForm.winner_team_id
                                            }
                                          >
                                            {isRecordingResult ? "Saving..." : "Save Result"}
                                          </button>
                                          {!canRecordResult && <span className="muted-endpoint">Assign both teams before recording a result.</span>}
                                        </form>
                                      </details>
                                    ) : (
                                      <small>{resultActionMessage}</small>
                                    )}
                                  </div>

                                  {activeAssignment && (
                                    <details className="match-action-panel tournament-admin-controls">
                                      <summary>Admin Controls</summary>
                                      <form className="tournament-admin-row" onSubmit={(event) => {
                                        event.preventDefault();
                                        void broadcastToTournamentMatch(match);
                                      }}>
                                        <label>
                                          <span>Broadcast</span>
                                          <input
                                            id={`tournament-broadcast-${match.id}`}
                                            value={broadcastValue}
                                            maxLength={BROADCAST_MAX_LENGTH}
                                            onChange={(event) => setBroadcastForms((current) => ({ ...current, [adminId]: event.target.value }))}
                                            placeholder="Match starts in 2 minutes"
                                          />
                                        </label>
                                        <button className="secondary" type="submit" disabled={isBroadcasting || !broadcastValue.trim()}>
                                          {isBroadcasting ? "Sending..." : "Send"}
                                        </button>
                                      </form>
                                      <form className="tournament-admin-row" onSubmit={(event) => {
                                        event.preventDefault();
                                        void changeTournamentMatchMap(match);
                                      }}>
                                        <label>
                                          <span>Map</span>
                                          <select
                                            id={`tournament-change-map-${match.id}`}
                                            value={selectedMap}
                                            onChange={(event) => setChangeMapForms((current) => ({ ...current, [adminId]: event.target.value }))}
                                          >
                                            {ADMIN_MAPS.map((mapName) => (
                                              <option key={mapName} value={mapName}>
                                                {mapName}
                                              </option>
                                            ))}
                                          </select>
                                        </label>
                                        <button className="secondary" type="submit" disabled={isChangingMap}>
                                          {isChangingMap ? "Changing..." : "Change"}
                                        </button>
                                      </form>
                                      {feedback && (
                                        <p className={`admin-feedback ${feedback.type === "warning" ? "admin-feedback-warning" : ""}`}>
                                          {feedback.message || feedback}
                                        </p>
                                      )}
                                    </details>
                                  )}
                                </div>
                              </article>
                            );
                          })}
                        </div>
                      )}
                    </article>
                  </>
                )}
              </>
            )}
          </div>
        </div>
      </section>

      <aside className="right-rail">
      <section className="panel match-rooms-panel">
        <div className="panel-header">
          <h2>Advanced / Debug Match Rooms</h2>
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
            <span>Mode</span>
            <select
              value={matchForm.requested_game_mode}
              onChange={(event) => setMatchRoomMode(event.target.value)}
            >
              {modeOptions().map((mode) => (
                <option key={mode.mode} value={mode.mode}>
                  {mode.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Map</span>
            <select
              value={matchForm.requested_map}
              onChange={(event) => setMatchForm((current) => ({ ...current, requested_map: event.target.value }))}
            >
              {mapsForSelectedMode(matchForm.requested_game_mode).map((mapName) => (
                <option key={mapName} value={mapName}>
                  {mapName}
                </option>
              ))}
            </select>
          </label>
          <button className="secondary" type="submit" disabled={creatingMatch || loading}>
            {creatingMatch ? "Creating..." : "Create Match Room"}
          </button>
        </form>
        <p className="deferred-note">
          Lower-level/manual infrastructure testing. Normal tournament workflow uses Tournament Matches above.
          Servers still come from the warm Agones Fleet, apply requested map/mode through whitelisted RCON, verify with getstatus, then expose the endpoint.
          {" "}{VERIFIED_CONFIG_NOTE}
        </p>

        {loading ? (
          <p className="empty-state">Loading match rooms...</p>
        ) : matches.length === 0 ? (
          <p className="empty-state">No Match Rooms yet. Create one first, then allocate a server into it.</p>
        ) : (
          <div className="match-grid">
            {matches.map((match) => {
              const endpoint = allocationEndpoint(match);
              const command = connectCommand(endpoint);
              const hasBackingServer = Boolean(match.allocated_server);
              const isJoinable = match.joinable === true;
              const isAllocating = Boolean(allocatingMatches[match.match_id]) || match.status === "allocating";
              const isConfiguring = match.status === "configuring";
              const isReleasing = Boolean(releasingMatches[match.match_id]) || match.status === "releasing";
              const isReleased = match.status === "released" || match.status === "finished";
              const liveStatus = match.live_status;
              const players = liveStatus?.players || [];
              const liveMaxPlayers = liveStatus?.max_players ?? match.live_max_players;
              const requestedConfig = matchRequestedConfig(match);
              const broadcastValue = broadcastForms[match.match_id] || "";
              const selectedMap = changeMapForms[match.match_id] || (ADMIN_MAPS.includes(match.map) ? match.map : ADMIN_MAPS[0]);
              const isBroadcasting = Boolean(adminActions[adminActionKey(match.match_id, "broadcast")]);
              const isChangingMap = Boolean(adminActions[adminActionKey(match.match_id, "change-map")]);
              const feedback = adminFeedback[match.match_id];
              const hasConfigWarning = requestedConfigDiffers(match);

              return (
                <article
                  className={`match-card ${isJoinable ? "match-card-allocated" : ""} ${isReleased ? "match-card-released" : ""}`}
                  key={match.match_id}
                >
                  <div className="match-card-header">
                    <div>
                      <h3>{match.name}</h3>
                      <p>{match.match_id}</p>
                    </div>
                    <span className="state-badge">{match.status}</span>
                  </div>
                  <dl className="match-details live-config">
                    <div>
                      <dt>Requested Map</dt>
                      <dd>{unknown(match.requested_map)}</dd>
                    </div>
                    <div>
                      <dt>Requested Mode</dt>
                      <dd>{unknown(match.requested_game_mode)}</dd>
                    </div>
                    <div>
                      <dt>Live Map / Mode</dt>
                      <dd>{unknown(match.map)} / {unknown(match.game_mode)}</dd>
                    </div>
                    <div>
                      <dt>Players</dt>
                      <dd>
                        {unknown(match.current_players)} / {unknown(liveMaxPlayers)}
                      </dd>
                    </div>
                  </dl>

                  {hasBackingServer ? (
                    <>
                      {isJoinable ? (
                        <div className="assigned-server">
                          <span>Verified join target</span>
                          <strong className="join-endpoint">{endpoint}</strong>
                          <code>{command}</code>
                          <div className="button-row">
                            <CopyButton text={endpoint} label="Endpoint" onCopy={copyText} />
                            <CopyButton text={command} label="Command" onCopy={copyText} />
                            <button className="danger-button" type="button" onClick={() => void releaseMatch(match)} disabled={isReleasing}>
                              {isReleasing ? "Releasing..." : "End Match"}
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="assigned-server assigned-server-warning">
                          <span>Backing server allocated, not verified</span>
                          <strong>Endpoint withheld</strong>
                          <p>{match.allocation_config_result?.message || "Requested map/mode has not been verified yet."}</p>
                          <div className="button-row">
                            <button className="danger-button" type="button" onClick={() => void releaseMatch(match)} disabled={isReleasing}>
                              {isReleasing ? "Releasing..." : "Release Server"}
                            </button>
                          </div>
                        </div>
                      )}

                      <div className="admin-controls">
                        <div className="admin-controls-header">
                          <div>
                            <h4>Admin Override Controls</h4>
                            <p>Secondary whitelisted RCON actions. Normal flow configures map/mode before exposing the endpoint.</p>
                          </div>
                        </div>

                        <form className="admin-control-row" onSubmit={(event) => {
                          event.preventDefault();
                          void broadcastToMatch(match);
                        }}>
                          <label>
                            <span>Broadcast message</span>
                            <input
                              id={`broadcast-${match.match_id}`}
                              value={broadcastValue}
                              maxLength={BROADCAST_MAX_LENGTH}
                              onChange={(event) => setBroadcastForms((current) => ({ ...current, [match.match_id]: event.target.value }))}
                              placeholder="Match starts in 2 minutes"
                            />
                          </label>
                          <button className="secondary" type="submit" disabled={isBroadcasting || !broadcastValue.trim()}>
                            {isBroadcasting ? "Sending..." : "Send Message"}
                          </button>
                        </form>

                        <form className="admin-control-row" onSubmit={(event) => {
                          event.preventDefault();
                          void changeMatchMap(match);
                        }}>
                          <label>
                            <span>Change map</span>
                            <select
                              id={`change-map-${match.match_id}`}
                              value={selectedMap}
                              onChange={(event) => setChangeMapForms((current) => ({ ...current, [match.match_id]: event.target.value }))}
                            >
                              {ADMIN_MAPS.map((mapName) => (
                                <option key={mapName} value={mapName}>
                                  {mapName}
                                </option>
                              ))}
                            </select>
                          </label>
                          <button className="secondary" type="submit" disabled={isChangingMap}>
                            {isChangingMap ? "Changing..." : "Change Map"}
                          </button>
                        </form>

                        {feedback && (
                          <p className={`admin-feedback ${feedback.type === "warning" ? "admin-feedback-warning" : ""}`}>
                            {feedback.message || feedback}
                          </p>
                        )}
                      </div>

                      <div className={`live-status ${liveStatus?.ok ? "live-status-ok" : "live-status-muted"}`}>
                        <div className="live-status-header">
                          <span>{liveStatusLabel(liveStatus)}</span>
                          <strong>
                            {unknown(match.current_players)} / {unknown(liveMaxPlayers)} players
                          </strong>
                        </div>
                        {liveStatus?.ok ? (
                          <>
                            {hasConfigWarning && (
                              <p className="status-warning">
                                Requested config differs from live status. Requested {match.requested_map}/{match.requested_game_mode}; live {unknown(match.map)}/{unknown(match.game_mode)}.
                              </p>
                            )}
                            {match.last_status_error && (
                              <p className="status-warning">
                                Latest live status check failed, showing last known good status.
                              </p>
                            )}
                            {liveStatus.teams?.length > 0 && (
                              <div className="team-score-list">
                                {liveStatus.teams.map((team) => (
                                  <span key={`${match.match_id}-${team.team}`}>
                                    Team {team.team}: {Object.values(team.scores || {}).join(", ") || team.score_raw}
                                  </span>
                                ))}
                              </div>
                            )}
                            {players.length > 0 ? (
                              <div className="player-list">
                                {players.map((player) => (
                                  <div className="player-row" key={`${match.match_id}-${player.name}-${player.ping}`}>
                                    <strong>{player.name}</strong>
                                    <span>{playerScore(player)}</span>
                                    <span>{player.ping ?? "?"} ms</span>
                                  </div>
                                ))}
                              </div>
                            ) : (
                              <p className="empty-state">No connected players reported yet.</p>
                            )}
                          </>
                        ) : (
                          <p className="empty-state">{liveStatus?.message || "Waiting for the next getstatus response."}</p>
                        )}
                      </div>
                    </>
                  ) : (
                    <div className="preallocation-controls">
                      <div className="admin-control-row">
                        <label>
                          <span>Mode before allocation</span>
                          <select
                            value={requestedConfig.requested_game_mode}
                            onChange={(event) => updateMatchRequestedConfig(match.match_id, "requested_game_mode", event.target.value)}
                            disabled={isAllocating || isConfiguring || isReleased}
                          >
                            {modeOptions().map((mode) => (
                              <option key={mode.mode} value={mode.mode}>
                                {mode.label}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          <span>Map before allocation</span>
                          <select
                            value={requestedConfig.requested_map}
                            onChange={(event) => updateMatchRequestedConfig(match.match_id, "requested_map", event.target.value)}
                            disabled={isAllocating || isConfiguring || isReleased}
                          >
                            {mapsForSelectedMode(requestedConfig.requested_game_mode).map((mapName) => (
                              <option key={mapName} value={mapName}>
                                {mapName}
                              </option>
                            ))}
                          </select>
                        </label>
                      </div>
                      <p className="empty-state">
                        Allocation will configure the warm server with RCON and verify getstatus before showing a join endpoint.
                        {" "}{VERIFIED_CONFIG_NOTE}
                      </p>
                      <button className="secondary" type="button" onClick={() => void allocateMatch(match)} disabled={isAllocating || isConfiguring || isReleased}>
                        {isAllocating || isConfiguring ? "Allocating..." : isReleased ? "Match Ended" : "Allocate Server"}
                      </button>
                    </div>
                  )}
                  {isReleased && (
                    <p className="release-note">
                      Match ended. The user-facing endpoint was removed and the allocated GameServer was released.
                    </p>
                  )}
                </article>
              );
            })}
          </div>
        )}
      </section>

      <section className="grid metrics rail-metrics">
        <MetricCard label="Fleet Desired" value={fleetStatus.desired_replicas} />
        <MetricCard label="Fleet Total" value={fleetStatus.replicas} />
        <MetricCard label="Ready" value={fleetStatus.ready_replicas} />
        <MetricCard label="Allocated" value={fleetStatus.allocated_replicas} />
        <MetricCard label="Reserved" value={fleetStatus.reserved_replicas} />
      </section>

      <section className="grid panels rail-panels">
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

        <details className="panel debug-panel">
          <summary>
            <span>Advanced / Debug</span>
            <small>Manual direct allocation for lower-level allocator testing only</small>
          </summary>
          <div className="debug-panel-body">
            <p className="empty-state debug-copy">
              This bypasses Match Rooms and should not be used as the normal operator workflow.
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
                  <CopyButton text={latestCommand} label="Copy connect" onCopy={copyText} />
                </div>
              </dl>
            ) : (
              <p className="empty-state">No direct debug allocation yet in this browser session.</p>
            )}

            <div className="debug-history">
              <div className="panel-header">
                <h3>Manual Allocation History</h3>
                <span className="panel-meta">{allocationHistory.length} recent debug allocations</span>
              </div>
              {allocationHistory.length === 0 ? (
                <p className="empty-state">Successful direct debug allocations will appear here for this browser session.</p>
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
                        <CopyButton text={command} label="Copy connect" onCopy={copyText} />
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </details>
      </section>

      <section className="panel allocated-servers-panel">
        <div className="panel-header">
          <h2>Allocated Servers</h2>
          <span className="panel-meta">{allocatedServers.length} backing infrastructure allocations</span>
        </div>
        <p className="empty-state infra-note">
          These are allocated Agones GameServers. Prefer Match Room controls when a server is linked to a room; terminate is for cleaning up allocated backing servers.
        </p>
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
                  const linkedMatch = linkedMatchForServer(server.name);
                  const isTerminating = Boolean(terminatingServers[server.name]);
                  const isCommandPanelOpen = commandPanelServerName === server.name;

                  return (
                    <Fragment key={server.name}>
                      <tr>
                        <td>
                          <div className="server-name-cell">
                            <strong>{server.name}</strong>
                            <span>{linkedMatch ? `Match Room: ${linkedMatch.name}` : "No linked Match Room"}</span>
                          </div>
                        </td>
                        <td className="join-endpoint">{endpoint}</td>
                        <td className="connection-command">{command}</td>
                        <td>{server.node_name || "-"}</td>
                        <td>
                          <div className="table-actions">
                            <CopyButton text={endpoint} label="Endpoint" onCopy={copyText} />
                            <CopyButton text={command} label="Copy connect" onCopy={copyText} />
                            <button
                              className="copy-button"
                              type="button"
                              onClick={() => setCommandPanelServerName((current) => (current === server.name ? "" : server.name))}
                            >
                              Commands
                            </button>
                            <button
                              className="danger-button"
                              type="button"
                              onClick={() => void terminateAllocatedServer(server)}
                              disabled={isTerminating}
                            >
                              {isTerminating ? "Terminating..." : "Terminate"}
                            </button>
                          </div>
                        </td>
                      </tr>
                      {isCommandPanelOpen && (
                        <tr className="command-panel-row">
                          <td colSpan="5">
                            <div className="command-panel">
                              <div>
                                <h4>Safe Commands</h4>
                                <p>
                                  Broadcast and map-change actions are available only through a linked Match Room. Direct/manual allocations can be terminated, but RCON commands stay disabled until they have a safe room context.
                                </p>
                              </div>
                              {linkedMatch ? (
                                <>
                                  <form className="admin-control-row" onSubmit={(event) => {
                                    event.preventDefault();
                                    void broadcastToMatch(linkedMatch);
                                  }}>
                                    <label>
                                      <span>Broadcast message</span>
                                      <input
                                        value={broadcastForms[linkedMatch.match_id] || ""}
                                        maxLength={BROADCAST_MAX_LENGTH}
                                        onChange={(event) => setBroadcastForms((current) => ({
                                          ...current,
                                          [linkedMatch.match_id]: event.target.value,
                                        }))}
                                        placeholder="Match starts in 2 minutes"
                                      />
                                    </label>
                                    <button
                                      className="secondary"
                                      type="submit"
                                      disabled={Boolean(adminActions[adminActionKey(linkedMatch.match_id, "broadcast")]) || !(broadcastForms[linkedMatch.match_id] || "").trim()}
                                    >
                                      {adminActions[adminActionKey(linkedMatch.match_id, "broadcast")] ? "Sending..." : "Send Message"}
                                    </button>
                                  </form>

                                  <form className="admin-control-row" onSubmit={(event) => {
                                    event.preventDefault();
                                    void changeMatchMap(linkedMatch);
                                  }}>
                                    <label>
                                      <span>Change map</span>
                                      <select
                                        value={changeMapForms[linkedMatch.match_id] || (ADMIN_MAPS.includes(linkedMatch.map) ? linkedMatch.map : ADMIN_MAPS[0])}
                                        onChange={(event) => setChangeMapForms((current) => ({
                                          ...current,
                                          [linkedMatch.match_id]: event.target.value,
                                        }))}
                                      >
                                        {ADMIN_MAPS.map((mapName) => (
                                          <option key={mapName} value={mapName}>
                                            {mapName}
                                          </option>
                                        ))}
                                      </select>
                                    </label>
                                    <button
                                      className="secondary"
                                      type="submit"
                                      disabled={Boolean(adminActions[adminActionKey(linkedMatch.match_id, "change-map")])}
                                    >
                                      {adminActions[adminActionKey(linkedMatch.match_id, "change-map")] ? "Changing..." : "Change Map"}
                                    </button>
                                  </form>
                                  {adminFeedback[linkedMatch.match_id] && (
                                    <p className={`admin-feedback ${adminFeedback[linkedMatch.match_id].type === "warning" ? "admin-feedback-warning" : ""}`}>
                                      {adminFeedback[linkedMatch.match_id].message || adminFeedback[linkedMatch.match_id]}
                                    </p>
                                  )}
                                  <span>Commands route through linked Match Room: {linkedMatch.name}.</span>
                                </>
                              ) : (
                                <p className="empty-state">
                                  Command actions disabled: this allocated server is not linked to an in-memory Match Room.
                                </p>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel internal-servers-panel">
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
      </aside>
      </div>
    </main>
  );
}
