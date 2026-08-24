# Feature Specification: Images as files, not as Markdown

**Feature Branch**: `003-extract-images`

**Created**: 2026-08-23

**Status**: Draft

**Input**: User description: "Bilder sollen nicht ins Markdown übernommen werden. Stattdessen, sollen Bilder extrahiert werden. Im Markdown ist ein Verweis auf das extrahierte Bild anzugeben."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Markdown that is text, and only text (Priority: P1)

An operator converts a document containing figures, diagrams, logos, and scanned stamps. The
Markdown that lands in the output folder contains the document's text and, where a picture
stood, a reference to that picture — not the picture itself. The file is readable, editable,
and searchable in any text editor, and its size reflects the words in the document rather
than its illustrations.

**Why this priority**: This is the whole request, and it is the half that has to work for
anything else to matter. A picture carried inside the Markdown makes the file unreadable to
a human, inflates it beyond what its text justifies, and is passed to the knowledge base as
a wall of characters that means nothing there. Reference-only Markdown is a viable delivery
on its own, even if nobody ever opens the extracted pictures.

**Independent Test**: Convert a document with at least one embedded figure and confirm the
Markdown contains a reference where the figure was, contains no picture data, and is no
larger than its text warrants.

**Acceptance Scenarios**:

1. **Given** a document containing a figure, **When** it is converted, **Then** the Markdown
   contains a reference to that figure at the position the figure occupied in the document,
   and contains no encoded picture data anywhere.
2. **Given** a document containing no pictures at all, **When** it is converted, **Then** the
   Markdown is exactly what it would have been before this feature existed, and no image
   files are produced.
3. **Given** a document whose Markdown was previously produced with pictures inside it,
   **When** it is converted again, **Then** the replacement Markdown carries references
   instead, and no version containing picture data survives in the output folder.

---

### User Story 2 - The pictures themselves, alongside the document (Priority: P2)

Having read a reference in the Markdown, the operator can open the picture it names. The
pictures arrive in the output folder with the Markdown, named so that it is obvious which
document they belong to and which reference points at them, and they can be opened with an
ordinary image viewer.

**Why this priority**: A reference to a file that does not exist is worse than no reference.
This story is what makes the reference meaningful — but it is separable: the Markdown is
already improved by carrying references, and the extraction can be delivered and verified
on its own.

**Independent Test**: Convert a document with several figures and confirm that following
every reference in the Markdown reaches a picture file that opens and shows the figure that
stood at that position.

**Acceptance Scenarios**:

1. **Given** a converted document with figures, **When** the operator follows a reference
   from the Markdown, **Then** it resolves to an image file in the output folder that opens
   in a standard viewer and shows the expected figure.
2. **Given** two different documents that each contain figures, **When** both are converted,
   **Then** neither document's pictures can be confused with or overwrite the other's.
3. **Given** a converted document, **When** the operator retrieves it from the page as a
   single archive, **Then** the archive contains the Markdown **and** its pictures, with the
   references inside the archive still resolving.

---

### User Story 3 - Removing a document removes its pictures (Priority: P3)

An operator deletes a converted document, or converts it again after an engine upgrade. The
pictures follow the same rules as the Markdown: they go when the document goes, and they are
replaced when the document is replaced. The output folder never accumulates pictures whose
document no longer exists.

**Why this priority**: Without it the feature leaks. Every conversion of an illustrated
document leaves files behind for ever, and the operator is back to cleaning the output
folder by hand — the exact habit feature 002 was written to end. It is P3 only because the
leak is slow and invisible for a while.

**Independent Test**: Convert an illustrated document, delete it, and confirm the output
folder contains none of its files — Markdown or pictures — and that nothing belonging to any
other document was touched.

**Acceptance Scenarios**:

1. **Given** a converted document with pictures, **When** the operator deletes it, **Then**
   every picture it produced is removed along with its Markdown, and the confirmation says
   so before the operator commits.
2. **Given** a document converted twice, the second time producing different pictures,
   **When** the second conversion finishes, **Then** the pictures from the first do not
   survive alongside the second's.
3. **Given** a deletion that cannot remove some file, **When** it reports its outcome,
   **Then** pictures it could not remove are named in the report exactly as unremovable
   Markdown files already are.

---

### Edge Cases

- **A document that is entirely pictures.** A scanned document has a full-page image on
  every page. Resolved by FR-004: page-sized images are not extracted, so such a document
  produces no image files and its pages are represented by their recognised text.
