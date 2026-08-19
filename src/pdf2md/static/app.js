// The operations page. Vanilla ES2022, no framework, no external request (FR-025).
"use strict";

const POLL_INTERVAL_MS = 2000;
const HEALTH_INTERVAL_MS = 15000;

const state = {
  jobs: new Map(),      // job_id -> job, as returned by GET /api/jobs
  since: null,          // server_time of the last list response, for cheap polling
  selection: [],        // files chosen but not yet submitted
  batchId: null,        // the upload being watched, for aggregate progress
};

const el = {
  form: document.getElementById("upload-form"),
  fileInput: document.getElementById("file-input"),
  noteInput: document.getElementById("note-input"),
  dropzone: document.getElementById("dropzone"),
  submit: document.getElementById("submit-button"),
  selection: document.getElementById("selection"),
  rejections: document.getElementById("rejections"),
  progress: document.getElementById("batch-progress"),
  rows: document.getElementById("job-rows"),
  empty: document.getElementById("empty"),
  health: document.getElementById("health"),
};

// --- upload ---------------------------------------------------------------

function setSelection(files) {
  state.selection = Array.from(files || []);
  el.submit.disabled = state.selection.length === 0;
  if (state.selection.length === 0) {
    el.selection.textContent = "";
  } else if (state.selection.length === 1) {
    el.selection.textContent = state.selection[0].name;
  } else {
    el.selection.textContent = `${state.selection.length} files selected`;
  }
}

el.fileInput.addEventListener("change", (event) => setSelection(event.target.files));

for (const name of ["dragenter", "dragover"]) {
  el.dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    el.dropzone.classList.add("over");
  });
}
for (const name of ["dragleave", "drop"]) {
  el.dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    el.dropzone.classList.remove("over");
  });
}
el.dropzone.addEventListener("drop", (event) => {
  if (event.dataTransfer && event.dataTransfer.files.length) {
    setSelection(event.dataTransfer.files);
  }
});

el.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.selection.length === 0) return;

  const body = new FormData();
  for (const file of state.selection) body.append("files", file, file.name);
  if (el.noteInput.value.trim()) body.append("note", el.noteInput.value.trim());

  el.submit.disabled = true;
  el.submit.textContent = "Sending…";
  try {
    const response = await fetch("/api/uploads", { method: "POST", body });
    const payload = await response.json();
    if (!response.ok) {
      showRejections([{ filename: "Upload", reason: messageOf(payload) }]);
      return;
    }
    state.batchId = payload.batch_id || null;
    showRejections(payload.rejected || []);
    el.fileInput.value = "";
    el.noteInput.value = "";
    setSelection([]);
    await refreshJobs({ full: true });
  } catch (error) {
    showRejections([{ filename: "Upload", reason: "The server could not be reached." }]);
  } finally {
    el.submit.textContent = "Convert";
    el.submit.disabled = state.selection.length === 0;
  }
});

function messageOf(payload) {
  return (payload && payload.error && payload.error.message) || "The upload was not accepted.";
}

function showRejections(rejections) {
  el.rejections.replaceChildren();
  for (const rejection of rejections) {
    const item = document.createElement("li");
    item.textContent = `${rejection.filename}: ${rejection.reason}`;
    el.rejections.append(item);
  }
}

// --- status ---------------------------------------------------------------

async function refreshJobs({ full = false } = {}) {
  const query = !full && state.since ? `?since=${encodeURIComponent(state.since)}` : "";
  const response = await fetch(`/api/jobs${query}`);
  if (!response.ok) return;
  const payload = await response.json();
  state.since = payload.server_time;
  for (const job of payload.jobs) state.jobs.set(job.job_id, job);
  render();
}

function sortedJobs() {
  // Newest first, and stable: a list must not reorder while jobs finish.
  return Array.from(state.jobs.values()).sort((a, b) => {
    if (a.created_at === b.created_at) return a.job_id < b.job_id ? -1 : 1;
    return a.created_at < b.created_at ? 1 : -1;
  });
}

function render() {
  const jobs = sortedJobs();
  el.empty.hidden = jobs.length > 0;
  el.rows.replaceChildren(...jobs.map(renderRow));
  renderBatchProgress(jobs);
}

const TERMINAL = new Set([
  "succeeded",
  "succeeded_suspect",
  "already_converted",
  "failed",
  "timed_out",
]);

