// The operations page. Vanilla ES2022, no framework, no external request (FR-025).
"use strict";

const POLL_INTERVAL_MS = 2000;
const HEALTH_INTERVAL_MS = 15000;

const state = {
  jobs: new Map(),      // job_id -> job, as returned by GET /api/jobs
  since: null,          // server_time of the last list response, for cheap polling
  selection: [],        // files chosen but not yet submitted
  batchId: null,        // the upload being watched, for aggregate progress
  openDetailJobId: null, // the job whose detail dialog is open, so the poll can refresh it
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
  clearAll: document.getElementById("clear-all"),
  modal: document.getElementById("modal"),
  modalTitle: document.getElementById("modal-title"),
  modalBody: document.getElementById("modal-body"),
};

// --- modal ----------------------------------------------------------------
// showModal() traps focus, closes on Escape, and returns focus to whatever was
// focused when it opened. Nothing here re-implements any of that.

function openModal(title, body) {
  el.modalTitle.textContent = title;
  el.modalBody.replaceChildren(body);
  if (!el.modal.open) el.modal.showModal();
}

function closeModal() {
  state.openDetailJobId = null;
  if (el.modal.open) el.modal.close();
}

// Escape and the backdrop close the dialog without going through closeModal().
el.modal.addEventListener("close", () => {
  state.openDetailJobId = null;
});

el.modal.addEventListener("click", (event) => {
  // A click on the backdrop lands on the dialog itself, never on its contents.
  if (event.target === el.modal || event.target.hasAttribute("data-close")) closeModal();
});

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
  el.clearAll.hidden = jobs.length === 0;
  el.rows.replaceChildren(...jobs.map(renderRow));
  renderBatchProgress(jobs);
  refreshOpenDetail();
}

function refreshOpenDetail() {
  // An open dialog must keep pace with the conversion behind it rather than showing the
  // state at open time (FR-011). Terminal jobs stop changing, so they need no re-fetch.
  const jobId = state.openDetailJobId;
  if (!jobId) return;
  const job = state.jobs.get(jobId);
  if (job && !IN_FLIGHT.has(job.status)) return;
  showDetail(jobId);
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
  file.title = job.filename;
  row.append(file);

  const status = document.createElement("td");
  status.className = `state state-${job.status}`;
  // `display_status` already carries the part counter for a split document (FR-037), so
  // the page never has to work out how to describe a state itself.
  status.textContent = job.display_status;
  row.append(status);

  row.append(renderDetail(job));
  row.append(renderActions(job));
  return row;
}

function renderDetail(job) {
  const cell = document.createElement("td");
  cell.className = "detail";
  const text = document.createElement("div");
  text.className = "clamp";
  if (job.failure_reason) {
    text.classList.add("reason");
    text.textContent = job.failure_reason;
  } else if (job.status === "succeeded_suspect") {
    // A conversion that produced almost nothing: still downloadable, but say so (FR-029).
    text.classList.add("caution");
    text.textContent =
      "The Markdown came out almost empty. Open it before importing — the source may be " +
      "a blank scan, or the text may not have been recognized.";
  } else if (job.status === "succeeded_incomplete") {
    // Some pages could not be converted. The file is there and downloadable, but it has a
    // hole in it and the person importing needs to know which pages (FR-035).
    text.classList.add("caution");
    const ranges = (job.missing_page_ranges || [])
      .map(([first, last]) => (first === last ? `${first}` : `${first}–${last}`))
      .join(", ");
    text.textContent = ranges
      ? `Converted, but pages ${ranges} are missing — those pages could not be converted. ` +
        "The gap is marked in the Markdown too."
      : "Converted, but some pages are missing from the result.";
  } else if (job.status === "already_converted") {
    // Not new work: this exact document is already in the output folder (FR-014).
    text.textContent = job.output_filename
      ? `Already in the output folder as ${job.output_filename} — converted earlier.`
      : "This document was already converted earlier.";
  } else if (job.engine_status === "partial_success") {
    text.classList.add("caution");
    text.textContent =
      "Converted, but parts of the document could not be fully read. Check those " +
      "sections before importing.";
  } else if (job.status === "queued" && job.queue_position !== null) {
    text.textContent = `Waiting — position ${job.queue_position} in the queue`;
  } else if (job.status === "queued" || job.status === "submitted") {
    text.textContent = "Waiting for a converter";
  } else if (job.status === "running") {
    text.textContent = "Converting…";
  } else {
    text.textContent = "";
  }
  cell.append(text);
  markIfClamped(cell, text, job);
  return cell;
}