- **A picture that is the page's only content but carries all its meaning** — a scanned
  table, a signed page. Not extracted under FR-004, and correctly so: its text is recovered
  by recognition. The judgement to watch is what counts as *page-sized* — a figure filling
  most of a page is still a figure.
- **The same picture repeated on every page** — a letterhead or watermark. Extracting it
  hundreds of times produces hundreds of identical files.
- **A picture too small to be worth a file** — a bullet glyph, a rule, a one-pixel spacer.
- **A document converted in parts** (FR-034): the parts are converted separately and joined,
  so references and file names must remain unique and correct across the join rather than
  restarting at one for each part.
- **A document written as section files** (FR-033): a reference must still resolve from the
  section file that contains it, not only from a whole-document file.
- **The output folder is not writable, or fills up mid-conversion.** Pictures multiply the
  number of files written per document, so partial writes become likelier.
- **An engine that returns a picture the system cannot store** — an unsupported format, or
  a name that is not safe as a file name.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Converted Markdown MUST NOT contain picture data. Where the source document had
  a picture, the Markdown MUST carry a reference to a separately stored image file instead.
  This is the difference between a Markdown file a person can read and one that is mostly
  encoded bytes — and between a knowledge base ingesting a document's words and ingesting
  its illustrations as text.
- **FR-002**: Every picture the converter identifies in a document MUST be written to the
  output location as an image file that opens in a standard image viewer, subject to FR-004.
- **FR-003**: Each reference in the Markdown MUST resolve to the image file for the picture
  that stood at that position, from the Markdown file's own location. A reference that does
  not resolve is a defect, not a degraded result.
- **FR-004**: Only pictures that occupy **part** of a page are extracted. A page-sized image
  — a scanned page — MUST NOT produce an image file: its content is already in the Markdown
  as recognised text (FR-039), so extracting it would add a file per page that duplicates
  text the operator already has, and would turn a two-thousand-page scan into an output
  folder nobody can work with.
- **FR-005**: The system MUST NOT extract a picture below a configured size, and MUST bound
  how many pictures one document can produce. The floor keeps rules, bullet glyphs, and
  spacers out of the output folder; the ceiling is a safety net for a document that defeats
  the part-page rule.
- **FR-006** *(revised 2026-08-24)*: The Markdown MUST carry a reference for an extracted
  picture and **nothing at all** for one that is not extracted, whatever the reason. What
  was skipped, and why, belongs on the document — in its record and in the log — not in its
  text.

  The original rule left a note where a picture had been skipped as too small or past the
  ceiling, so the operator would not lose the information silently. That was written for the
  occasional skipped picture. A real document skipped **three and a half thousand**, and the
  file bound for the knowledge base filled with markers for pictures nobody could see —
  worse than the problem the note existed to prevent. A note that appears once is
  information; the same note three thousand times is noise, and noise in the Markdown is
  what this feature exists to remove.
- **FR-007**: Image file names MUST be derived deterministically from the document's identity
  and the picture's position within it, so that converting the same document again produces
  the same names and overwrites in place rather than accumulating a second set. Two different
  documents MUST NOT be able to produce the same image file name.
- **FR-008**: The images of a document MUST be treated as part of that document's output
  everywhere the system already treats its Markdown that way: they are removed when the
  document is deleted, replaced when it is converted again, named in a deletion confirmation
  before the operator commits, reported when they cannot be removed, and counted in what the
  document produced.
- **FR-009**: Retrieving a document from the page MUST deliver its pictures with its
  Markdown, and the references MUST still resolve within what was delivered.
- **FR-010**: The operator MUST be able to turn extraction off, returning the system to
  producing Markdown with no pictures and no image files. A corpus that is entirely text
  should not pay for a feature it never triggers.
- **FR-011**: Extraction MUST NOT reach the internet, MUST NOT require anything the
  deployment does not already carry, and MUST NOT weaken the isolation properties of the
  running stack (FR-021, FR-022 of feature 001).
- **FR-012**: A failure to store a picture MUST NOT fail the document. The Markdown and the
  pictures that were stored MUST still be written, and the document MUST report that some
  pictures are missing in the same way it already reports missing pages — visibly, with a
  reason, and marked in the Markdown itself rather than only in a history that is pruned.
- **FR-013**: The system MUST record how many pictures a document produced, so an operator
- **FR-014**: A picture lying entirely within the page's header or footer band MUST NOT be extracted, and MUST leave nothing in the Markdown. It is the page's furniture — a party logo, a mark — repeated on every page, and nothing a reader of the text loses. *Added 2026-08-24, from the operator: "why not ignore images in headers and footers at all — an image there is of no need for the LLM to know."* Position is what separates furniture from content; **size never did**, because the same logo appears at many sizes and any threshold high enough to exclude it excluded real figures too. The bands MUST be configurable, and MUST be read correctly whichever way up the source's coordinates run.
  can tell an illustrated document that extracted nothing from a document that had no
  pictures to extract.

