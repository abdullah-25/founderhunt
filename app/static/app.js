const USER_ID_KEY = "founderhunt_user_id";

function getUserId() {
  let id = localStorage.getItem(USER_ID_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(USER_ID_KEY, id);
  }
  return id;
}

const headers = () => ({
  "Content-Type": "application/json",
  "X-User-Id": getUserId(),
});

let currentSearchId = null;
let pollTimer = null;
let latestResults = [];

const statusEl = document.getElementById("status");
const quotaEl = document.getElementById("quota");
const checkpointBanner = document.getElementById("checkpoint-banner");
const sourceBreakdown = document.getElementById("source-breakdown");
const resultsBody = document.querySelector("#results-table tbody");
const exportBtn = document.getElementById("export-btn");
const searchBtn = document.getElementById("search-btn");
const ycFiltersEl = document.getElementById("yc-filters");
const ycSourceEl = document.getElementById("yc-source");

function syncYcFiltersVisibility() {
  if (!ycFiltersEl || !ycSourceEl) return;
  ycFiltersEl.classList.toggle("hidden", !ycSourceEl.checked);
}

document.querySelectorAll('input[name="source"]').forEach((el) => {
  el.addEventListener("change", syncYcFiltersVisibility);
});
syncYcFiltersVisibility();

function setStatus(status, message) {
  statusEl.className = `status status-${status}`;
  statusEl.textContent = message || status.replace(/_/g, " ");
}

async function fetchQuota() {
  const res = await fetch("/api/quota", { headers: headers() });
  if (!res.ok) return;
  const data = await res.json();
  if (data.enabled === false) {
    quotaEl.textContent = "Daily quota: suspended";
    return;
  }
  quotaEl.textContent = `Daily quota: ${data.remaining} of ${data.limit} searches remaining`;
}

function renderResults(results) {
  latestResults = results;
  resultsBody.innerHTML = "";
  if (!results.length) {
    resultsBody.innerHTML = "<tr><td colspan='8'>No results yet.</td></tr>";
    exportBtn.disabled = true;
    return;
  }
  exportBtn.disabled = false;
  for (const job of results) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(job.title)}</td>
      <td>${escapeHtml(job.company)}</td>
      <td>${escapeHtml(job.stage)}</td>
      <td>${escapeHtml((job.tech_stack || []).join(", "))}</td>
      <td>${escapeHtml(job.compensation || "—")}</td>
      <td>${escapeHtml(job.summary)}</td>
      <td>${escapeHtml(job.source)}</td>
      <td><a href="${escapeAttr(job.url)}" target="_blank" rel="noopener">Open</a></td>
    `;
    resultsBody.appendChild(tr);
  }
}

function renderSourceBreakdown(sourceStatuses) {
  if (!sourceStatuses || !sourceStatuses.length) {
    sourceBreakdown.classList.add("hidden");
    return;
  }
  sourceBreakdown.classList.remove("hidden");
  sourceBreakdown.innerHTML = sourceStatuses
    .map(
      (s) =>
        `<div><strong>${s.source}</strong>: ${s.outcome}, ${s.jobs_found} jobs, ${s.walls_hit} wall(s), ${s.elapsed_seconds}s</div>`
    )
    .join("");
}

function renderCheckpoint(data) {
  if (data.status === "needs_attention" && data.checkpoint_message) {
    checkpointBanner.classList.remove("hidden");
    checkpointBanner.textContent = data.checkpoint_message;
  } else {
    checkpointBanner.classList.add("hidden");
    checkpointBanner.textContent = "";
  }
}

function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapeAttr(str) {
  return String(str).replaceAll('"', "&quot;");
}

async function pollSearch() {
  if (!currentSearchId) return;
  const res = await fetch(`/api/search/${currentSearchId}`, { headers: headers() });
  if (!res.ok) return;
  const data = await res.json();
  setStatus(data.status, data.status.replace(/_/g, " "));
  renderResults(data.results);
  renderSourceBreakdown(data.source_statuses);
  renderCheckpoint(data);

  const done = ["complete", "partial", "failed"].includes(data.status);
  if (done) {
    clearInterval(pollTimer);
    pollTimer = null;
    searchBtn.disabled = false;
    await fetchQuota();
  }
}

document.getElementById("search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const query = document.getElementById("query").value.trim();
  const locationField = document.getElementById("location");
  const location = locationField ? locationField.value.trim() : "";
  const stages = [...document.querySelectorAll('input[name="stage"]:checked')].map((el) => el.value);
  const sources = [...document.querySelectorAll('input[name="source"]:checked')].map((el) => el.value);
  const yc_filters = {
    role: document.getElementById("yc-role").value,
    commitment: document.getElementById("yc-commitment").value,
    remote: document.getElementById("yc-remote").value,
  };

  if (!query || !stages.length || !sources.length) {
    alert("Please fill query, at least one stage, and one source.");
    return;
  }

  searchBtn.disabled = true;
  setStatus("pending", "Submitting…");

  const res = await fetch("/api/search", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({
      query,
      location: location || null,
      stages,
      sources,
      yc_filters,
    }),
  });

  if (res.status === 429) {
    alert("Daily search quota exceeded.");
    searchBtn.disabled = false;
    setStatus("idle", "Quota exceeded");
    await fetchQuota();
    return;
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(err.detail || "Search failed");
    searchBtn.disabled = false;
    setStatus("failed", "Failed");
    return;
  }

  const data = await res.json();
  currentSearchId = data.search_id;
  setStatus("running", "Running");
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollSearch, 2000);
  await pollSearch();
  await fetchQuota();
});

exportBtn.addEventListener("click", () => {
  if (!latestResults.length) return;
  const cols = ["title", "company", "stage", "tech_stack", "compensation", "summary", "source", "url"];
  const header = ["Job Title", "Startup", "Stage", "Tech Stack", "Compensation", "Summary", "Source", "Link"];
  const rows = latestResults.map((job) =>
    cols.map((c) => {
      let val = job[c];
      if (c === "tech_stack") val = (val || []).join("; ");
      val = val == null ? "" : String(val);
      return `"${val.replaceAll('"', '""')}"`;
    })
  );
  const csv = [header.join(","), ...rows.map((r) => r.join(","))].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "founderhunt-results.csv";
  a.click();
  URL.revokeObjectURL(url);
});

fetchQuota();
