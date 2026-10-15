# Workflow and editable contracts

> 2026-10-02 起，新制作 Work 默认采用 [精简存储](../WORKFLOW_STORAGE.md)：不保留本地对话副本，不生成重复复盘，交 review 时使用一份 production receipt。Studio、技能和库维护不创建 Work。本文旧格式归档和叙事复盘步骤仅在旧格式 Work 的兼容闭环明确要求时适用。入口选择见 [authoring entrypoints](../AUTHORING_ENTRYPOINTS.md)。


## Contents

1. Job files
2. Editing rules
3. Route boundaries
4. Failure handling
5. Work completion and knowledge consolidation
6. Asset gaps and web acquisition

## 1. Job files

- `planning/brief.json`: customer goal, audience, default 10-page target or explicit 1–200-page request, must-include items, rewrite and cloud permissions. Explicit counts must match the plan; insufficient sourced content is a recoverable planning gap, not permission to silently shorten the deck.
- `planning/intake-decision.json`: readable companion to the Brief's hash-bound `presentation_intent`. It records the request snapshot, task type, expression occasion, visual-system relationship, preservation contract and reasoning. It is an instruction artifact, not slide content.
- `planning/intake-requests/<sha256>.md`: immutable copy of the Work's original `input/request.md`. It guides the Brief but is intentionally outside the evidence ledger and `must_include`, so user instructions and assistant-facing wording cannot become a slide title or speaker note.
- `evidence-ledger.json`: source hashes and locked facts. Add facts only with a valid `source_id`.
- `planning/deck-plan.json`: one `SlideSpec` per output slide. Keep stable slide IDs so comments and revisions remain targeted.
- `reviews/comments.json`: written through the review API or CLI workflow; do not edit statuses manually.
- `deliverables/build-manifest.json`: final output hashes, engines, source hashes, and QA status.

## 2. Editing rules

- Preserve names, figures, dates, quotations, conclusions, citations, and mandatory content recorded in the evidence ledger.
- Rewrite narrative, page order, and wording when doing so improves the client's goal.
- Use action titles that state the page takeaway.
- Keep body content to three or four concise points. Put the talk track in `speaker_notes`.
- Keep `speaker_notes` presenter-facing. Do not include `[Sources]` blocks, local filesystem paths, task/build/source directories, asset locator filenames, or `file://` URIs. Provenance belongs in `evidence-ledger.json`, `source-notes.txt`, external-engine manifests, and QA records.
- Keep ordinary text and shapes editable. Use separate images only for photographs and complex illustration.
- Keep the slide meaningful without animation.

## 3. Route boundaries

- `native`: execution route for a new editable composition; HTML/CSS may be converted by dom-to-pptx.
- `template`: execution route when an actual editable source template is selected and its layouts, object relationships, theme/master, or page composition are to be inherited and adapted.
- `rebuild`: use for 1:1 reconstruction of screenshots, scans, or flattened PPT pages.
- `visual`: optional image-first alternate version; never replace the editable delivery without explicit approval.

A bounded edit to an existing PPTX uses the `local_patch` authoring intent and source-bound edit workflow. The file being a PPTX does not force the template route. A template may still be the source of a `template_relayout` when its visual system is retained while content structure changes. New composition can consult reference pages without claiming to inherit a template.

One job uses one primary route. Do not mix independent static renderers inside the same editable master.

Status includes a persisted `route_decision` with input roles, source, and reason. File extension alone never makes a PPTX a template. User overrides win and invalidate stale route artifacts and approvals.

`route_decision` is technical only. Every new Work also receives a separate `presentation_intent` in its Brief before approval:

- `local_patch`: bounded pages or objects in an existing deck; non-target pages, accepted master, brand objects and media are preserved when the contract says so. A generic template reassembler is not permitted to impersonate this operation.
- `template_relayout`: a required template or edit target supplies a visual system while the requested content structure is redesigned; source selection and page mapping remain explicit.
- `new_composition`: current source material is organized into a new editable story; this does not falsely claim inheritance from a reference template.
- `faithful_reconstruction` and `visual_alternate`: retain their existing editable/acceptance boundaries.

The intent also names a presentation form such as government briefing, business report, competition defense, teaching, technical explanation, ceremonial story or premium portfolio. These forms control narrative and composition principles; none is a universal colour palette, font-size rule or stock template. Explicit style direction, source material and accepted reference geometry remain authoritative.

