---
name: aippt-studio-powerpoint
description: Guide full-deck beautification of an existing PowerPoint and creation from Word, notes, or other sources. Route by audience and occasion, use supplied portfolio references, research or generate suitable visuals when the current host permits, review whole-page design, and capture evidence-backed lessons. Use in ChatGPT for PowerPoint; do not use for unrelated writing.
metadata:
  short-description: Source-led PPT redesign, creation, visuals, and review
---

# AI PPT Studio · PowerPoint

Use this as a reusable presentation workbench inside ChatGPT for PowerPoint. It turns a brief, source material, an open deck, and later feedback into an intentional presentation workflow. It is a playbook, not a promise that an unverified tool, local file system, native Office feature, or prior conversation is available.

The goal is a deck that a real presenter can use: every page has a purpose, the hierarchy supports that purpose, visuals carry evidence or meaning, and changes preserve the right parts of the original. Do not replace this judgment with a universal font-size rule, an AI-card layout, or an arbitrary color preset.

For ordinary PPT creation, whole-deck beautification, and small revisions, save the editable PPTX and perform only the visual checks needed for the requested scope. Prefer the host's direct file operations for inspecting a PPTX, creating a blank deck, and saving a copy; do not spend time clicking through PowerPoint to perform routine file handling. Open PowerPoint only when the user asks to view the deck in the app or a native review is specifically required. During authoring, when you need to see one slide or the whole deck, use the host's direct page-rendering capability and inspect the resulting images; do not drive PowerPoint's save dialog with a mouse or create an intermediate PDF just to see pages. The local Studio provides `export-png <current.pptx> --slide N` for one page and `export-png <current.pptx>` for the full deck; keep these internal images in a task QA or temporary area. Its OfficeCLI HTML images are approximate previews, not native PowerPoint visual acceptance, and the normal local build-preview path does not perform a PowerPoint open/save/reopen roundtrip.

The user may redirect or stop work at any stage. At the next safe file boundary, cancel obsolete queued steps and apply the new instruction to the current artifact. If they approve the image currently being viewed and ask for the PPTX, stop unrelated generation, tests, and archive steps; identify and deliver the exact editable PPTX that produced the image. If the current image is only one page, state that only that page was reviewed; if the matching deck is unavailable or uncertain, report the limitation rather than guessing. This delivers the current artifact but does not claim full-deck native PowerPoint QA, acceptance of unseen pages, or authorization to finish/archive a Work. Do not make an AIPPT learning bundle a prerequisite to returning the PPTX. Keep those states separate and honor the user's explicit scope.

Do not automatically deliver PDF, MP4 or PNG images to the user. When the user explicitly asks in the current task for a finished deck's PDF, MP4 or PNG files, use the existing PPTX and the direct export operation available in the current host, then provide the result. This format conversion does not repeat creation or add a full visual/media QA, archive, or Work-closure gate; confirm only that export succeeded and produced nonempty output. This portable skill cannot assume access to the local CLI or filesystem beyond what the current host exposes. A Mac PowerPoint MP4 may be a slide-sequence preview and may omit object animations; checking motion playback is a separate request. A PDF supplied by the user as source material may still be read when relevant; record the capability according to the actual PDF operation performed (`confirmed` if evidenced, otherwise `not_attempted`).

If the image to inspect already exists in the current host, read that image directly; do not take another app screenshot or render the PPTX again just to see the same pixels.

## Establish the working context

Before editing or composing, identify what is actually available:

- the active presentation, selected slide or object, and editable scope;
- whether the user supplied an existing PPT for whole-deck beautification, or Word, notes, data, and other sources for a new deck;
- the user's audience, occasion, decision or outcome, language, source material, and non-negotiable content;
- the requested change boundary, including accepted pages, a reference deck, a template, or content that must remain untouched;
- any supplied portfolio examples and whether they are binding references or merely inspiration;
- the host capabilities needed for the request, such as slide editing, rendering, web or connected-source research, image generation, image insertion, export, or access to an approved connected source.

Use the active deck and supplied sources as the authority for facts and style. If a detail is unavailable, state the limitation in the response or plan. Do not invent a deck state, source fact, native export, or successful edit.

Treat a user's current instruction as stronger than a historical convention. Ask only when a decision cannot be inferred from the deck, sources, or request and would materially change the result.

Keep slide copy, speaker notes, and the working response in the language requested by the user or established by the authoritative source. Do not let an English skill instruction turn a Chinese briefing or another source-language deck into mixed-language presentation copy.

## Classify the request before acting

Select one primary route. Keep the rationale in the working plan, never as visible slide text.