function markIfClamped(cell, text, job) {
  // CSS hides the overflow but cannot report it, so the page measures after insertion.
  // The row is not in the document yet, so defer to the next frame.
  requestAnimationFrame(() => {
    if (text.scrollHeight <= text.clientHeight) return;
    const more = document.createElement("button");
    more.type = "button";
    more.className = "more";
    more.textContent = "More";
    more.addEventListener("click", () => showDetail(job.job_id));
    cell.append(more);
  });
}

function renderActions(job) {
  const cell = document.createElement("td");
  cell.className = "row-actions";
  const stack = document.createElement("div");
  stack.className = "actions-inner";
  cell.append(stack);

  if (job.download_url) {
    const link = document.createElement("a");
    link.className = "download";
    link.href = job.download_url;
    // Labelled, not named after the file: a section filename is long enough to have
    // been a width problem of its own. The name is in the title and in the detail view.
    link.textContent = "Download";
    link.title = job.output_filename || "";
    link.setAttribute("download", "");
    stack.append(link);
  }

  const details = document.createElement("button");
  details.type = "button";
  details.textContent = "Details";
  details.addEventListener("click", () => showDetail(job.job_id));
  stack.append(details);

  stack.append(renderDeleteButton(job));
  return cell;
}

// --- detail view (feature 002) --------------------------------------------

async function showDetail(jobId) {
  state.openDetailJobId = jobId;
  let detail;
  try {
    const response = await fetch(`/api/jobs/${jobId}`);
    if (!response.ok) throw new Error("not available");
    detail = await response.json();
  } catch (error) {
    openModal("Details", messageBlock("The server could not be reached. Try again."));
    return;
  }
  if (state.openDetailJobId !== jobId) return;
  openModal(detail.filename, renderDetailDialog(detail));
}

function renderDetailDialog(detail) {
  const body = document.createElement("div");

  const message = document.createElement("p");
  message.className = detail.failure_reason ? "full-message reason" : "full-message";
  message.textContent = fullMessage(detail);
  body.append(message);

  const facts = document.createElement("dl");
  facts.className = "facts";
  for (const [term, value] of factsOf(detail)) {
    if (value === null || value === undefined || value === "") continue;
    const dt = document.createElement("dt");
    dt.textContent = term;
    const dd = document.createElement("dd");
    dd.textContent = value;
    facts.append(dt, dd);
  }
  body.append(facts);

  const missing = detail.missing_parts || [];
  if (missing.length) {
    // A gap with no reason attached is the same sentence whether the engine ran out of
    // time or the pages were unreadable — and only one of those has an answer.
    const heading = document.createElement("p");
    heading.textContent =
      missing.length === 1 ? "Missing pages" : `Missing pages (${missing.length} ranges)`;
    body.append(heading);
    const list = document.createElement("ul");
    list.className = "detail-files";
    for (const part of missing) {
      const item = document.createElement("li");
      const pages =
        part.first_page === part.last_page
          ? `Page ${part.first_page}`
          : `Pages ${part.first_page}\u2013${part.last_page}`;
      const attempts = part.attempts > 1 ? ` (${part.attempts} attempts)` : "";
      item.textContent = `${pages}${attempts}: ${part.failure_reason || "no reason recorded"}`;
      list.append(item);
    }
    body.append(list);
  }

  const files = detail.document_outputs || [];
  if (files.length) {
    const heading = document.createElement("p");
    heading.textContent = files.length === 1 ? "Output file" : `Output files (${files.length})`;
    body.append(heading);
    const list = document.createElement("ul");
    list.className = "detail-files";
    for (const file of files) {
      const item = document.createElement("li");
      const title = file.section_title ? `${file.section_title} — ` : "";
      item.textContent = `${title}${file.filename} (${formatBytes(file.bytes)})`;
      list.append(item);
    }
    body.append(list);
  }

  if (detail.engine_errors && detail.engine_errors.length) {
    const errors = document.createElement("pre");
    errors.className = "engine-errors";
    errors.textContent = detail.engine_errors.join("\n");
    body.append(errors);
  }

  return body;
}

function fullMessage(detail) {
  // The complete text the row had to cut short, with no truncation of any kind (FR-009).
  if (detail.failure_reason) return detail.failure_reason;
  if (detail.status === "succeeded_suspect") {
    return "The Markdown came out almost empty. Open it before importing it.";
  }
  if (detail.status === "succeeded_incomplete") {
    const ranges = (detail.missing_page_ranges || [])
      .map(([first, last]) => (first === last ? `${first}` : `${first}–${last}`))
      .join(", ");
    return `Converted, but pages ${ranges} are missing — those parts failed.`;
  }
  if (detail.status === "already_converted") {
    return "This document was already converted earlier; the Markdown is in the output folder.";
  }
  if (detail.engine_status === "partial_success") {
    return "Converted, but parts of the document could not be fully read.";
  }
  return "Converted.";
}

