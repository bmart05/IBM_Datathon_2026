const byId = (id) => document.getElementById(id);
const queryApi = new URLSearchParams(location.search).get("api");
const defaultApi = queryApi || localStorage.getItem("riverguard-api") || (location.hostname === "localhost" || location.hostname === "127.0.0.1" ? "http://localhost:8001" : `${location.protocol}//${location.hostname}:8000`);
let apiBase = defaultApi.replace(/\/$/, "");
let replay = null;
let evaluation = null;
let playTimer = null;
let playSpeed = 1;
let currentIndex = 0;
let policyTimer = null;
let policyRequestVersion = 0;

byId("api-url").value = apiBase;
byId("docs-link").href = `${apiBase}/docs`;

function setStatus(state, message) {
  byId("connection-status").textContent = message;
  byId("connection-status").previousElementSibling.className = `live-dot ${state}`;
}

async function api(path) {
  const response = await fetch(`${apiBase}${path}`);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `API returned ${response.status}`);
  }
  return response.json();
}

function clearPolicyCards(message, tone = "neutral") {
  document.querySelectorAll(".policy-card").forEach((card) => {
    const status = card.querySelector(".policy-status");
    status.className = `policy-status ${tone}`;
    status.textContent = message;
    card.querySelector(".policy-score").textContent = "—";
  });
}

function policyHistory(index) {
  const available = replay.observations.slice(0, index + 1).slice(-24);
  if (available.length < 2) return { error: "At least two past observations are needed to compare policies." };
  const intervals = available.slice(1).map((row, i) =>
    (new Date(row.time) - new Date(available[i].time)) / 60000
  );
  const sorted = [...intervals].sort((a, b) => a - b);
  const medianInterval = sorted[Math.floor(sorted.length / 2)];
  if (!Number.isFinite(medianInterval) || medianInterval <= 0 || intervals.some((gap) => gap <= 0 || gap > medianInterval * 1.5)) {
    return { error: "A data gap in the recent history prevents a comparable policy estimate." };
  }
  return {
    observed_at: available.at(-1).time,
    recent_heights_m: available.map((row) => row.stage_m),
    sample_interval_minutes: Math.max(1, Math.round(medianInterval)),
  };
}

