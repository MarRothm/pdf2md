# Feature Specification: Fixed-Width Document List with Detail on Demand and Conversion Deletion

**Feature Branch**: `002-job-list-layout-delete`

**Created**: 2026-08-19

**Status**: Draft

**Input**: User description: "make the web frontend more convenient. 1. the status column is growing with the content making the page too broad. Implement a fixed layout with multiline preview and detailed view if needed. 2. allow deletion of conversions cleaning the output data. Ask for validation."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Read the document list without fighting the page (Priority: P1)

An operator submits a batch of PDFs and watches the documents list. Some conversions fail with a long explanation, some finish with a caution about missing pages, and some are simply waiting. Whatever the wording of those messages, the list stays inside the width of the browser window: each column keeps a predictable share of the row, long explanations wrap onto a small fixed number of lines instead of stretching their column, and the operator can scan the whole list top to bottom without scrolling sideways.

**Why this priority**: This is the reported defect. Today a single verbose message widens the table, pushes the download link off-screen, and makes the page unusable for the batch it was built for. Fixing the layout alone restores day-to-day usability and is a viable stand-alone improvement.

**Independent Test**: Submit a batch containing at least one document that fails with a long reason, one that finishes with a page-gap caution, and one that is still waiting. Confirm the page needs no horizontal scrolling, that every column keeps the same width across all rows, and that the download link of every finished document is visible without scrolling sideways.

**Acceptance Scenarios**:

1. **Given** a documents list containing the longest failure explanation the system can produce, **When** the list is displayed at a normal desktop window width, **Then** the page requires no horizontal scrolling and no column has been widened by that message.
2. **Given** an explanation longer than the preview allows, **When** the row is displayed, **Then** the message wraps over several lines, is cut off at a fixed line count, and the row makes it visibly clear that more text exists.
3. **Given** a list of documents that are converting, **When** their statuses change as work progresses, **Then** column widths and row order stay as they were and only the cell contents change.
4. **Given** a document whose name is very long, **When** the row is displayed, **Then** the name wraps or is shortened within its own column instead of widening the table.
5. **Given** a browser window narrower than a typical laptop screen, **When** the list is displayed, **Then** the list remains readable and usable without horizontal scrolling.

---

### User Story 2 - Delete a conversion and its output, after confirming (Priority: P2)

An operator notices a conversion they no longer want in the knowledge base: a document uploaded by mistake, a duplicate, or a conversion whose Markdown came out unusable. They choose to delete it from the documents list. The page asks them to confirm, naming the document and stating exactly what will be removed and that this cannot be undone. On confirmation the document is undone entirely — its Markdown leaves the output folder, its entry leaves the list, the retained copy of the upload is discarded, and the record that would make a re-upload count as already converted goes with it, so submitting the same PDF again converts it afresh. On cancellation nothing at all changes.

**Why this priority**: Without this, the only way to remove a bad conversion is to open the output folder on the host and delete files by hand, which risks removing the wrong file and leaves the service's record disagreeing with the folder. It is a clear, self-contained gain, but the list has to be usable first.

**Independent Test**: Convert a document, delete it from the list, and confirm the dialog names that document; then confirm that its Markdown is gone from the output folder, that the entry is gone from the list, that no other document's output was touched, and that uploading the same PDF again starts a real conversion rather than reporting it as already converted. Repeat and cancel at the dialog to confirm nothing was removed.

**Acceptance Scenarios**:

1. **Given** a finished conversion in the list, **When** the operator chooses to delete it, **Then** the system asks for explicit confirmation, names the document, and states what will be removed.
2. **Given** that confirmation dialog, **When** the operator cancels or dismisses it, **Then** nothing is deleted and the list is unchanged.
3. **Given** that confirmation dialog, **When** the operator confirms, **Then** every Markdown file that conversion produced is removed from the output folder, the retained copy of the upload is discarded, the entry leaves the list, and the operator is told the deletion succeeded.
4. **Given** a document that was split into sections and produced several Markdown files, **When** its conversion is deleted, **Then** all of its section files are removed, not only the first.
5. **Given** a document whose conversion has been deleted, **When** the same PDF is uploaded again, **Then** it is converted afresh and is not reported as already converted.
6. **Given** a document that was converted twice and so has two entries sharing one set of Markdown files, **When** either entry is deleted, **Then** the confirmation says both entries will go, and after confirming neither entry remains and no download points at a removed file.
7. **Given** a conversion that is still queued or converting, **When** the operator looks for the delete action, **Then** the action is visible but unavailable, and the reason is stated.
8. **Given** a conversion whose Markdown was already removed from the output folder by other means, **When** it is deleted, **Then** the deletion still completes and removes the remaining record rather than reporting an error.
9. **Given** the output folder cannot be written to, **When** a deletion is confirmed, **Then** the operator is told the output could not be removed and which part of the deletion did not happen, and the list still reflects the true state.

---

### User Story 3 - Open the full detail of a single conversion (Priority: P3)

Having seen a truncated explanation in the list, an operator opens the full detail of that one conversion. They see the complete message and everything the service recorded about the conversion — how long it took, the document's size and page count, which attempt this was, which Markdown files it produced, and the engine's own errors where there were any. They close it and are back in the list exactly where they were.

**Why this priority**: It completes the layout change by giving the truncated text somewhere to live, and it is the natural home for detail the list has never shown. Valuable, but the list is legible without it as long as the preview leads with what matters.

**Independent Test**: For a conversion with a long failure explanation, open its detail from the list, confirm the complete text is shown along with timings, size, page count, attempt, and produced files, then close it and confirm the list is unchanged and the position is kept.

**Acceptance Scenarios**:

1. **Given** a row whose explanation is truncated, **When** the operator opens its detail, **Then** the complete explanation is shown with no truncation.
2. **Given** any conversion in the list, **When** its detail is opened, **Then** it shows the recorded facts about that conversion, including the files it produced where there are any.
3. **Given** an open detail view, **When** the operator dismisses it by keyboard or by pointer, **Then** it closes, the list is unchanged, and keyboard focus returns to the row it was opened from.
4. **Given** an open detail view for a conversion that is still converting, **When** the conversion progresses, **Then** the detail view reflects the new state without being reopened.

---

### Edge Cases

- **Two conversions of the same document.** The same PDF converted twice shares one set of Markdown files, and the second conversion is recorded as already converted. Deleting either one removes the document itself, so both entries go together and the confirmation has to say so before the operator commits.
- **A document with a conversion still in flight and an earlier finished one.** The document cannot be deleted while any of its conversions is unfinished, because the running one would write its output back into a folder the operator believes they emptied.
- **Delete while the download is in flight.** A deletion confirmed while someone else is downloading that Markdown must not corrupt the download, and a download attempted after deletion must report that the output is gone rather than failing silently.
- **The same conversion deleted from two browser tabs.** The second deletion must report success or a plain "already removed", never an unexplained error.
- **A conversion that produced no output at all** (failed, timed out, refused). Deleting it removes the record; there is nothing in the output folder to remove.
- **A conversion whose history entry was already pruned.** History is pruned on a retention schedule while the output folder is the durable record, so a Markdown file can outlive its list entry and become undeletable from this page. The feature must not present a deletion promise it cannot keep for such files.
- **Files in the output folder this service never wrote.** Deletion must only ever remove files the service recorded as the output of the conversion being deleted.
- **A message with no natural break** — a very long file name or an engine error with no spaces — must be broken or shortened rather than forcing the column wider.
- **An empty list, and a list of several hundred conversions.** Both must render within the fixed layout and stay responsive.

## Requirements *(mandatory)*

### Functional Requirements

Requirement identifiers below are scoped to this specification.