function factsOf(detail) {
  return [
    ["Status", detail.display_status],
    ["Queue position", detail.queue_position],
    ["Parts", detail.part_count > 1 ? `${detail.parts_completed} of ${detail.part_count}` : null],
    ["Size", formatBytes(detail.size_bytes)],
    ["Pages", detail.page_count],
    ["Attempt", detail.attempt],
    ["Submitted", formatTime(detail.created_at)],
    ["Started", formatTime(detail.started_at)],
    ["Finished", formatTime(detail.ended_at)],
    ["Took", detail.processing_seconds === null ? null : `${detail.processing_seconds}s`],
    ["Uploaded copy", detail.retained_upload ? "still on the server" : "no longer held"],
  ];
}

function formatBytes(value) {
  if (value === null || value === undefined) return null;
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  if (value >= 1024) return `${Math.round(value / 1024)} KB`;
  return `${value} bytes`;
}

function formatTime(value) {
  if (!value) return null;
  return new Date(value).toLocaleString();
}

// --- deletion (feature 002) -----------------------------------------------

const IN_FLIGHT = new Set(["queued", "submitted", "running"]);
// Only these block a delete. A queued job has never reached the engine, so removing its
// row simply takes it out of the queue — and a job the dispatcher never picks up has to
// stay removable.
const CONVERTING = new Set(["submitted", "running"]);

function siblingsOf(job) {
  // Every conversion of the same document that this page currently knows about.
  return Array.from(state.jobs.values()).filter((other) => other.content_hash === job.content_hash);
}

function renderDeleteButton(job) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "delete";
  button.textContent = "Delete";

  const busy = siblingsOf(job).find((other) => CONVERTING.has(other.status));
  if (busy) {
    // Visible but unavailable, with the reason: an absent control is indistinguishable
    // from the feature being missing (FR-019). The server refuses it too (FR-022).
    button.disabled = true;
    button.title = `"${job.filename}" is being converted right now. Wait for it to finish, then delete it.`;
    return button;
  }

  button.addEventListener("click", () => confirmDelete(job));
  return button;
}

async function confirmDelete(job) {
  // The confirmation has to say exactly what will go, so it asks rather than guesses.
  // The entry count comes from a filtered query, not from the rows on screen: the list is
  // capped by `limit` and an older sibling would go uncounted (X5).
  let entries;
  let detail;
  try {
    const [listResponse, detailResponse] = await Promise.all([
      fetch(`/api/jobs?content_hash=${encodeURIComponent(job.content_hash)}`),
      fetch(`/api/jobs/${job.job_id}`),
    ]);
    if (!listResponse.ok || !detailResponse.ok) throw new Error("lookup failed");
    entries = (await listResponse.json()).jobs.length;
    detail = await detailResponse.json();
  } catch (error) {
    openModal("Delete", messageBlock("The server could not be reached, so there is nothing to confirm. Try again."));
    return;
  }
  openModal(`Delete "${job.filename}"?`, confirmationBody(job, detail, entries));
}

function confirmationBody(job, detail, entries) {
  const body = document.createElement("div");
  body.className = "confirm";

  const what = document.createElement("p");
  what.textContent =
    entries > 1
      ? `This removes all ${entries} entries for this document, and everything it produced.`
      : "This removes the entry and everything it produced.";
  body.append(what);

  // Every file for the document, whichever conversion wrote it. Built from `outputs`
  // instead, an already-converted row would promise to remove nothing (X4).
  const files = detail.document_outputs || [];
  if (files.length) {
    const heading = document.createElement("p");
    heading.textContent = files.length === 1 ? "This Markdown file:" : `These ${files.length} Markdown files:`;
    body.append(heading);
    const list = document.createElement("ul");
    for (const file of files) {
      const item = document.createElement("li");
      item.textContent = file.filename;
      list.append(item);
    }
    body.append(list);
  } else {
    body.append(messageBlock("No Markdown was produced for this document, so there is none to remove."));
  }

  if (detail.retained_upload) {
    body.append(messageBlock("The uploaded PDF held on the server is discarded too."));
  }

  const warn = document.createElement("p");
  warn.className = "warn";
  warn.textContent = "This cannot be undone.";
  body.append(warn);

  const choices = document.createElement("div");
  choices.className = "choices";

  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "ghost";
  cancel.textContent = "Cancel";
  cancel.addEventListener("click", closeModal);

  const confirm = document.createElement("button");
  confirm.type = "button";
  confirm.className = "danger";
  confirm.textContent = "Delete";
  confirm.addEventListener("click", () => performDelete(job, confirm));

  choices.append(cancel, confirm);
  body.append(choices);

  // Cancel is the default outcome, so it is what a stray Enter or Space lands on (FR-015).
  requestAnimationFrame(() => cancel.focus());
  return body;
}

