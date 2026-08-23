# Feature Specification: Offline Docling PDF-to-Markdown Stack

**Feature Branch**: `001-docling-pdf2md-stack`

**Created**: 2026-08-18

**Status**: Draft

**Input**: User description: "Let's create docker implementation open source tool Docling for converting complex pdf documents into markdown files to be fed into AnythingLLM. The docker container(s) shall run stack in portainer on macmini in same network. stack shall no access to internet. Accessible only from same local network."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Convert a complex PDF into ingestion-ready Markdown (Priority: P1)

A knowledge worker on the local network has a complex PDF (multi-column layout, tables, figures, headers/footers, scanned pages) that must become searchable knowledge inside AnythingLLM. They open the stack's page in a browser, upload the PDF, watch it convert, and retrieve a Markdown file whose reading order, headings, and tables faithfully reflect the original document.

**Why this priority**: This is the entire reason the stack exists. Without accurate conversion of complex PDFs, nothing else in the feature has value. A single working conversion path is a viable MVP.

**Independent Test**: Upload a representative complex PDF (containing at least one multi-column page, one table, and one scanned page) to the running stack and confirm a Markdown file is produced with correct reading order, a Markdown table for the tabular content, and text recovered from the scanned page.

**Acceptance Scenarios**:

1. **Given** the stack is running and a complex text-based PDF is uploaded, **When** conversion completes, **Then** a Markdown file is produced containing the document's headings as Markdown headings, body text in correct reading order, and tables rendered as Markdown tables.
2. **Given** a PDF whose pages are scanned images, **When** conversion completes, **Then** the resulting Markdown contains the recognized text of those pages rather than an empty or image-only result.
3. **Given** a PDF containing embedded images or figures, **When** conversion completes, **Then** the Markdown references or describes each figure in its correct position in the document flow, and no figure silently removes surrounding text.
4. **Given** a password-protected or corrupt PDF is uploaded, **When** conversion is attempted, **Then** the job is reported as failed on the page with a human-readable reason and no partial Markdown file is presented as a successful result.
5. **Given** a PDF longer than the configured part size, **When** it is uploaded, **Then** it is divided into parts and converted without the user asking for that or knowing it happened, and the page shows which part is currently converting.
6. **Given** one part of such a document fails, **When** the job ends, **Then** the sections drawn from the successful parts are still written, the document is reported as incomplete rather than as a success, and the missing page range is named both on the page and inside the Markdown.
7. **Given** a PDF above the absolute page ceiling, **When** it is uploaded, **Then** it is refused immediately with a reason saying it is too long and what to do about it, not with a suggestion that the file is damaged.
8. **Given** a converted document whose Markdown exceeds the size threshold, **When** the job finishes, **Then** the output location receives one file per detected section rather than one very large file, and re-converting the same document overwrites those files in place.

---

### User Story 2 - Operate the stack from Portainer on the Mac mini (Priority: P1)

An operator deploys the conversion stack on the Mac mini by pointing Portainer at the project's GitHub repository, which holds the single stack definition. They can start, stop, redeploy, and inspect logs of the stack entirely from Portainer, and after a Mac mini reboot the stack comes back on its own.

**Why this priority**: The stack has no value if it cannot be deployed and kept alive on the target host by its intended operator. Deployment is a hard requirement stated by the user and is testable independently of conversion quality.

**Independent Test**: Deploy the stack in Portainer on the Mac mini from the GitHub repository, without editing files on the host by hand and without supplying any credential, confirm all services reach a healthy state, restart the Mac mini, and confirm the stack returns to a healthy state unattended.

**Acceptance Scenarios**:

1. **Given** Portainer is running on the Mac mini and one-time provisioning is complete, **When** the operator points Portainer at the GitHub repository and deploys, **Then** all services start and report a healthy status within 10 minutes on first deploy (excluding image download time) and 5 minutes on redeploy, with no further host-side steps.
2. **Given** the stack is running, **When** the operator stops and redeploys it from Portainer, **Then** previously converted Markdown files and job history remain intact.
3. **Given** the Mac mini is rebooted, **When** the host finishes starting, **Then** the stack returns to a healthy state without operator intervention.
4. **Given** a conversion service fails or crashes, **When** the operator opens the stack logs in Portainer, **Then** the logs identify which document failed and why, in plain text, without requiring access to the host shell.