#### Layout of the documents list

- **FR-001**: The documents list MUST occupy a fixed layout whose total width is bounded by the page width. Content MUST NOT be able to widen a column, and the page MUST NOT scroll horizontally at any supported window width.
- **FR-002**: Explanatory text in a row MUST wrap over multiple lines within its own column and MUST be limited to a fixed, small number of lines. Where the text has been cut off, the row MUST make that visible rather than ending mid-sentence with no indication.
- **FR-003**: A truncated message MUST lead with the information the operator needs to decide what to do, so that the visible portion alone is actionable.
- **FR-004**: Column widths and row order MUST remain stable while the list refreshes: a status changing from waiting to converting to converted MUST NOT reflow the table or move other rows.
- **FR-005**: The status of a conversion MUST remain distinguishable without relying on colour alone, and MUST stay legible when its column is narrow.
- **FR-006**: The action available for each row — downloading the Markdown, or deleting the conversion — MUST remain reachable without horizontal scrolling in every row state.
- **FR-007**: A document name too long for its column MUST be wrapped or shortened within that column, and its full value MUST remain obtainable by the operator.

#### Detail on demand

- **FR-008**: Operators MUST be able to open a detailed view of any single conversion from its row, without leaving the page and without losing their position in the list.
- **FR-009**: The detailed view MUST show the complete, untruncated explanation for that conversion, together with the facts the service records about it: its status, the time it took, the document's size and page count, the attempt number, the Markdown files it produced, and any engine-reported errors.
- **FR-010**: The detailed view MUST be dismissible by both keyboard and pointer, and dismissing it MUST return keyboard focus to the row it was opened from.
- **FR-011**: While a conversion is still in progress, its open detailed view MUST keep pace with its status rather than showing a stale snapshot.
- **FR-012**: Where a row's text has been truncated, the way to reach the full text MUST be discoverable from the row itself.

#### Deleting a conversion

- **FR-013**: Operators MUST be able to delete a conversion from the documents list.
- **FR-014**: The system MUST require an explicit confirmation before any deletion. The confirmation MUST name the document, state exactly what will be removed — the Markdown files, the history entry or entries, and the retained upload — and state that the removal cannot be undone.
- **FR-015**: The confirmation MUST default to the safe outcome: dismissing it, cancelling it, or navigating away MUST leave everything untouched.
- **FR-016**: On confirmation, the system MUST undo the conversion completely. It MUST remove every Markdown file the conversion produced from the output folder, the conversion's entry from the documents list, the retained copy of the uploaded PDF, and the record of the source document that causes a later upload of the same file to be reported as already converted. Nothing that identifies the document to the service may survive the deletion.
- **FR-017**: A deletion MUST remove every Markdown file the deleted conversion produced, including every section file of a document that was split, and MUST remove nothing else from the output folder.
- **FR-018**: The system MUST report the outcome of a deletion. If some part of it could not be completed, the report MUST say which part succeeded and which did not, and the list MUST show the true resulting state. Where files survived, the report MUST also say that the output-folder count no longer matches the folder, because that count is derived from the service's records and those were removed.
- **FR-019**: Where deletion is refused, the system MUST show that the action exists and state why it is unavailable. Omitting the control with no explanation is not a refusal the operator can act on — it is indistinguishable from the feature being missing. The rule itself is FR-022.
- **FR-020**: Deleting a conversion whose output is already absent MUST be treated as success, not as an error.
- **FR-021**: Where several conversions belong to the same source document and therefore share its Markdown output, deleting any one of them MUST remove all of them together, and the confirmation MUST state how many entries will disappear before the operator commits. No entry may be left offering a download of a removed file.
- **FR-022**: The system MUST refuse to delete a document while any conversion of it is still unfinished, including conversions the operator is not currently looking at, so that work in flight cannot write output back after the deletion.
- **FR-023**: After a document has been deleted, uploading the same PDF again MUST start a fresh conversion and MUST NOT report it as already converted.
- **FR-024**: The system MUST record every deletion in its operational log, naming the document and the files removed, so the output folder's contents can be reconciled afterwards.
- **FR-025**: The count of documents in the output folder shown on the page MUST reflect a completed deletion without requiring a page reload.
- **FR-026**: Deletion MUST operate on one conversion at a time, initiated from that conversion's own row. Multi-selection, whole-batch deletion, and any "delete everything finished" action are out of scope, so that every confirmation names exactly one document.