Use `presentation-intent <job> --mode ... --form ... --visual-system ...` to override only the dimensions explicitly settled in the current conversation. The command clears stale Brief/prototype/final approvals, plans and render artifacts while preserving sources, raw request snapshots and review history. `--reset-automatic` removes hand-set dimensions and reclassifies from current request evidence.

Every route executes through `aippt_studio.adapters`. External routes write only to `build/adapter-staging` until Studio verifies job-relative paths, hashes, editability and previews. `doctor --json` reports maturity; only `automatic_ready` runs automatically. Blocked routes retain a visible manual handoff and never switch routes silently. Full details and commands are in `docs/ADAPTER_ARCHITECTURE.md`.

For a job-bound hybrid template, use `library-hybrid-bind` only after an editable `template` source and current plan are present; then resume the job through its normal `run` route. See the [template binding contract](../TEMPLATE_FIRST_WORKFLOW.md#studio-执行接口) for allowed states, mapping requirements, blockers, and evidence limits.

## 4. Failure handling

- Missing material: keep or return the job to `blocked`; name the specific material needed.
- Missing route engine: use the handoff file and install or invoke that engine; do not switch routes silently.
- A PowerPoint roundtrip required by the request or identified risk has failed: keep the affected QA claim unresolved, fix the source deck and rerun that check before making the claim. Do not impose a PowerPoint open/save/reopen cycle on every static output or local correction.
- Editable PPTX is the default deliverable. During authoring or revision, inspect actual page images with standalone `export-png` (one page or the full deck), save internal previews under Work QA/workspace or temporary space with `--output`, and read the images directly. OfficeCLI HTML rendering is approximate; page-image review and any separately required native review are distinct. Use native PowerPoint when requested or when fonts, master content, media, or requested behavior require it. Scale other checks to the affected content and risk. PDF/MP4 and user-facing PNG are not routine deliverables. If the user approves the image currently under review and asks for the deck, identify and deliver the matching editable PPTX at the next safe file boundary; a one-page review does not establish whole-deck review or native QA. When the user explicitly asks to convert an already finished PPTX, use standalone `export-pdf`, `export-mp4`, or `export-png` directly and return the result; do not reopen production gates, run media QA, or close/archive a Work for conversion alone. For a small change to an existing deck, use source-bound `fast-revise`, inspect the affected whole page, then `revision-publish` to update the same Work-local PPTX path. Keep revision history in `workspace`; do not multiply user-visible deck versions or rerun unrelated full-deck checks for every correction. See [quick revisions](../QUICK_REVISIONS.md).
- Confidential job with cloud disabled: do not call remote OCR, LLM, search, or image services.
- Revision batch: change only listed slide IDs, then complete the batch through the CLI.

## 5. Work completion and knowledge consolidation

This lifecycle applies when a Work is actually being closed. It does not delay delivery of an already requested PPTX, apply to Studio maintenance, or add legacy narrative steps to a minimal Work.

- A delivered or generated file is not the same as a completed Work.
- For a minimal Work, after the user-facing artifact and necessary review are ready, `prepare-work-close.ps1` creates the hash-bound production receipt and moves the Work to `review`. It does not create legacy retrospective or knowledge packets.
- Do not set `completed` until the user explicitly agrees the task is finished.
- Apply any legacy report/knowledge disposition only to a legacy Work whose compatible closure path requires it. Run `finish-work.ps1` only after clear user agreement to finish that identified Work; do not demand a fixed phrase or infer agreement from delivery.

## 6. Asset gaps and web acquisition

- After the deck plan exists, inspect each slide for real semantic or compositional asset gaps; do not add decorative downloads by default.
- Register gaps through `asset-need`. The search query and intended page purpose are part of the task record.
- The project Skill may use Firecrawl or another available live-web capability for discovery. Studio does not own the search provider or persist its credentials.
- Download only the selected direct asset through `asset-fetch`, with a source/licensing page, explicit license and attribution when required.
- Files live under `assets/acquired`; `asset-plan.json` and `asset-ledger.json` record the complete chain. Required pending needs block prototype/final approval and full build.
- See [asset-acquisition.md](asset-acquisition.md) for commands, storage, source selection and security boundaries.