async function comparePolicies(index, version) {
  const history = policyHistory(index);
  if (history.error) {
    clearPolicyCards("Unavailable");
    byId("policy-rationale").textContent = history.error;
    return;
  }
  const policies = ["cautious", "balanced", "early"];
  try {
    const results = await Promise.all(policies.map(async (policy) => {
      const response = await fetch(`${apiBase}/v1/predict`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          station_id: replay.station.noaa_id,
          ...history,
          flood_stage_m: replay.target.threshold_m,
          policy,
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Prediction API returned ${response.status}`);
      }
      return [policy, await response.json()];
    }));
    if (version !== policyRequestVersion) return;
    for (const [policy, result] of results) {
      const card = document.querySelector(`.policy-card[data-policy="${policy}"]`);
      const status = card.querySelector(".policy-status");
      const reviewing = result.alert === "review_now";
      status.className = `policy-status ${reviewing ? "review" : "monitor"}`;
      status.textContent = reviewing ? "Human review" : "Monitor";
      card.querySelector(".policy-score").textContent = Number(result.probability_exceedance).toFixed(2);
    }
    const baseline = results[0][1];
    byId("policy-rationale").textContent = `${baseline.rationale} The score is a prototype heuristic, not a calibrated probability. Only observations available at this replay position were sent to the API.`;
  } catch (error) {
    if (version !== policyRequestVersion) return;
    clearPolicyCards("Unavailable");
    byId("policy-rationale").textContent = `Policy comparison unavailable: ${error.message}`;
  }
}

function schedulePolicyComparison(index) {
  if (policyTimer) clearTimeout(policyTimer);
  const version = ++policyRequestVersion;
  clearPolicyCards("Updating");
  byId("policy-rationale").textContent = "Comparing the same replay position under all three backend policies…";
  policyTimer = setTimeout(() => comparePolicies(index, version), 220);
}

function fmtDate(value, options = { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }) {
  return new Intl.DateTimeFormat("en-US", options).format(new Date(value));
}

function fmtPercent(value) {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

function htmlNode(name, attrs = {}, text = "") {
  const node = document.createElement(name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  node.textContent = text;
  return node;
}

async function connect() {
  stopPlayback();
  apiBase = byId("api-url").value.trim().replace(/\/$/, "");
  if (!apiBase) {
    setStatus("error", "Enter an API URL");
    return;
  }
  localStorage.setItem("riverguard-api", apiBase);
  byId("docs-link").href = `${apiBase}/docs`;
  setStatus("", "Connecting to API");
  byId("event-select").disabled = true;
  try {
    const data = await api("/v1/replays");
    setStatus("online", "API connected");
    const select = byId("event-select");
    select.replaceChildren(...data.replays.map((item) => htmlNode("option", { value: item.replay_id }, item.name)));
    select.disabled = data.replays.length === 0;
    if (!data.replays.length) throw new Error("The API has no replay fixtures.");
    await loadReplay(select.value);
  } catch (error) {
    setStatus("error", "API unavailable");
    clearPolicyCards("Unavailable");
    byId("policy-rationale").textContent = `Policy comparison unavailable: ${error.message}`;
    byId("event-select").replaceChildren(htmlNode("option", {}, "Could not load events"));
    byId("event-name").textContent = "Can't reach the replay API";
    byId("event-location").textContent = error.message;
    byId("chart-empty").textContent = "Check the API URL and that the backend is running.";
    byId("chart-empty").classList.remove("hidden");
  }
}

async function loadReplay(id) {
  stopPlayback();
  byId("event-name").textContent = "Loading replay…";
  try {
    const [series, score] = await Promise.all([
      api(`/v1/replays/${encodeURIComponent(id)}`),
      api(`/v1/replays/${encodeURIComponent(id)}/evaluation`),
    ]);
    replay = series;
    evaluation = score.evaluation;
    currentIndex = 0;
    byId("event-select").value = id;
    byId("event-name").textContent = replay.name;
    byId("event-location").textContent = `${replay.station.name} · NOAA ${replay.station.noaa_id} · USGS ${replay.station.usgs_id}`;
    byId("event-period").textContent = `${replay.period.start} — ${replay.period.end}`;
    byId("event-count").textContent = `${replay.observations.length.toLocaleString()} observations`;
    const source = htmlNode("a", { href: replay.source.url, target: "_blank", rel: "noreferrer" }, "Source: USGS ↗");
    byId("event-source").replaceChildren(source);
    byId("timeline").max = String(replay.observations.length - 1);
    byId("timeline").value = "0";
    byId("timeline").disabled = false;
    byId("play-button").disabled = false;
    byId("time-start").textContent = fmtDate(replay.period.start);
    byId("time-end").textContent = fmtDate(replay.period.end);
    byId("chart-empty").classList.add("hidden");
    renderEvaluation();
    updatePosition(0);
  } catch (error) {
    byId("event-name").textContent = "Could not load this event";
    byId("event-location").textContent = error.message;
    clearPolicyCards("Unavailable");
    byId("policy-rationale").textContent = `Policy comparison unavailable: ${error.message}`;
    setStatus("error", "Replay load failed");
  }
}

function renderEvaluation() {
  const methods = evaluation.methods;
  byId("trend-recall").textContent = fmtPercent(methods.linear_trend_1h.recall);
  byId("persistence-recall").textContent = fmtPercent(methods.persistence.recall);
  byId("trend-precision").textContent = fmtPercent(methods.linear_trend_1h.precision);
  byId("trend-lead").textContent = methods.linear_trend_1h.first_alert_lead_minutes == null ? "—" : `${methods.linear_trend_1h.first_alert_lead_minutes}m`;
  byId("evaluation-note").textContent = `${evaluation.forecast_origins_evaluated.toLocaleString()} forecast origins scored · ${evaluation.forecast_origins_excluded_for_gaps_or_warmup.toLocaleString()} excluded for data gaps or warm-up. Overlapping windows are correlated; results describe this event only.`;
}

function chartPath(points, x, y) {
  return points.map((point, index) => `${index ? "L" : "M"}${x(index).toFixed(2)},${y(point.stage_m).toFixed(2)}`).join(" ");
}

function renderChart(index) {
  if (!replay) return;
  const svg = byId("stage-chart");
  const ns = "http://www.w3.org/2000/svg";
  const points = replay.observations;
  const width = 1000, height = 320, left = 54, right = 16, top = 14, bottom = 31;
  const values = points.map((point) => point.stage_m);
  const threshold = replay.target.threshold_m;
  const low = Math.min(...values, threshold) * 0.9;
  const high = Math.max(...values, threshold) * 1.08;
  const x = (i) => left + (points.length < 2 ? 0 : i / (points.length - 1)) * (width - left - right);
  const y = (value) => top + (1 - (value - low) / (high - low || 1)) * (height - top - bottom);
  svg.replaceChildren();
  const defs = document.createElementNS(ns, "defs");
  defs.innerHTML = '<linearGradient id="stage-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="#65b8ff" stop-opacity=".18"/><stop offset="100%" stop-color="#65b8ff" stop-opacity="0"/></linearGradient>';
  svg.append(defs);
  for (let tick = 0; tick <= 4; tick++) {
    const yy = top + tick * (height - top - bottom) / 4;
    const value = high - tick * (high - low) / 4;
    const grid = document.createElementNS(ns, "line");
    grid.setAttribute("x1", left); grid.setAttribute("x2", width - right); grid.setAttribute("y1", yy); grid.setAttribute("y2", yy); grid.setAttribute("class", "chart-grid"); svg.append(grid);
    const label = document.createElementNS(ns, "text");
    label.setAttribute("x", left - 9); label.setAttribute("y", yy + 3); label.setAttribute("text-anchor", "end"); label.setAttribute("class", "chart-axis"); label.textContent = value.toFixed(1); svg.append(label);
  }
  const area = document.createElementNS(ns, "path");
  const observedPath = chartPath(points, x, y);
  area.setAttribute("d", `${observedPath} L${x(points.length - 1)},${height - bottom} L${x(0)},${height - bottom} Z`); area.setAttribute("class", "chart-area"); svg.append(area);
  const line = document.createElementNS(ns, "path"); line.setAttribute("d", observedPath); line.setAttribute("class", "chart-line"); svg.append(line);
  const thresholdLine = document.createElementNS(ns, "line"); thresholdLine.setAttribute("x1", left); thresholdLine.setAttribute("x2", width - right); thresholdLine.setAttribute("y1", y(threshold)); thresholdLine.setAttribute("y2", y(threshold)); thresholdLine.setAttribute("class", "threshold-line"); svg.append(thresholdLine);
  const thresholdLabel = document.createElementNS(ns, "text"); thresholdLabel.setAttribute("x", width - right); thresholdLabel.setAttribute("y", y(threshold) - 6); thresholdLabel.setAttribute("text-anchor", "end"); thresholdLabel.setAttribute("class", "chart-axis"); thresholdLabel.setAttribute("fill", "#ffbd62"); thresholdLabel.textContent = `MINOR ${threshold.toFixed(2)} m`; svg.append(thresholdLabel);
  const cursor = document.createElementNS(ns, "line"); cursor.setAttribute("x1", x(index)); cursor.setAttribute("x2", x(index)); cursor.setAttribute("y1", top); cursor.setAttribute("y2", height - bottom); cursor.setAttribute("class", "cursor-line"); svg.append(cursor);
  const dot = document.createElementNS(ns, "circle"); dot.setAttribute("cx", x(index)); dot.setAttribute("cy", y(points[index].stage_m)); dot.setAttribute("r", "5"); dot.setAttribute("class", "cursor-dot"); svg.append(dot);
  const startLabel = document.createElementNS(ns, "text"); startLabel.setAttribute("x", left); startLabel.setAttribute("y", height - 8); startLabel.setAttribute("class", "chart-axis"); startLabel.textContent = fmtDate(points[0].time, { month: "short", day: "numeric", timeZone: "UTC" }); svg.append(startLabel);
  const endLabel = document.createElementNS(ns, "text"); endLabel.setAttribute("x", width - right); endLabel.setAttribute("y", height - 8); endLabel.setAttribute("text-anchor", "end"); endLabel.setAttribute("class", "chart-axis"); endLabel.textContent = fmtDate(points.at(-1).time, { month: "short", day: "numeric", timeZone: "UTC" }); svg.append(endLabel);
}

function trendEstimate(index) {
  const now = new Date(replay.observations[index].time);
  const history = replay.observations.slice(0, index + 1).filter((row) => now - new Date(row.time) <= 60 * 60 * 1000);
  if (history.length < 3 || now - new Date(history[0].time) < 35 * 60 * 1000) return null;
  if (history.some((row, i) => i > 0 && new Date(row.time) - new Date(history[i - 1].time) > 45 * 60 * 1000)) return null;
  const x = history.map((row) => (new Date(row.time) - new Date(history[0].time)) / 3600000);
  const y = history.map((row) => row.stage_m);
  const xMean = x.reduce((a, b) => a + b, 0) / x.length;
  const yMean = y.reduce((a, b) => a + b, 0) / y.length;
  const denom = x.reduce((sum, value) => sum + (value - xMean) ** 2, 0);
  if (!denom) return null;
  const slope = x.reduce((sum, value, i) => sum + (value - xMean) * (y[i] - yMean), 0) / denom;
  const current = replay.observations[index].stage_m;
  return Math.max(current, current + slope * replay.target.horizon_hours);
}

function updatePosition(index) {
  if (!replay) return;
  currentIndex = Math.max(0, Math.min(index, replay.observations.length - 1));
  const observation = replay.observations[currentIndex];
  const threshold = replay.target.threshold_m;
  const estimate = trendEstimate(currentIndex);
  byId("timeline").value = String(currentIndex);
  byId("current-time").textContent = fmtDate(observation.time, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC", timeZoneName: "short" });
  byId("current-stage").textContent = observation.stage_m.toFixed(3);
  byId("threshold-distance").textContent = observation.stage_m >= threshold ? `${(observation.stage_m - threshold).toFixed(2)} m above minor stage` : `${(threshold - observation.stage_m).toFixed(2)} m below minor stage`;
  byId("meter-values").textContent = `${observation.stage_m.toFixed(2)} / ${threshold.toFixed(2)} m`;
  byId("meter-fill").style.width = `${Math.min(100, observation.stage_m / threshold * 78)}%`;
  byId("trend-peak").textContent = estimate == null ? "—" : estimate.toFixed(3);
  const badge = byId("trend-badge");
  if (estimate == null) {
    byId("outlook-title").textContent = "Building a history window";
    byId("outlook-copy").textContent = "At least three observations spanning 35 minutes are needed for this illustrative trend.";
    badge.className = "badge neutral"; badge.textContent = "Awaiting data";
    byId("trend-note").textContent = "The estimate appears after enough recent observations are available.";
  } else if (estimate >= threshold) {
    byId("outlook-title").textContent = "Threshold in projected range";
    byId("outlook-copy").textContent = "The simple linear trend projects a six-hour peak at or above minor flood stage.";
    badge.className = "badge alert"; badge.textContent = "Review warning";
    byId("trend-note").textContent = "A simple historical replay estimate, not a calibrated forecast or an emergency alert.";
  } else {
    byId("outlook-title").textContent = "Below projected threshold";
    byId("outlook-copy").textContent = "This simple trend does not project minor flood stage within six hours.";
    badge.className = "badge safe"; badge.textContent = "Below threshold";
    byId("trend-note").textContent = "A low trend estimate does not rule out flooding; use official forecasts and qualified review.";
  }
  renderChart(currentIndex);
  schedulePolicyComparison(currentIndex);
}

function stopPlayback() {
  if (playTimer) clearInterval(playTimer);
  playTimer = null;
  byId("play-button").textContent = "▶";
  byId("play-button").setAttribute("aria-label", "Play replay");
}

function togglePlayback() {
  if (playTimer) return stopPlayback();
  if (!replay) return;
  if (currentIndex >= replay.observations.length - 1) updatePosition(0);
  byId("play-button").textContent = "Ⅱ";
  byId("play-button").setAttribute("aria-label", "Pause replay");
  playTimer = setInterval(() => {
    if (currentIndex >= replay.observations.length - 1) return stopPlayback();
    updatePosition(currentIndex + playSpeed);
  }, 220);
}

byId("connect-button").addEventListener("click", connect);
byId("api-url").addEventListener("keydown", (event) => { if (event.key === "Enter") connect(); });
byId("event-select").addEventListener("change", (event) => loadReplay(event.target.value));
byId("timeline").addEventListener("input", (event) => { stopPlayback(); updatePosition(Number(event.target.value)); });
byId("play-button").addEventListener("click", togglePlayback);
document.querySelectorAll(".speed").forEach((button) => button.addEventListener("click", () => {
  playSpeed = Number(button.dataset.speed);
  document.querySelectorAll(".speed").forEach((item) => item.classList.toggle("active", item === button));
}));
connect();