### Key Entities

- **Conversion**: One attempt at converting one submitted document — the row in the documents list. Attributes relevant here: its status, its explanation, its timings, its attempt number, the document it came from, and the Markdown files it produced.
- **Source document**: The uploaded PDF, identified by its content, shared by every conversion of that same file. Attributes: name, size, page count, and whether the uploaded copy is still retained.
- **Markdown output**: A file this service wrote into the output folder — one per document, or one per section for a document above the size threshold. Attributes: file name, size, the conversion that produced it, and the source document it belongs to.
- **Deletion request**: An operator's confirmed instruction to remove one conversion, and with it the source document that conversion belongs to. Attributes: the conversion named, the entries and files removed, anything that could not be removed, and when it happened.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: At every window width from a narrow laptop to a wide desktop, the documents list requires zero horizontal scrolling in every conversion state, including the longest message the system can produce.
- **SC-002**: Column widths measured before and after a batch of 20 documents runs to completion are identical, and no row changes position.
- **SC-003**: An operator can locate a named document and read its status in a list of 50 conversions in under 5 seconds, without scrolling sideways.
- **SC-004**: The complete explanation and all recorded facts for any conversion are reachable in one action from its row, and the detail closes in one action.
- **SC-005**: Deleting one conversion takes exactly two deliberate actions — choose delete, then confirm — and completes in under 2 seconds for a document with up to 50 section files.
- **SC-006**: After a confirmed deletion that completed cleanly, none of the files that conversion produced remain in the output folder, no trace of the document remains in the service's records, and the output-folder count shown on the page agrees with the folder within one refresh. Where a file could not be removed, the count is known to under-report the folder, and the deletion report says so.
- **SC-007**: Across a review of every deletion path, 100% of deletions are preceded by a confirmation that names the document, and no cancelled or dismissed confirmation removes anything.
- **SC-008**: No deletion removes any file belonging to a different document or any file this service did not write — verified including the case of the same document converted twice, where both entries are expected to go together.
- **SC-009**: Re-uploading a deleted document produces a real conversion on the first attempt, 100% of the time.
- **SC-010**: Operators stop removing files from the output folder by hand to clean up bad conversions.

## Assumptions

- The page keeps its current trust model: it is reachable only from the local network and has no accounts, so anyone who can open it may delete a conversion. No per-user permission or audit identity is introduced by this feature.
- Deletion applies to documents whose conversions have all finished. Cancelling work in progress is a different capability and is out of scope here.
- Deleting a conversion is understood as deleting the document: because conversions of one PDF share its output and its already-converted record, a deletion that left siblings behind would leave the list pointing at files that no longer exist.
- No undo and no recycle bin: deletion is immediate and permanent, which is why confirmation is required.
- A short preview of two to three lines is enough for an operator to decide whether to open the detail; the exact number is a design decision.
- Desktop browsers on the local network are the primary target. The list must degrade gracefully at narrow widths but a dedicated mobile layout is out of scope.
- Existing job-history pruning and uploaded-file retention continue to run unchanged; this feature adds a deliberate deletion path alongside them and does not alter their schedules. A deletion simply discards the retained upload ahead of the retention clock.
- Markdown files whose history entry has already been pruned remain outside what this page can delete; managing those stays a filesystem task.
- Requirement identifiers in this specification are numbered independently of the identifiers in the original stack specification.