### Key Entities

- **Extracted image**: One picture taken from a source document and stored as a file.
  Attributes: its file name, the document it belongs to, its position within that document,
  its size, and the reference by which the Markdown reaches it. Belongs to exactly one
  source document and shares that document's lifetime — created with its conversion,
  replaced by a re-conversion, removed by its deletion.
- **Markdown output** (existing): gains the fact that it may reference extracted images, and
  that its completeness now depends on those images being present.
- **Source document** (existing): gains a count of the pictures its conversion produced.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For an illustrated document, the Markdown contains zero bytes of encoded
  picture data, and its size is within 10% of the size of the same document's text.
- **SC-002**: 100% of references in a converted document's Markdown resolve to an image file
  that opens — measured across the whole output folder, not a sample.
- **SC-003**: An operator reading the Markdown can tell, at every point where the source had
  a picture, that a picture was there and where to find it.
- **SC-004**: Converting the same document twice produces the same set of image files, with
  no duplicates and no orphans left from the first conversion.
- **SC-005**: Deleting an illustrated document leaves none of its files in the output folder,
  and removes nothing belonging to any other document.
- **SC-006**: A document with no pictures produces exactly the output it produced before this
  feature existed — same Markdown, same file count, no image files.
- **SC-007**: A document of scanned pages produces no image files at all, and its Markdown
  is the same recognised text it produces today.
- **SC-008**: An illustrated document converts in no more than 25% more time than the same
  document converted with extraction turned off.
- **SC-009**: Every document the operator imports is ingested by the knowledge base without
  the failures that embedded pictures currently cause.
- **SC-010**: Operators stop having to open the source PDF alongside the Markdown to see what
  a figure showed.

## Assumptions

- **The converter can already do this.** The engine offers a choice of how pictures are
  carried in its Markdown — inside the file, as a placeholder, or as a reference to a stored
  file — and the deployment has been taking the default, which embeds them. The feature is
  therefore expected to be mostly a matter of asking for a different mode and handling the
  files that come back, not of building picture extraction. **This is an assumption about a
  third-party component and must be confirmed in planning**, in particular how extracted
  images are returned by the conversion interface currently in use.
- **The output folder is the right home for the images**, alongside the Markdown that
  references them, because it is the folder the operator already opens and imports from.
  Whether they sit beside the Markdown or in a folder of their own is a planning decision;
  either satisfies FR-003.
- **The knowledge base cannot cope with Markdown that contains pictures** — stated by the
  operator, 2026-08-23, and the reason this feature exists. It is a stronger constraint than
  "the files are large": embedded picture data does not merely bloat the Markdown, it makes
  the document unusable to AnythingLLM. FR-001 is therefore about the knowledge base working
  at all, not about tidiness, and it holds for every picture — including the ones FR-004
  declines to extract, which leave no data behind either.
- **The images are for people, not for the knowledge base.** Nobody expects them to be
  searchable or imported. They exist so a person following a citation can see what the
  document showed.
- **Text recognition is unaffected.** Recognition of scanned pages continues exactly as it
  does today (FR-039); this feature is about what happens to the picture after its text has
  been read, not about whether it is read.
- **Existing documents are not migrated.** Markdown already written with pictures inside it
  stays as it is until the document is converted again. The operator can force that with the
  existing re-conversion action.
- **Extraction is on by default**, because the request is to change the current behaviour,
  not to make the change available.

## Dependencies

- Feature 001's output naming (FR-014), section files (FR-033), splitting and joining
  (FR-034), and archive retrieval (FR-043) all extend to the new files rather than treating
  them as a separate concern.
- Feature 002's deletion rules (FR-016, FR-017, FR-018) must cover images, including the
  guarantee that a deletion removes nothing this service did not write.
- The engine's behaviour in reference mode, which is unverified — see Assumptions.

## Out of Scope

- Describing, captioning, or classifying pictures, whether by a vision model or otherwise.
  The stack deliberately does not generate content it cannot extract (feature 001,
  Assumptions).
- Making the images searchable, or importing them into AnythingLLM.
- Re-writing Markdown that has already been produced with pictures inside it.
- Extracting anything other than pictures — tables and formulas stay as Markdown.
- Extracting page-sized images, per FR-004. A scanned page is text once it has been read.