function renderBatchProgress(jobs) {
  // Counts for the batch this browser submitted, so someone who uploaded 50 documents
  // and walked away can see at a glance how far it got.
  if (!state.batchId) {
    el.progress.textContent = "";
    return;
  }
  const batch = jobs.filter((job) => job.batch_id === state.batchId);
  if (batch.length === 0) {
    el.progress.textContent = "";
    return;
  }

  const counts = { done: 0, converting: 0, waiting: 0, failed: 0 };
  for (const job of batch) {
    if (job.status === "failed" || job.status === "timed_out") counts.failed += 1;
    else if (TERMINAL.has(job.status)) counts.done += 1;
    else if (job.status === "queued") counts.waiting += 1;
    else counts.converting += 1;
  }

  const parts = [];
  if (counts.done) parts.push(`${counts.done} converted`);
  if (counts.converting) parts.push(`${counts.converting} converting`);
  if (counts.waiting) parts.push(`${counts.waiting} waiting`);
  if (counts.failed) parts.push(`${counts.failed} failed`);
  const finished = counts.done + counts.failed;
  const suffix = finished === batch.length ? " — this upload is finished" : "";
  el.progress.textContent = `${parts.join(" · ")} of ${batch.length}${suffix}`;
}

function renderRow(job) {
  const row = document.createElement("tr");
  row.id = `job-${job.job_id}`;

  const file = document.createElement("td");
  file.className = "file";
  file.textContent = job.filename;
  row.append(file);

  const status = document.createElement("td");
  status.className = `state state-${job.status}`;
  // `display_status` already carries the part counter for a split document (FR-037), so
  // the page never has to work out how to describe a state itself.
  status.textContent = job.display_status;
  row.append(status);

  row.append(renderDetail(job));
  row.append(renderDownload(job));
  return row;
}

function renderDetail(job) {
  const cell = document.createElement("td");
  cell.className = "detail";
  if (job.failure_reason) {
    cell.classList.add("reason");
    cell.textContent = job.failure_reason;
  } else if (job.status === "succeeded_suspect") {
    // A conversion that produced almost nothing: still downloadable, but say so (FR-029).
    cell.classList.add("caution");
    cell.textContent =
      "The Markdown came out almost empty. Open it before importing — the source may be " +
      "a blank scan, or the text may not have been recognized.";
  } else if (job.status === "succeeded_incomplete") {
    // Some pages could not be converted. The file is there and downloadable, but it has a
    // hole in it and the person importing needs to know which pages (FR-035).
    cell.classList.add("caution");
    const ranges = (job.missing_page_ranges || [])
      .map(([first, last]) => (first === last ? `${first}` : `${first}–${last}`))
      .join(", ");
    cell.textContent = ranges
      ? `Converted, but pages ${ranges} are missing — those pages could not be converted. ` +
        "The gap is marked in the Markdown too."
      : "Converted, but some pages are missing from the result.";
  } else if (job.status === "already_converted") {
    // Not new work: this exact document is already in the output folder (FR-014).
    cell.textContent = job.output_filename
      ? `Already in the output folder as ${job.output_filename} — converted earlier.`
      : "This document was already converted earlier.";
  } else if (job.engine_status === "partial_success") {
    cell.classList.add("caution");
    cell.textContent =
      "Converted, but parts of the document could not be fully read. Check those " +
      "sections before importing.";
  } else if (job.status === "queued" && job.queue_position !== null) {
    cell.textContent = `Waiting — position ${job.queue_position} in the queue`;
  } else if (job.status === "queued" || job.status === "submitted") {
    cell.textContent = "Waiting for a converter";
  } else if (job.status === "running") {
    cell.textContent = "Converting…";
  } else {
    cell.textContent = "";
  }
  return cell;
}

function renderDownload(job) {
  const cell = document.createElement("td");
  if (!job.download_url) return cell;
  const link = document.createElement("a");
  link.className = "download";
  link.href = job.download_url;
  link.textContent = job.output_filename || "Download";
  link.setAttribute("download", "");
  cell.append(link);
  return cell;
}

// --- health ---------------------------------------------------------------

async function refreshHealth() {
  try {
    const response = await fetch("/api/health");
    const payload = await response.json();
    const backlog = payload.backlog || { queued: 0, converting: 0 };
    const parts = [];
    parts.push(payload.engine && payload.engine.reachable ? "Converter ready" : "Converter offline");
    if (backlog.queued || backlog.converting) {
      parts.push(`${backlog.converting} converting, ${backlog.queued} waiting`);
    }
    if (payload.outbox) parts.push(`${payload.outbox.documents} in the output folder`);
    el.health.textContent = parts.join(" · ");
    el.health.className = `health ${payload.status === "ok" ? "ok" : "degraded"}`;
  } catch (error) {
    el.health.textContent = "Cannot reach the server";
    el.health.className = "health down";
  }
}

// --- start ----------------------------------------------------------------

setSelection([]);
refreshJobs({ full: true });
refreshHealth();
setInterval(refreshJobs, POLL_INTERVAL_MS);
setInterval(refreshHealth, HEALTH_INTERVAL_MS);