---

### User Story 3 - Guarantee the stack is offline and LAN-only (Priority: P1)

A security-conscious owner must be able to demonstrate that the stack's running services never reach the internet and cannot be reached from outside the local network, including at first start on a freshly provisioned Mac mini. Pulling the deployment from GitHub is the host's business and happens before the stack runs; once running, the stack is sealed.

**Why this priority**: "No internet access" and "local network only" are explicit, non-negotiable constraints from the user. They bind the running stack rather than the act of deploying it, which is performed over the internet from GitHub. They still constrain the design of every other story, because anything the stack needs at runtime must already be inside it.

**Independent Test**: Deploy the stack from scratch, then confirm from inside each running container that no internet address is reachable, and run a full conversion end to end; separately, attempt to reach the stack's interfaces from a host outside the local network and confirm the attempt fails.

**Acceptance Scenarios**:

1. **Given** the stack has been deployed and the host's internet path is then removed, **When** a document is converted, **Then** conversion succeeds with no failures attributable to missing downloads.
2. **Given** the stack is running normally, **When** the outbound network activity of its containers is observed over a full conversion cycle, **Then** no connection attempts leave the local network.
3. **Given** a client on the same local network, **When** it opens the stack's interface, **Then** access is granted; **Given** a client outside the local network, **When** it attempts the same, **Then** access is refused.
4. **Given** all recognition and layout models required for conversion, **When** the stack starts for the first time on a machine that has never run it, **Then** those models are already present inside the deployment and no runtime download is attempted.

---

### User Story 4 - Collect converted Markdown for import into AnythingLLM (Priority: P2)

Converted Markdown accumulates in one dedicated output location on the Mac mini, and is also downloadable from the browser page. An operator periodically takes what is there and imports it into AnythingLLM by hand, confident that every file is complete, uniquely named, and traceable to its source PDF.

**Why this priority**: This completes the intended workflow into AnythingLLM. It ranks below conversion itself because conversion (P1) already delivers standalone value, and the import step is deliberately manual.

**Independent Test**: Convert a small set of PDFs, confirm each finished file appears in the output location with a stable unique name and can also be downloaded from the page, then import that batch into AnythingLLM and confirm it answers questions citing those documents.

**Acceptance Scenarios**:

1. **Given** a PDF has been converted successfully, **When** the job finishes, **Then** the Markdown file appears in the dedicated output location with a name that is stable, unique, and traceable back to the source PDF.
2. **Given** a job shows as completed on the page, **When** the user chooses to retrieve it, **Then** the converted Markdown is downloaded to their machine over the local network.
3. **Given** a source PDF is converted a second time, **When** the job finishes, **Then** the output location does not accumulate ambiguous duplicates that would cause the same content to be imported twice under different identities.
4. **Given** a conversion fails, **When** the job ends, **Then** no file for that document is placed in the output location.
5. **Given** the operator imports the output location's contents into AnythingLLM, **When** they query AnythingLLM, **Then** answers cite the correct source documents.

---

### User Story 5 - Convert a batch of documents unattended (Priority: P3)

A user selects a whole set of PDFs on the upload page in one go and lets the stack work through them, checking back on the page later to see which succeeded and which failed.

**Why this priority**: A quality-of-life improvement over single-document conversion. Valuable at volume, but the stack is already useful without it.

**Independent Test**: Upload a batch containing a mix of valid and invalid PDFs, leave it unattended, and confirm every valid document is converted, every invalid one is reported as failed, and the batch does not stall.

**Acceptance Scenarios**:

1. **Given** multiple documents are submitted at once, **When** the stack processes them, **Then** each document is converted and reported individually, and one failing document does not prevent the others from completing.
2. **Given** a batch is in progress, **When** the user looks at the page, **Then** they can see which documents are queued, running, completed, or failed.
3. **Given** the stack is restarted mid-batch, **When** it comes back up, **Then** documents that had not completed are either resumed or clearly reported as incomplete on the page rather than silently lost.

---

### Edge Cases