| Route | Use when | Main obligation |
| --- | --- | --- |
| Exact local revision | The user identifies specific pages, objects, or defects in an existing deck. | Change only the intended scope and preserve accepted or out-of-scope material. |
| Template derivative | A real template or prior deck should carry forward, with new content. | Reuse its actual visual language and page roles rather than treating it as a loose color sample. |
| New composition | The material needs a new deck or a new reusable template. | Build a coherent story and visual system for this audience and presentation form. |
| Faithful reconstruction | A reference page, screenshot, or prior slide must be closely reproduced. | Match composition, relationships, and editable structure where the host supports it; disclose any image-based fallback. |
| Visual alternate | The user wants a new expression of the same content. | Preserve facts and intent while making the new visual direction explicit. |

Then classify the presentation form. Read [intake and routing](references/intake-routing.md) for the form-specific narrative and review lens. A government briefing, competition defense, business report, teaching deck, technical explanation, ceremonial narrative, and premium portfolio do not share one default layout or voice.

When the user asks to create a cinematic or motion-led presentation, also make the scene and playback plan in [intake and routing](references/intake-routing.md). A plan is not a rendered effect: claim animation or motion acceptance only when this host can produce it and actual playback or sampled video frames have been reviewed. A request only to export an already finished PPTX as MP4 follows the direct conversion path above; animation playback review is needed only when the user asks to verify motion.

For **whole-deck beautification of a supplied PPT**, use visual alternate when the visual system should change, or template derivative when the supplied template must remain the design contract. Set the scope to the entire deck; this is different from an exact local revision. Preserve factual content and source objects according to the user's instructions, map every source page to its new page role, and inspect the entire revised deck. For **a new PPT from Word, notes, or other supplied material**, use new composition unless an actual supplied template controls the result. Both workflows are specified in [intake and routing](references/intake-routing.md).

## Work from meaning outward

For each page, establish the audience-facing message, evidence, visual role, and relationship to neighboring pages before arranging objects.

1. Extract claims, facts, constraints, and source boundaries. Keep unsupported ideas out of the deck.
2. Make a concise page-story plan. Each page should earn its place in the overall argument, lesson, report, or narrative.
3. Choose the visual form that communicates the page message: comparison, sequence, process, evidence, scene, quote, data, diagram, or restrained editorial composition. Do not use decorative cards merely to fill space.
4. Edit or create only what the host can actually produce. Preserve editability when supported; clearly label a rendered or image-based result when that is the only available route.
5. Inspect the complete visible page at presentation scale, then inspect adjacent pages. Repair defects before treating the page as ready.

Build a design direction from the requested presentation form and any accessible portfolio or reference examples. Extract hierarchy, grid, image treatment, color relationships, page roles, and pacing; do not copy unrelated client content. Use [intake and routing](references/intake-routing.md) for distinct style families, a measurable first-draft type and spacing system, and the full-deck and document-to-deck workflows. A style family guides decisions across pages; it does not force every page into one layout.

## Keep visible slides audience-facing

Slide copy and speaker notes belong to the presenter and audience. Do not place assistant instructions, production steps, QA reminders, file locations, draft labels, prompts, or conversational replies on a slide or in notes.

Avoid generic AI symptoms:

- a huge headline with a narrow, overloaded column of tiny explanatory text that carries the real meaning;
- vague labels such as “project overview,” “key insight,” or “next step” when the page can make a concrete claim;
- arbitrary corner numbers, empty numbered circles, attached lines, decorative English tags, or other ornaments with no audience purpose;
- a title that merely repeats the first sentence below it;
- a page made of unrelated cards that does not advance the deck;
- a generic “this project...” statement when it hides the actual subject, finding, responsibility, or action.

These are judgment cues, not a ban on meaningful numbers, navigation, formal labels, business processes, or specific project language. Preserve any element that carries real context, a requested chapter, a verified quantity, or an established template function.

Read [expression review](references/expression-review.md) whenever composing, revising, or diagnosing visible slides. It covers whole-page hierarchy, wording, source fidelity, neighboring-page logic, overlap, and real image framing.

## Research and make visuals when they help

Use the supplied images and verified source material first. When the request permits additional visuals, identify the exact page claim or atmosphere each missing visual must serve. Search the web or an approved connected source for real people, places, events, works, products, and evidence visuals **only when the current host actually exposes that search capability**. Check identity, provenance, usable resolution, crop, and usage terms before selecting an asset. Record its source outside visible slide copy and notes.

Generate an image when an illustrative or conceptual scene will communicate the page better than a weak stock image, and **only when image generation and insertion are available in the current host**. Give it the presentation form, composition, aspect ratio, subject-safe area, and intended page role. Do not present a generated scene as documentary proof of a real event, person, product, place, chart, or government activity. If search or generation is unavailable, make a specific visual brief or use the strongest authorized supplied asset; do not claim a search or generation happened. Read [visual sourcing](references/visual-sourcing.md) for the source/search/generate decision and placement checks.

## Handle visuals as content, not decoration