async function performDelete(job, button) {
  button.disabled = true;
  button.textContent = "Deleting…";
  let payload;
  let ok;
  try {
    const response = await fetch(`/api/jobs/${job.job_id}`, { method: "DELETE" });
    ok = response.ok;
    payload = await response.json();
    if (!ok && response.status === 404) {
      // Another tab got there first, or the history was pruned. The row is gone either
      // way, which is the outcome the operator asked for.
      forget([job.job_id]);
      closeModal();
      return;
    }
  } catch (error) {
    reportOutcome("The server could not be reached. Nothing was deleted.", true);
    return;
  }

  if (!ok) {
    reportOutcome(messageOf(payload), true);
    return;
  }

  // `job_ids` is authoritative: if it names entries the confirmation did not predict,
  // those rows go too (X6).
  forget(payload.job_ids);
  refreshHealth();
  closeModal();

  if (payload.kept_files.length) {
    showRejections([
      {
        filename: payload.filename,
        reason:
          `Removed from the list, but ${payload.kept_files.length} file(s) could not be ` +
          `deleted from the output folder: ${payload.kept_files.join(", ")}. ` +
          "The output-folder count no longer matches the folder.",
      },
    ]);
  }
}

el.clearAll.addEventListener("click", confirmClearAll);

async function confirmClearAll() {
  // Counted from the whole list, and stated before anything is destroyed: this removes
  // successful conversions and their Markdown too, which is the part worth being sure of.
  const jobs = sortedJobs();
  const documents = new Set(jobs.map((job) => job.content_hash)).size;
  const withOutput = jobs.filter((job) => job.output_filename).length;
  openModal("Clear the whole list?", clearAllBody(jobs.length, documents, withOutput));
}

function clearAllBody(entries, documents, withOutput) {
  const body = document.createElement("div");
  body.className = "confirm";

  const what = document.createElement("p");
  what.append(
    document.createTextNode("This removes all "),
    countSpan(entries),
    document.createTextNode(entries === 1 ? " entry" : " entries"),
    document.createTextNode(", covering "),
    countSpan(documents),
    document.createTextNode(documents === 1 ? " document" : " documents"),
    document.createTextNode(".")
  );
  body.append(what);

  if (withOutput) {
    const files = document.createElement("p");
    files.append(
      countSpan(withOutput),
      document.createTextNode(
        withOutput === 1
          ? " converted document's Markdown is deleted from the output folder — successful conversions go too, not only the failures."
          : " converted documents' Markdown is deleted from the output folder — successful conversions go too, not only the failures."
      )
    );
    body.append(files);
  } else {
    body.append(messageBlock("No Markdown has been produced yet, so there is none to remove."));
  }

  body.append(messageBlock("Uploaded PDFs still held on the server are discarded as well."));

  const warn = document.createElement("p");
  warn.className = "warn";
  warn.textContent = "This cannot be undone.";
  body.append(warn);

  const choices = document.createElement("div");
  choices.className = "choices";

  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "ghost";
  cancel.textContent = "Cancel";
  cancel.addEventListener("click", closeModal);

  const confirm = document.createElement("button");
  confirm.type = "button";
  confirm.className = "danger";
  confirm.textContent = "Delete everything";
  confirm.addEventListener("click", () => performClearAll(confirm));

  choices.append(cancel, confirm);
  body.append(choices);
  requestAnimationFrame(() => cancel.focus());
  return body;
}

function countSpan(value) {
  const span = document.createElement("span");
  span.className = "count";
  span.textContent = String(value);
  return span;
}

async function performClearAll(button) {
  button.disabled = true;
  button.textContent = "Deleting…";
  let payload;
  try {
    const response = await fetch("/api/jobs", { method: "DELETE" });
    if (!response.ok) throw new Error("failed");
    payload = await response.json();
  } catch (error) {
    reportOutcome("The server could not be reached. Nothing was deleted.", true);
    return;
  }

  forget(payload.job_ids);
  refreshHealth();
  closeModal();

  const notes = [];
  for (const entry of payload.skipped) {
    notes.push({ filename: entry.filename, reason: `Kept — ${entry.reason}.` });
  }
  if (payload.kept_files.length) {
    notes.push({
      filename: "Output folder",
      reason:
        `${payload.kept_files.length} file(s) could not be deleted: ` +
        `${payload.kept_files.join(", ")}. The output-folder count no longer matches the folder.`,
    });
  }
  if (notes.length) showRejections(notes);
}

function forget(jobIds) {
  for (const id of jobIds) state.jobs.delete(id);
  render();
}

function reportOutcome(message, bad) {
  const note = document.createElement("p");
  note.className = bad ? "outcome bad" : "outcome";
  note.textContent = message;
  el.modalBody.append(note);
}

function messageBlock(text) {
  const paragraph = document.createElement("p");
  paragraph.textContent = text;
  return paragraph;
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