- What happens when a PDF is very large (hundreds of pages) or a single conversion runs far longer than expected — is it allowed to run to completion, or is it cut off and reported as timed out?
- What happens when a table or a paragraph spans the boundary between two parts — the split is made on page numbers, without knowing where the document's structures begin and end.
- What happens to section files already in the outbox when the same document is re-converted after an engine upgrade that detects headings differently — the new files are written, but files for sections that no longer exist are left behind, and nothing prunes the outbox.
- What happens when two files with the same name are uploaded by different users?
- What happens when a user closes the browser page mid-batch, or opens it on a second machine — does the work continue and does the status still show?
- What happens when a user uploads a file far larger than the page expects, or the upload is interrupted partway?
- What happens when the storage location fills up mid-batch?
- What happens when a submitted file is not a PDF at all, or has a PDF extension but different content?
- What happens when a PDF contains no extractable text and no recognizable page images (e.g., blank scans)?
- What happens when a PDF's tables span multiple pages, or contain merged and nested cells that have no clean Markdown equivalent?
- What happens when the stack receives a new document while it is already saturated with concurrent work?
- What happens when a conversion produces Markdown that is empty or drastically shorter than the source suggests — is it surfaced as suspect rather than reported as success?
- What happens when the Mac mini loses power mid-conversion, leaving a partially written output file?
- What happens when a client on the local network requests a document that is still being converted?

## Clarifications

### Session 2026-08-19

- Q: When you say deployment happens via GitHub, which parts should come from GitHub — the stack definition, the container images, or both? → A: Both — Portainer pulls the stack definition from the GitHub repository, and the container images are pulled from a registry.
- Q: Once the stack is deployed, should the two running containers still be blocked from reaching the internet entirely? → A: Yes — both containers stay permanently egress-blocked; the host runtime and Portainer may reach GitHub and the registry at any time.
- Q: Must the conversion models still be baked inside the engine image, or may they be downloaded once during provisioning? → A: Baked in — the pinned engine image ships its own weights; the stack never needs a model source. The host's Ollama is not used: it cannot serve docling's layout, table, and recognition models, and replacing them with a generative vision model would trade extracted content for generated content.
- Q: Should the GitHub repository and the published web image be public, or private with credentials stored in Portainer? → A: Public repository, public image package — no credentials anywhere in the deployment.
- Q: Should Portainer redeploy automatically when the GitHub repository changes, or only when an operator triggers it? → A: Manual only — no polling and no webhook; the operator decides when the deployed version changes.
- Q: For a document large enough to require splitting, what should land in the outbox — one Markdown file, one per page-range part, or one per detected section? → A: One file per detected section, but only above a size threshold; smaller documents stay a single file. Retrieval sees chunks rather than files, so this is a citation decision, not a ranking one.
- Q: What should trigger the split — the engine's page ceiling, or a part size chosen to fit the per-document timeout? → A: Split any document longer than a configured part size, sized from measured throughput so a part fits well inside the per-document timeout.
- Q: If one part of a split document fails, should the whole document fail or should the successful sections still be written? → A: Write the successful sections, report the document incomplete, and mark the gap in the Markdown itself as well as on the page.
- Q: Should the page count be read at upload, and should there be an upper bound beyond which the system declines to split? → A: Yes to both — count at upload, split accordingly, and refuse above a configured absolute ceiling with a plain-language reason.
- Q: While a split document converts, should the page show one row for the document with part progress, or a row per part? → A: One row per document, showing which part is running — "Converting — part 7 of 20".

## Requirements *(mandatory)*

### Functional Requirements

**Conversion**

