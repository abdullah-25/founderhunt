"use strict";

// Stable per-browser user id for the X-User-Id header (quota identity).
const USER_ID = (() => {
  let id = localStorage.getItem("founderhunt_uid");
  if (!id) {
    id = "u-" + Math.random().toString(36).slice(2, 12);
    localStorage.setItem("founderhunt_uid", id);
  }
  return id;
})();

const $ = (sel) => document.querySelector(sel);
const STAGE_LABELS = {
  pre_seed: "Pre-seed", seed: "Seed", series_a: "Series A",
  series_b: "Series B", series_c_plus: "Series C+", unknown: "Unknown",
};
const SOURCE_LABELS = { google: "Google", yc: "Y Combinator" };
const TERMINAL = new Set(["complete", "partial", "failed"]);

let currentSearchId = null;
let pollTimer = null;

function checked(name) {
  return [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((e) => e.value);
}

function setQuota(q) {
  const el = $("#quota");
  if (!q.enabled) {
    el.textContent = "Daily quota: suspended";
  } else {
    el.textContent = `Daily quota: ${q.remaining} / ${q.limit} left`;
  }
}

async function refreshQuota() {
  try {
    const res = await fetch("/api/quota", { headers: { "X-User-Id": USER_ID } });
    setQuota(await res.json());
  } catch (_) { /* non-fatal */ }
}

function statusText(status) {
  switch (status) {
    case "pending": return "Queued — starting the worker.";
    case "running": return "Scraping sources. A browser window will open.";
    case "needs_attention": return "A source needs you — finish in the open browser, then click Continue below.";
    case "complete": return "Search complete.";
    case "partial": return "Search finished with partial results.";
    case "failed": return "Search failed — no jobs found.";
    default: return status;
  }
}

function renderBreakdown(sources) {
  const wrap = $("#sources-breakdown");
  wrap.innerHTML = "";
  for (const s of sources) {
    const card = document.createElement("div");
    card.className = "source-card";
    let line = `${s.jobs_found} job(s) · ${s.walls_hit} wall(s)`;
    if (s.elapsed_seconds) line += ` · ${s.elapsed_seconds}s`;
    let msg = s.message || "";
    if (s.wall_active && s.seconds_remaining != null) {
      msg = `${msg} ${s.seconds_remaining}s remaining.`;
    }
    card.innerHTML = `
      <h3><span class="dot ${s.outcome}"></span>${SOURCE_LABELS[s.source] || s.source}</h3>
      <div class="stat-line">${line}</div>
      <div class="msg">${escapeHtml(msg)}</div>`;
    if (s.wall_active) {
      const btn = document.createElement("button");
      btn.className = "resume-btn";
      btn.textContent = s.source === "yc"
        ? "I've signed in — continue"
        : "I've cleared it — continue";
      btn.onclick = (e) => { e.target.disabled = true; continueSource(s.source); };
      card.appendChild(btn);
    } else if (s.outcome === "needs_attention") {
      const btn = document.createElement("button");
      btn.className = "resume-btn";
      btn.textContent = "Retry this source";
      btn.onclick = () => resumeSource(s.source);
      card.appendChild(btn);
    }
    wrap.appendChild(card);
  }
}

function renderJobs(jobs) {
  const tbody = $("#results-table tbody");
  tbody.innerHTML = "";
  $("#result-count").textContent = jobs.length;
  $("#empty-results").classList.toggle("hidden", jobs.length > 0);
  for (const j of jobs) {
    const tr = document.createElement("tr");
    const tech = (j.tech_stack || []).map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join("");
    tr.innerHTML = `
      <td>${escapeHtml(j.title)}</td>
      <td>${escapeHtml(j.company)}</td>
      <td><span class="stage-badge stage-${j.stage}">${STAGE_LABELS[j.stage] || j.stage}</span></td>
      <td>${tech || "<span class='muted-cell'>—</span>"}</td>
      <td>${escapeHtml(j.compensation || "—")}</td>
      <td>${escapeHtml(j.summary || "")}</td>
      <td>${escapeHtml(SOURCE_LABELS[j.source] || j.source)}</td>
      <td><a href="${encodeURI(j.url)}" target="_blank" rel="noopener">Open</a></td>`;
    tbody.appendChild(tr);
  }
}

function escapeHtml(str) {
  return String(str ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function render(data) {
  $("#status-panel").classList.remove("hidden");
  $("#results-panel").classList.remove("hidden");
  const banner = $("#status-banner");
  banner.className = "banner " + data.status;
  banner.textContent = statusText(data.status);
  renderBreakdown(data.sources_breakdown || []);
  renderJobs(data.jobs || []);
}

async function poll() {
  if (!currentSearchId) return;
  try {
    const res = await fetch(`/api/search/${currentSearchId}`);
    if (!res.ok) return;
    const data = await res.json();
    render(data);
    if (TERMINAL.has(data.status)) {
      stopPolling();
      $("#submit-btn").disabled = false;
      refreshQuota();
    }
  } catch (_) { /* keep polling */ }
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

async function resumeSource(source) {
  if (!currentSearchId) return;
  await fetch(`/api/search/${currentSearchId}/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source }),
  });
  if (!pollTimer) pollTimer = setInterval(poll, 2000);
  poll();
}

async function continueSource(source) {
  // Tell the worker the wall is cleared (signed in / captcha solved).
  if (!currentSearchId) return;
  try {
    await fetch(`/api/search/${currentSearchId}/continue`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source }),
    });
  } catch (_) { /* the next poll will reflect the new state */ }
  poll();
}

$("#search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const stages = checked("stages");
  const sources = checked("sources");
  if (!$("#query").value.trim() || !stages.length || !sources.length) {
    alert("Enter a query and select at least one stage and one source.");
    return;
  }
  const payload = {
    query: $("#query").value.trim(),
    location: $("#location").value.trim() || null,
    stages,
    sources,
    yc_filters: {
      role: $("select[name=yc_role]").value,
      commitment: $("select[name=yc_commitment]").value,
      remote: $("input[name=yc_remote]").checked,
    },
  };

  $("#submit-btn").disabled = true;
  try {
    const res = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": USER_ID },
      body: JSON.stringify(payload),
    });
    if (res.status === 429) {
      alert("Daily search quota exceeded.");
      $("#submit-btn").disabled = false;
      return;
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert("Search rejected: " + (JSON.stringify(err.detail) || res.status));
      $("#submit-btn").disabled = false;
      return;
    }
    const data = await res.json();
    currentSearchId = data.search_id;
    stopPolling();
    pollTimer = setInterval(poll, 2000);
    poll();
  } catch (err) {
    alert("Could not submit search: " + err);
    $("#submit-btn").disabled = false;
  }
});

$("#export-btn").addEventListener("click", () => {
  if (currentSearchId) {
    window.location = `/api/search/${currentSearchId}/export.csv`;
  }
});

function syncYCFilters() {
  const ycOn = document.querySelector('input[name="sources"][value="yc"]').checked;
  $("#yc-filters").classList.toggle("hidden", !ycOn);
}
document.querySelectorAll('input[name="sources"]').forEach((el) =>
  el.addEventListener("change", syncYCFilters)
);

syncYCFilters();
refreshQuota();
