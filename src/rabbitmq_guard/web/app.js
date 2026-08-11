const state = {
  scenarios: [],
  selectedFile: null,
  currentRunId: null,
  baselineRunId: null,
  historyRuns: [],
  liveEnabled: false,
};

const elements = {
  modeTabs: [...document.querySelectorAll(".mode-tab")],
  panels: [...document.querySelectorAll("[data-panel]")],
  scenarioSelect: document.querySelector("#scenario-select"),
  scenarioDescription: document.querySelector("#scenario-description"),
  runScenario: document.querySelector("#run-scenario"),
  snapshotFile: document.querySelector("#snapshot-file"),
  fileLabel: document.querySelector("#file-label"),
  analyzeFile: document.querySelector("#analyze-file"),
  liveUrl: document.querySelector("#live-url"),
  liveUser: document.querySelector("#live-user"),
  livePassword: document.querySelector("#live-password"),
  liveDisabled: document.querySelector("#live-disabled"),
  runLive: document.querySelector("#run-live"),
  serverState: document.querySelector("#server-state"),
  resultKicker: document.querySelector("#result-kicker"),
  resultTitle: document.querySelector("#result-title"),
  resultMeta: document.querySelector("#result-meta"),
  counts: {
    critical: document.querySelector("#count-critical"),
    high: document.querySelector("#count-high"),
    medium: document.querySelector("#count-medium"),
    low: document.querySelector("#count-low"),
  },
  metrics: {
    connections: document.querySelector("#metric-connections"),
    channels: document.querySelector("#metric-channels"),
    queues: document.querySelector("#metric-queues"),
    consumers: document.querySelector("#metric-consumers"),
  },
  findingsCount: document.querySelector("#findings-count"),
  findingsList: document.querySelector("#findings-list"),
  historyList: document.querySelector("#history-list"),
  refreshHistory: document.querySelector("#refresh-history"),
  downloadReport: document.querySelector("#download-report"),
  comparisonSection: document.querySelector("#comparison-section"),
  comparisonTitle: document.querySelector("#comparison-title"),
  comparisonMeta: document.querySelector("#comparison-meta"),
  comparisonResolved: document.querySelector("#comparison-resolved"),
  comparisonNew: document.querySelector("#comparison-new"),
  comparisonPersisting: document.querySelector("#comparison-persisting"),
  comparisonMetrics: document.querySelector("#comparison-metrics"),
  downloadComparison: document.querySelector("#download-comparison"),
  clearBaseline: document.querySelector("#clear-baseline"),
  toast: document.querySelector("#toast"),
};

const severityNames = {
  critical: "严重",
  high: "高",
  medium: "中",
  low: "低",
};

const outcomeNames = {
  improved: "风险下降",
  worsened: "风险上升",
  mixed: "风险结构变化",
  unchanged: "风险无变化",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || `请求失败 (${response.status})`);
  }
  return payload;
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.remove("hidden");
  window.setTimeout(() => elements.toast.classList.add("hidden"), 4200);
}

function setBusy(button, busy) {
  button.disabled = busy;
  button.dataset.originalText ||= button.innerHTML;
  button.innerHTML = busy ? "处理中…" : button.dataset.originalText;
}