- **FR-001**: The system MUST accept PDF documents and produce a Markdown representation of each document's content.
- **FR-002**: The system MUST preserve document structure in the output, including heading hierarchy, paragraph reading order across multi-column layouts, lists, and tables rendered as Markdown tables.
- **FR-003**: The system MUST recover text from scanned or image-only pages so that such pages are represented as text in the Markdown output.
- **FR-004**: The system MUST represent figures and images in the output at their correct position in the document flow, without dropping surrounding text.
- **FR-005**: The system MUST produce output that is valid Markdown suitable for direct ingestion by AnythingLLM, with no post-processing required by the user.
- **FR-006**: The system MUST record, for every converted document, the source file identity and the time of conversion so an output file can be traced back to its source PDF.
- **FR-007**: The system MUST reject unsupported, corrupt, or unreadable inputs with a human-readable failure reason and MUST NOT emit an output file for them.
- **FR-036**: The system MUST determine a document's page count when it is uploaded, and MUST use it to decide immediately whether the document is converted whole, split into parts, or refused. A document above a configured absolute page ceiling MUST be refused at upload with a plain-language reason that says it is too long and what to do about it — never with a suggestion that the file is damaged. The ceiling exists to protect the shared queue: parts occupy queue slots, so without it one very long upload can hold every slot and stall every other document (FR-027, SC-008).
- **FR-029**: The system MUST detect a conversion whose Markdown is empty or implausibly small for the source document, and MUST report it distinctly from an ordinary success, so that no one imports an empty document into AnythingLLM believing it converted. The output MUST still be written and retrievable, and a document that is genuinely blank MUST be reportable as blank rather than as a system failure.

**Intake, status, and handoff**

- **FR-008**: Users MUST be able to submit documents through a page opened in a standard web browser from any machine on the local network, with no client software to install.
- **FR-009**: Users MUST be able to select and submit more than one document at a time from that page.
- **FR-010**: The page MUST show the current status of each submitted document (queued, in progress, completed, failed, timed out) and update as jobs progress, without the user reloading or re-submitting.
- **FR-037**: A split document MUST appear on the page as one entry, matching the one document the user submitted, and that entry MUST show which part is currently converting. A long document can take an hour or more, and an entry that shows no movement for that long is indistinguishable from a stalled one — the part counter is what makes legitimate slow progress legible (FR-010).
- **FR-011**: The page MUST show a human-readable reason for every failed document.
- **FR-012**: Users MUST be able to retrieve the converted Markdown for a completed document directly from that page.
- **FR-013**: The system MUST write every successfully converted Markdown file to a dedicated output location on the Mac mini, from which an operator imports documents into AnythingLLM manually. Automatic delivery into AnythingLLM is out of scope.
- **FR-014**: The system MUST name output files predictably and uniquely so that repeated conversions of the same source do not create ambiguous duplicates for the person importing them into AnythingLLM.
- **FR-033**: When a converted document's Markdown exceeds a configured size threshold, the system MUST write one file per detected top-level section instead of a single file, so that an answer drawn from a very long document cites the section it came from rather than the whole document. Documents below the threshold MUST continue to produce exactly one output file. Section file names MUST be derived deterministically from the source document's identity and its section, so re-converting the same document overwrites its files in place rather than accumulating duplicates (FR-014). Section sizes vary, so the system MUST bound them: sections too small to stand alone are merged, and a section larger than the threshold is itself divided.

**Deployment and operation**

- **FR-015**: The system MUST be deployable as a single container stack definition through Portainer on the Mac mini, with the definition pulled by Portainer directly from the project's GitHub repository. Host-side commands MUST be confined to one-time provisioning — preparing the declared storage locations. Every subsequent deploy, redeploy, stop, and start MUST be possible from Portainer alone, with no host shell access and no manual transfer of artifacts to the host.
- **FR-016**: The system MUST restart automatically after a host reboot or a service crash, returning to a working state without operator intervention.
- **FR-017**: The system MUST retain converted outputs and job history across stack stop, restart, and redeploy.
- **FR-018**: The system MUST expose health status for each service so Portainer shows an accurate healthy/unhealthy state.
- **FR-019**: The system MUST emit logs that identify each job, its source document, its outcome, and the reason for any failure, viewable through Portainer.
- **FR-020**: The system MUST document the storage locations it requires on the Mac mini, their purpose, and their expected growth, so the operator can plan capacity.
- **FR-030**: The GitHub repository MUST be the single source of the deployed stack definition, and all container images MUST be obtained from a container registry at deploy time. Deploying, redeploying, or upgrading the stack MUST NOT require copying images or files onto the Mac mini by hand.
- **FR-031**: Deployment MUST require no credentials: the repository and the published images MUST be publicly readable, so that no token, deploy key, or registry login is stored on the Mac mini or in Portainer, and no expiring credential can break an unattended redeploy. The repository MUST therefore contain no secret at any point in its history.
- **FR-032**: Redeployment MUST be initiated by an operator. The system MUST NOT poll the repository for changes or accept an inbound trigger to redeploy itself, so that the version performing conversions never changes without someone deciding it should. Image references in the stack definition MUST be pinned to exact versions rather than to a moving tag.

