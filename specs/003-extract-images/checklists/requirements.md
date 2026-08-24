# Specification Quality Checklist: Images as files, not as Markdown

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-23
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

**All items pass.** Validated after the FR-004 clarification was resolved.

**FR-004 resolved 2026-08-23 — Option A**: only pictures occupying part of a page are
extracted; a page-sized image is not, because its content is already in the Markdown as
recognised text. The operator's reason for the feature — *AnythingLLM cannot cope with
Markdown containing images* — was recorded in Assumptions, and it tightened the spec rather
than merely justifying it: because the constraint is that no picture data may reach the
Markdown **at all**, FR-006 had to state what happens to a picture that is *not* extracted.
A page-sized image leaves nothing (a marker on every page of a scan would be noise in both
the file and the knowledge base); a picture skipped for being too small or past the ceiling
leaves a note, because that is information the operator would otherwise lose. That
distinction would have surfaced as a defect report if the clarification had been defaulted.

**Open for planning, not a spec defect.** The spec assumes the engine can return pictures as
referenced files. The option exists — `image_export_mode` accepts `embedded`, `placeholder`,
and `referenced`, and defaults to `embedded`, which is why the current Markdown contains
picture data — but *how* the image files come back over the async interface this service
uses is unverified, and it may bear on the single-use-result rule (research.md R3). Given
this repository's recent history of decisions built on unverified engine behaviour, confirm
it against the pinned image before `/speckit-tasks`, not after.