function setMode(mode) {
  elements.modeTabs.forEach((tab) => {
    const active = tab.dataset.mode === mode;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  elements.panels.forEach((panel) => panel.classList.toggle("hidden", panel.dataset.panel !== mode));
}

function updateScenarioDescription() {
  const scenario = state.scenarios.find((item) => item.id === elements.scenarioSelect.value);
  elements.scenarioDescription.textContent = scenario?.description || "";
}

function renderFinding(finding) {
  const evidence = (finding.evidence || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const actions = (finding.actions || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const sources = (finding.sources || [])
    .map((source, index) => `<a href="${escapeHtml(source)}" target="_blank" rel="noreferrer">依据 ${index + 1}</a>`)
    .join("");
  return `
    <article class="finding-card severity-${escapeHtml(finding.severity)}">
      <header class="finding-header">
        <div class="severity-label">${escapeHtml(severityNames[finding.severity] || finding.severity)}</div>
        <div class="finding-title">
          <strong>${escapeHtml(finding.title)}</strong>
          <div class="finding-target">${escapeHtml(finding.target)} · 置信度 ${escapeHtml(finding.confidence)}</div>
        </div>
      </header>
      <div class="finding-body">
        <p>${escapeHtml(finding.explanation)}</p>
        <div class="detail-label">证据</div>
        <ul class="evidence-list">${evidence}</ul>
        <div class="detail-label">建议动作</div>
        <ol class="action-list">${actions}</ol>
        <div class="detail-label">技术依据</div>
        <div class="source-links">${sources}</div>
      </div>
    </article>`;
}

function hideComparison() {
  elements.comparisonSection.classList.add("hidden");
  elements.downloadComparison.href = "#";
}

function formatDelta(value) {
  const number = Number(value || 0);
  return number > 0 ? `+${number}` : String(number);
}

function renderComparison(comparison) {
  const outcome = comparison.outcome || "unchanged";
  elements.comparisonSection.className = `comparison-band outcome-${outcome}`;
  elements.comparisonTitle.textContent = outcomeNames[outcome] || outcome;
  elements.comparisonMeta.textContent = `${comparison.baseline.label} → ${comparison.current.label} · 风险分 ${comparison.baseline.risk_score} → ${comparison.current.risk_score}`;
  elements.comparisonResolved.textContent = comparison.summary.resolved;
  elements.comparisonNew.textContent = comparison.summary.new;
  elements.comparisonPersisting.textContent = comparison.summary.persisting;
  elements.comparisonMetrics.innerHTML = comparison.metric_changes
    .map(
      (metric) => `
        <div>
          <span>${escapeHtml(metric.label)}</span>
          <strong>${escapeHtml(metric.baseline)} → ${escapeHtml(metric.current)}</strong>
          <small>${escapeHtml(formatDelta(metric.delta))}</small>
        </div>`,
    )
    .join("");
  elements.downloadComparison.href = `/api/compare/${comparison.baseline.id}/${comparison.current.id}/report.md`;
}

async function refreshComparison() {
  if (!state.baselineRunId || !state.currentRunId || state.baselineRunId === state.currentRunId) {
    hideComparison();
    return;
  }
  renderComparison(await api(`/api/compare/${state.baselineRunId}/${state.currentRunId}`));
}

function renderResult(result, kicker = "诊断完成") {
  const { summary, cluster, findings, run } = result;
  state.currentRunId = run?.id || null;
  hideComparison();
  elements.resultKicker.textContent = kicker;
  elements.resultTitle.textContent = cluster.name || "unknown";
  const timestamp = run?.created_at ? new Date(run.created_at).toLocaleString("zh-CN", { hour12: false }) : "未保存";
  elements.resultMeta.textContent = `${summary.total} 项结果 · ${timestamp}`;

  Object.entries(summary.counts).forEach(([key, value]) => {
    elements.counts[key].textContent = value;
  });
  Object.entries(elements.metrics).forEach(([key, element]) => {
    element.textContent = cluster[key] ?? "-";
  });

  elements.findingsCount.textContent = `${findings.length} 项`;
  elements.findingsList.innerHTML = findings.length
    ? findings.map(renderFinding).join("")
    : '<div class="healthy-state">未发现达到当前阈值的异常</div>';

  if (state.currentRunId) {
    elements.downloadReport.href = `/api/runs/${state.currentRunId}/report.md`;
    elements.downloadReport.classList.remove("hidden");
  } else {
    elements.downloadReport.classList.add("hidden");
  }
  document.querySelectorAll(".history-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.id === state.currentRunId);
  });
}

function renderHistory(runs) {
  state.historyRuns = runs;
  if (state.baselineRunId && !runs.some((run) => run.id === state.baselineRunId)) {
    state.baselineRunId = null;
  }
  if (!runs.length) {
    elements.historyList.innerHTML = '<div class="empty-state">暂无记录</div>';
    return;
  }
  elements.historyList.innerHTML = runs
    .map((run) => {
      const date = new Date(run.created_at).toLocaleString("zh-CN", { hour12: false });
      const isBaseline = run.id === state.baselineRunId;
      return `
        <div class="history-row ${isBaseline ? "baseline" : ""}">
          <button class="history-item ${run.id === state.currentRunId ? "active" : ""}" type="button" data-id="${escapeHtml(run.id)}">
            <span class="history-status ${escapeHtml(run.status)}" aria-hidden="true"></span>
            <strong>${escapeHtml(run.label)}</strong>
            <span>${escapeHtml(run.cluster_name)} · ${run.total} 项 · ${escapeHtml(date)}</span>
          </button>
          <button class="baseline-button" type="button" data-baseline-id="${escapeHtml(run.id)}" aria-pressed="${String(isBaseline)}" title="${isBaseline ? "取消对比基线" : "设为对比基线"}">基线</button>
        </div>`;
    })
    .join("");
  elements.historyList.querySelectorAll(".history-item").forEach((item) => {
    item.addEventListener("click", async () => {
      try {
        renderResult(await api(`/api/runs/${item.dataset.id}`), "历史诊断");
        await refreshComparison();
      } catch (error) {
        showToast(error.message);
      }
    });
  });
  elements.historyList.querySelectorAll(".baseline-button").forEach((button) => {
    button.addEventListener("click", async () => {
      state.baselineRunId = state.baselineRunId === button.dataset.baselineId ? null : button.dataset.baselineId;
      renderHistory(state.historyRuns);
      try {
        await refreshComparison();
      } catch (error) {
        hideComparison();
        showToast(error.message);
      }
    });
  });
}

async function refreshHistory() {
  const payload = await api("/api/runs?limit=30");
  renderHistory(payload.runs);
}

async function runScenario(persist = true) {
  setBusy(elements.runScenario, true);
  try {
    const result = await api("/api/analyze/scenario", {
      method: "POST",
      body: JSON.stringify({ id: elements.scenarioSelect.value, persist }),
    });
    renderResult(result, persist ? "演示诊断" : "演示预览");
    if (persist) {
      await refreshHistory();
      await refreshComparison();
    }
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(elements.runScenario, false);
  }
}

async function analyzeFile() {
  if (!state.selectedFile) return;
  setBusy(elements.analyzeFile, true);
  try {
    const snapshot = JSON.parse(await state.selectedFile.text());
    const result = await api("/api/analyze/snapshot", {
      method: "POST",
      body: JSON.stringify({ snapshot, label: state.selectedFile.name }),
    });
    renderResult(result, "快照诊断");
    await refreshHistory();
    await refreshComparison();
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(elements.analyzeFile, false);
  }
}

async function runLive() {
  setBusy(elements.runLive, true);
  try {
    const result = await api("/api/analyze/live", {
      method: "POST",
      body: JSON.stringify({
        url: elements.liveUrl.value,
        username: elements.liveUser.value,
        password: elements.livePassword.value,
      }),
    });
    elements.livePassword.value = "";
    renderResult(result, "实时诊断");
    await refreshHistory();
    await refreshComparison();
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(elements.runLive, false);
  }
}

async function initialize() {
  try {
    const [health, scenariosPayload] = await Promise.all([api("/api/health"), api("/api/scenarios")]);
    state.liveEnabled = health.live_enabled;
    elements.serverState.textContent = `本地 · v${health.version}`;
    elements.runLive.disabled = !state.liveEnabled;
    elements.liveDisabled.classList.toggle("hidden", state.liveEnabled);

    state.scenarios = scenariosPayload.scenarios;
    elements.scenarioSelect.innerHTML = state.scenarios
      .map((scenario) => `<option value="${escapeHtml(scenario.id)}">${escapeHtml(scenario.title)}</option>`)
      .join("");
    const defaultScenario = state.scenarios.find((item) => item.id === "growing_backlog") || state.scenarios[0];
    if (defaultScenario) elements.scenarioSelect.value = defaultScenario.id;
    updateScenarioDescription();
    await Promise.all([runScenario(false), refreshHistory()]);
  } catch (error) {
    showToast(error.message);
  }
}

elements.modeTabs.forEach((tab) => tab.addEventListener("click", () => setMode(tab.dataset.mode)));
elements.scenarioSelect.addEventListener("change", updateScenarioDescription);
elements.runScenario.addEventListener("click", () => runScenario(true));
elements.snapshotFile.addEventListener("change", () => {
  state.selectedFile = elements.snapshotFile.files[0] || null;
  elements.fileLabel.textContent = state.selectedFile?.name || "选择 JSON 快照";
  elements.analyzeFile.disabled = !state.selectedFile;
});
elements.analyzeFile.addEventListener("click", analyzeFile);
elements.runLive.addEventListener("click", runLive);
elements.refreshHistory.addEventListener("click", () => refreshHistory().catch((error) => showToast(error.message)));
elements.clearBaseline.addEventListener("click", () => {
  state.baselineRunId = null;
  renderHistory(state.historyRuns);
  hideComparison();
});

initialize();