**Network isolation**

- **FR-021**: The stack's running services MUST have no outbound internet access at any point after deployment, including first start, and MUST NOT depend on any runtime download to convert a document. This restriction binds the containers, not the host: the host's container runtime and Portainer MAY reach GitHub and the container registry in order to deploy, redeploy, or upgrade the stack.
- **FR-022**: The system MUST include every model, dictionary, and asset required for conversion — including text recognition and layout analysis — inside the container images themselves, not in host state populated by a provisioning step. A freshly pulled image on a machine that has never run the stack MUST be able to convert without acquiring anything further.
- **FR-023**: The system MUST only be reachable from the local network, and MUST NOT publish any interface to the internet.
- **FR-024**: The system MUST treat presence on the local network as sufficient authorization: no user accounts, logins, or shared credentials are required to submit, monitor, or retrieve documents. Access restriction is enforced solely by network reachability per FR-023.
- **FR-025**: The system MUST render its browser page and serve its content using only assets it hosts itself, so the page works fully for a client that has no internet access.
- **FR-026**: The system MUST provide the operator with a documented, repeatable way to verify both isolation properties: that the stack's running services make no internet connections, and that the stack is unreachable from outside the local network. This verification MUST be part of every deploy that changes the stack's networking.

**Resource behavior**

- **FR-027**: The system MUST limit how much work it performs at once so that a large batch does not exhaust the Mac mini's memory or make the host unresponsive.
- **FR-028**: The system MUST fail a conversion that exceeds a documented maximum duration, report it as timed out, and continue processing the remaining documents.
- **FR-034**: The system MUST convert a document longer than a configured part size by dividing it into parts of at most that size and converting each part, rather than submitting it whole and failing. The part size MUST be derived from measured conversion throughput so that a part completes well inside the engine's per-document timeout, not merely below its page ceiling — the time limit binds first at any realistic throughput. Splitting MUST be invisible in the result: the parts exist to get the work done, and the output is assembled from them (FR-033). The system's own per-document watchdog MUST account for the number of parts: a split document's total time is necessarily longer than any single part's, so a watchdog sized for one conversion would terminate every document that needed splitting — which is precisely the failure splitting exists to prevent. Part boundaries fall on page boundaries chosen without knowledge of the document's structure, so content that spans one — a table continuing across pages, most obviously — may be divided between parts and reassembled imperfectly. The system MUST NOT silently degrade table fidelity below the level SC-002 requires; how boundaries are chosen or reconciled is a design decision for the plan.
- **FR-035**: When one part of a split document fails to convert, the system MUST still write the sections derived from the parts that succeeded, and MUST report the document as incomplete rather than as an ordinary success — the same distinction FR-029 draws for implausibly small output. The report MUST name the page range that is missing. The gap MUST also be marked in the Markdown itself, not only in the job history: job history is pruned after a documented retention period while the output location is the durable record, so a warning that lives only on the page disappears while the incomplete file remains in the knowledge base.
- **FR-038**: Before a page range is reported missing, the system MUST try it again. A range the engine reports as failed, or that outruns the per-part time limit, MUST be divided into smaller ranges covering the same pages and converted again, to a bounded depth and not below a floor size — each retry costs another full timeout of engine time, and below the floor the pages themselves, not their number, are the problem. A range whose task or result the engine loses MUST simply be converted again, to a bounded number of attempts, matching what FR-026 already does for a whole document: the pages are still on the server, so nobody needs to be asked to upload them again. Only when those attempts are spent is the range a gap (FR-035), and the record of the gap MUST then carry the reason the engine gave and how many attempts were made. Without this, a part size that is merely too optimistic for the corpus does not degrade the result — it removes it: every full-size part meets whatever the first one met, and the document is reported as converted while containing almost nothing. The reason a range was finally given up on MUST be visible to the operator, because the ranges alone are the same sentence whatever went wrong, and a system that cannot say which ceiling it hit invites the wrong setting to be changed.
- **FR-039**: The recognition language MUST be configurable, and the configured language MUST be satisfiable from assets already inside the engine image (FR-022) — a language the image cannot serve must fail visibly at conversion, never by silently recognising the wrong alphabet. Where the engine's automatic choice does not cover the corpus's language, the deployment MUST name the recognition engine rather than relying on that choice. Recognition applies to scanned pages; a page carrying its own text layer MUST keep it, so that configuring a language cannot degrade a born-digital document.
- **FR-040**: An output with pages missing from it MUST NOT satisfy a later request to convert the same document. Re-uploading the document, or asking to convert it again, MUST start a real conversion rather than reporting it as already converted (FR-014) and handing back the incomplete file. The system MUST offer that second attempt directly on the page: without it the only way to ask for a whole document is to delete this one, which discards the pages that did convert along with the entry and the upload (FR-017). A complete output continues to satisfy the request, unchanged — this exception is exactly as wide as the gap that caused it.
- **FR-041**: The system MUST report whether work is actually moving, not merely whether the engine can be reached. A conversion that cannot be submitted MUST surface the converter's own refusal, and a conversion loop that has stopped MUST be reported as such — both leave documents queued indefinitely while every component reports itself healthy, which is a stall presented to the operator as a converter standing ready. Health MUST be degraded in either case, because the operator's decision (wait, or intervene) depends entirely on the distinction.
- **FR-042**: No single document may be able to stop the service repeatedly. Resuming whatever was in flight after a restart is correct until the document is what caused the restart — it is then recovered, stops the service again, and is recovered again, a loop that takes every other document down with it and that the operator cannot escape from the page, because the page is down too. The system MUST count recoveries of a document, whatever state it was recovered from, and MUST give up on one that has been recovered too many times without finishing, saying so in terms the operator can act on. Routine work MUST also not hold more than it needs: the converted text of a document is read when it is assembled and not on every pass of the conversion loop.
- **FR-043**: Where a document produced more than one file (FR-033), retrieving it from the browser MUST retrieve **all** of them, as a single archive named after the document. Offering the first of several is worse than offering nothing: what arrives is a plausible, complete-looking Markdown file that is a fraction of the document, and nothing about it says so. The page MUST also state how many files a document produced wherever it offers them, and the recorded output size MUST be the document's total rather than the first file's.