An image must support the exact page claim. Review crop, focal point, mask, border, contrast, and nearby text together. A rounded or elliptical backing shape does not crop a rectangular photograph. Either use a matching crop or mask on the visible photo, or use a frame that matches the photo's actual edges.

Check for hidden text, unintended overlap, false alignment, accidental clipping, and visual weight that contradicts the page message. See [expression review](references/expression-review.md).

For a small late-stage correction, use the host's direct object controls when they are confirmed: select the exact object, move or resize it, or edit the relevant text in the open presentation. Keep the accepted pages intact instead of regenerating the whole deck for a simple nudge. After saving, bind any available local geometry, collision, or text-fit check to the **current** presentation bytes or host revision, then inspect the affected whole page and neighboring pages when the story changed. Older screenshots or QA reports do not establish the quality of a revised file. Local structural signals point to questions; intentional text-on-image compositions and meaningful numbered steps still need a page-level judgment. If direct control or an exact saved-artifact identity is unavailable in this host, record that limit rather than claiming the check occurred.

## Use structured review and learning handoff

At a meaningful correction, review point, or task end, distinguish these facts:

- what was requested and what changed;
- what was observed on the actual page;
- what source or feedback supports the judgment;
- whether a tool ran, an artifact was reviewed, and the user accepted the result;
- whether the lesson is local to this project, conditionally reusable, or only a candidate for a future core repair.

Use [evidence and learning](references/evidence-and-learning.md) and the schemas in [handoff schemas](references/handoff-schemas.md). Create an AIPPT handoff bundle when the user asks to archive, when a repeated defect is corrected, or when a task is handed back to a local Studio workbench. The bundle contains the full presentation intent, capability record, optional expression review, and its learning packet; do not emit only opaque references that a local receiver cannot resolve.

Before claiming an authoring, editing, or structured learning handoff operation, make a capability record that separates: reading the active slide, editing text, editing geometry, rendering a page, saving and reopening PPTX, exporting or inspecting PDF, playing animation, and transmitting a handoff. For each operation, report confirmed, unavailable, not_attempted, or unknown with host evidence. Returning the exact existing PPTX because the user asked for it immediately does not require a new full capability matrix or learning packet; identify the file actually returned and the page scope that was reviewed. Rendering PNGs for your own slide inspection is part of authoring, not an unsolicited user-facing export. Only deliver a PDF, MP4 or PNG set when the user explicitly requests that format in the current task. For a request solely to convert an already finished PPTX, bypass the full capability matrix and handoff packet: run the direct conversion, confirm only that it succeeded and produced identifiable nonempty output, then return it. Visual inspection and playback review are separate requests. A user-supplied PDF source may still be read when relevant; record `export_or_inspect_pdf` in a normal authoring capability record according to the actual PDF operation performed, using `confirmed` when evidenced and `not_attempted` when no PDF operation was performed. A visible text edit does not prove that the deck saved, reopened, exported, played, or reached a local Studio.

If the user supplies an aippt-guidance-snapshot-v1, read only its active rules and its learned_projection state. Keep the snapshot read-only. For every rule considered, record applied, not_applicable, needs_revision, or not_reviewed in the learning packet inside the later handoff bundle. Do not say a historical rule was loaded or applied when no supplied snapshot or evidenced bridge made it available. A portable snapshot can expose only base rules and finite generic learned families; it never exposes free-form archived feedback.

The ordinary learning packet inside a bundle is a sanitized structured summary. Its outer bundle and inner packet set claim_provenance to source_report_unverified. Do not put the active PPTX, slide contents, speaker notes, Word/PDF material, images, app data, raw conversation, credentials, local paths, receiver receipts, receiver lifecycle states, or attachment records into it. A manual bundle uses prepared_not_transmitted and empty transmission evidence. Use transmitted only when the user authorized this structured-summary transfer, a visible approved direct bridge ran, and the capability record confirms transmit_handoff with source-side evidence. Transmitted is still only a source send-attempt claim; the local importer writes any received_external_unverified receipt separately.

Do not claim that this portable skill has changed its own source, stored every prior conversation, received a local archive, or repaired a local Studio installation. The portable skill can make feedback explicit and emit a safe source-side handoff. A connected, authorized local workbench may later retain the external bytes, independently label their claims unverified, decide whether an observation is corroborated, and require a reproducible regression before changing core code.

## Respect the host boundary

The skill is deliberately portable. It contains no client materials, raw conversation history, local paths, local runtimes, credentials, PowerPoint automation binaries, or hidden project database.

Read [platform and bridge](references/platform-and-bridge.md) before using an external app, relying on a connected source, reporting native Office behavior, or handing learning back to a local Studio. Only say an action happened when the host supplies evidence that it happened.

Read [portable release contract](references/release-contract.md) for the data boundary, revision-bound review, preservation check, bridge state, and guidance-readback contract. The schema files named there are the canonical machine-readable forms; examples in prose never authorize unavailable operations or private-data transfer.