### Key Entities

- **Source Document**: A PDF uploaded for conversion. Attributes: original file name, size, page count, time of upload.
- **Batch**: A set of documents submitted together in one upload. Attributes: submission time, member documents, aggregate progress.
- **Conversion Job**: One attempt to convert one source document. Attributes: status (queued, running, completed, failed, timed out), start and end time, failure reason, link to its source document and to its output.
- **Markdown Output**: The converted result of a successful job — one file for most documents, or one file per section for documents above the size threshold (FR-033). Attributes: file name, storage location, size, creation time, section title and ordinal where applicable, link back to the source document.
- **Stack Deployment**: The running set of services on the Mac mini. Attributes: service health, storage locations in use, network exposure scope.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a representative set of 20 complex PDFs (multi-column, tabular, and scanned), at least 90% convert successfully on the first attempt without operator intervention.
- **SC-002**: In a manual review of converted output, at least 95% of headings and at least 90% of tables present in the source documents are correctly represented in the Markdown.
- **SC-003**: A typical 20-page text-based document completes conversion in under 3 minutes on the Mac mini.
- **SC-004**: A newly provisioned Mac mini can go from an empty Portainer to a healthy, converting stack in under 30 minutes by pointing Portainer at the GitHub repository and following the provided documentation, excluding the time spent downloading the images over the operator's connection.
- **SC-005**: Over a full conversion cycle, zero outbound connections leave the local network from either of the stack's containers. Image and stack-definition retrieval performed by the host during deployment is excluded.
- **SC-006**: Access attempts from outside the local network fail 100% of the time, verified by a documented, repeatable test.
- **SC-007**: After a host reboot, the stack is healthy and accepting documents within 5 minutes with no operator action.
- **SC-008**: A batch of 50 documents runs unattended to completion with every document ending in a definite reported outcome (converted or failed with a reason) and none left in an indeterminate state.
- **SC-009**: A batch of converted documents taken from the output location and imported into AnythingLLM returns answers citing the correct source document in at least 9 of 10 spot-check questions.
- **SC-010**: A user on the local network who has never seen the stack before can open its page and get a first document converted in under 5 minutes without written instructions or credentials.
- **SC-011**: While a batch is running, the Mac mini remains responsive enough that other services on the host, including Portainer, stay usable.
- **SC-012**: A document at least ten times the configured part size converts to completion unattended, its output is retrievable from the output location, and no page range is missing without being reported as missing.
- **SC-013**: A split document meets the same fidelity thresholds as an unsplit one — at least 95% of headings and 90% of tables correctly represented (SC-002) — measured on content that spans a part boundary as well as content that does not, so that splitting itself costs no fidelity.
- **SC-014**: For a document large enough to be written as section files, an answer drawn from it in AnythingLLM cites the section containing the passage rather than only the document as a whole (extends SC-009).

## Assumptions

- Docling is the conversion engine, as named by the user; it is open source and can run fully offline because the published engine image ships its models. Engine image variants that omit the weights are unusable here, whatever their size advantage.
- The Mac mini is an Apple Silicon machine running a container runtime with Portainer already installed and in use for other stacks; conversion runs on CPU, since GPU acceleration is not available to Linux containers on macOS.
- An Ollama instance runs on the Mac mini outside containers and is not used by this feature. Docling's layout, table-structure, and text-recognition models are not language models and cannot be served by Ollama; the only thing Ollama could replace is the whole conversion pipeline, with a vision model whose output is generated rather than extracted. That is rejected here because a fabricated table entering AnythingLLM is indistinguishable from a correct one. Ollama-served figure captioning remains possible as a separate, opt-in feature; it is out of scope.
- AnythingLLM already exists and is operated separately. This feature delivers the conversion stack and the handoff into AnythingLLM; deploying, configuring, or modifying AnythingLLM itself is out of scope.
- The local network is trusted and privately administered. "Local network only" means reachable from that network's clients and not routable or port-forwarded from the internet. Anyone on that network can therefore upload documents and read every converted document; this is accepted deliberately.
- Deployment is performed over the internet from GitHub: Portainer reads the stack definition from the repository and the host's container runtime pulls the images from a registry. The internet restriction applies to the running stack, not to the act of deploying it.
- Scanned-page text recognition is limited to what the engine image already carries, since language packs cannot be fetched at runtime. In the pinned image that is any Latin-script language, German included, through the bundled EasyOCR `latin_g2` weights — but only when that engine is named explicitly, because the image's automatic choice recognises English and Chinese (research.md R4).
- Input is limited to PDF. Other formats Docling may support (Office documents, HTML, images) are out of scope for this feature.
- Automatic splitting requires the service to read a PDF's page structure and write out page ranges before any conversion happens — something it did not previously do, since until now a PDF was an opaque blob to be forwarded to the engine. This is a deliberate addition to what the service understands about its input, and it applies to every upload, because the page count is what decides whether a document is converted whole, split, or refused.
- The stack is sized for a small workgroup — on the order of tens of documents per day, a handful of concurrent users — not for high-volume or multi-tenant use.
- Source PDFs and converted Markdown may contain sensitive material; both remain on the Mac mini and are never transmitted off the local network.
- Importing documents into AnythingLLM is a deliberate manual step performed by an operator; the stack makes no connection to AnythingLLM and holds no credentials for it.
- The browser page is a functional operations page for submitting documents, watching status, and retrieving results. Branding, mobile layouts, and accessibility beyond ordinary browser defaults are out of scope.
- Job history is kept for operational visibility rather than as a long-term archive; the output location is the durable record.
- No authoring, editing, or review interface for the converted Markdown is in scope; output is consumed as produced.
- The project constitution at `.specify/memory/constitution.md` is an unfilled template, so no ratified project principles constrain this specification.
