---
name: aippt-studio
description: Route presentation work in the AI PPT workspace through the correct editing, template adaptation, or new-authoring path.
metadata:
  short-description: Route AI PPT Studio work
---

# AI PPT Studio entrypoint

Use this skill for PPT/PPTX production, revision, homework, template adaptation, review, and delivery in this workspace. Studio is the execution and task-state system; the current assistant makes content and visual decisions. This skill is a route selector. Load detailed contracts only for the path being used.

Current user instructions and authorization take precedence over defaults, old Works, and retrieved examples. Reuse context already supplied. Do not create a production Work for Studio/skill/workflow maintenance or template-library cleanup. New production tasks get one minimal Work; follow-up revisions reuse its current Work and editable PPTX.

## Choose one of three authoring paths

1. **Revise an existing deck locally.** Keep the current editable PPTX and build source. Change only requested pages or objects with source-hash-bound edits, preserve accepted and non-target content, inspect the complete affected page and its neighbors, then publish the revision to the same current PPTX path. Use the [local revision entry](../../../aippt-studio/docs/AUTHORING_ENTRYPOINTS.md#1-local-revision) and [quick revision contract](../../../aippt-studio/docs/QUICK_REVISIONS.md).
2. **Adapt a real editable template.** Select an actual source PPTX/page whose structure will be reused, map its objects and content to the current brief, replace old facts/media, build the editable candidate, and inspect revised pages and neighboring narrative. For a job-bound template adaptation, bind through `library-hybrid-bind` and then run the current job; follow the [template adaptation entry](../../../aippt-studio/docs/AUTHORING_ENTRYPOINTS.md#2-real-template-adaptation) and [template-first command contract](../../../aippt-studio/docs/TEMPLATE_FIRST_WORKFLOW.md).
3. **Create a new deck.** Start from the audience, occasion, page messages, evidence, and neighboring-page logic. Search for relevant source layouts when they help; use them as inspiration unless an actual editable template is selected and bound. Build editable PowerPoint objects and review the actual rendered pages. Use the [new authoring entry](../../../aippt-studio/docs/AUTHORING_ENTRYPOINTS.md#3-new-composition).

The task intent and technical route are related but separate. A local patch stays a local patch even though its file is PPTX; a template route requires a real source template or requested edit target; a new composition must not claim template inheritance. Read [workflow](../../../aippt-studio/docs/playbook/workflow.md) for job files, route boundaries, failure handling, and conditional closure rules.

For a new deck or substantial redesign, read the Work's compact `workspace/authoring-context.json` and current shared entrypoints. Turn the presentation form into this task's specific story before drawing pages: audience need, central message, argument or learning order, evidence boundary and each page's actual relationship. Compare suitable whole-deck examples first; then use Gaojie for individual page relationships and components. Reporting, teaching, technical explanation, academic or competition defense, brand and ceremonial storytelling retain their different purposes; no universal slide sequence, palette or card layout is imposed. Use the [two-level design workflow](../../../aippt-studio/docs/AUTHORING_ENTRYPOINTS.md#two-level-reference-workflow).

New versioned Works use `design-plan <job> --json` to inspect missing decisions and `design-plan <job> --request <author-plan.json> --json` to record the current assistant's reasoning before build. Complete it as assistant work, not a form for the user to fill or another user approval. Native and template builds check the same plan; changing current requirements, brief, content, choices or selected reference evidence invalidates it. `native-scene` binds the authored objects, and existing expression review judges actual pages. Deliberate new composition is valid with a concrete reason; local edits and faithful reconstruction do not inherit full-deck replanning. The system is stored in shared code and startup guidance and does not depend on remembering this chat.

## Always preserve these rules

- Apply current `user_authoring_defaults` even without a matching historical example. Keep assistant replies, editing instructions and QA advice outside slide copy and presenter notes. Review hierarchy, text width, proportions, image meaning and meaningful labels on the whole page; fixed font sizes are diagnostic references. Follow the [presentation quality contract](../../../aippt-studio/docs/PRESENTATION_QUALITY.md).
- Judge references by topic and substantive content pages. Distinguish a worthwhile content layout from editability, motion and successful text replacement. Rich career/contest evidence may need several coordinated groups; do not force every topic into sparse cards or equal photo boxes. Inspect image roles, grouping and claim–evidence relationships, and record both what to borrow and what to reject. Follow [content-layout selection](../../../aippt-studio/docs/AUTHORING_ENTRYPOINTS.md#judge-content-layouts-before-selecting-a-source); user-rejected examples are not design baselines.
- When a page needs a layout or reusable component, use [gaojie-layouts](../gaojie-layouts/SKILL.md) to consult the persistent 31-category learning. Select by the current content relationship, inspect the source page and native objects, and map current facts/media. Component galleries require selecting a group; ordinary local edits do not need a new library analysis. Concrete reuse findings can be retained through `library-layout-learn`.
- Use the current Work's verified runtime and wrapper, or the documented maintenance runtime for shared changes. Do not borrow historical Work dependencies. A capability listing is distinct from a recorded generation, render or native probe.
- Deliver editable PPTX by default. If the user approves the image currently being reviewed and asks for its deck, identify and deliver the exact editable PPTX that produced it at the next safe file boundary. If only one page was reviewed, state that scope; if the matching deck is uncertain, say so. This handoff does not imply native PowerPoint QA, review of unseen pages, user acceptance of the full deck, or permission to close/archive the Work.
- Distinguish direct internal PNG previews from native PowerPoint review. Inspect actual page images during authoring; use native PowerPoint only when requested or when the rendering, fonts, master content, media, or requested behavior makes it necessary. Apply QA in proportion to the change and its risks. Static page images do not establish animation playback.
- Before a prototype/final expression decision, the current assistant inspects actual page images and records the review where the workflow requires it. This is assistant self-review, not an extra user approval. Automated checks locate issues; they do not approve expression.
- Speaker notes contain presenter-facing talk tracks only. Never place local paths, Work/task/build/asset paths, `file://` URIs, asset locator filenames, or `[Sources]` provenance blocks in notes. Keep provenance in ledgers, source notes, manifests, and QA records.
- Keep received materials and reproducible build source, current editable output, and only the evidence needed to identify and verify the result. New production Works use `storage_profile=minimal`; maintenance creates no Work. Do not create raw conversation copies, redundant reports, or legacy retrospectives for minimal Works.
- Creation or passing checks do not establish user acceptance. Work closure and archive remain separate; apply closure only under the relevant Work lifecycle and clear user agreement.

## On-demand references

- Job state, routes, failure recovery, impact-scaled review, and legacy closure applicability: [workflow](../../../aippt-studio/docs/playbook/workflow.md).
- Real template search, preview, confirmation, mapping, hybrid-build contracts, motion, and template candidate review: [template-first workflow](../../../aippt-studio/docs/TEMPLATE_FIRST_WORKFLOW.md).
- Homework/reference matching and iterative visual correction: [homework lessons](../../../aippt-studio/docs/playbook/homework-lessons.md).
- Production planning, template selection, or multi-task cleanup: [production knowledge base](../../../aippt-studio/docs/playbook/ppt-production-knowledge-base.md).
- Failures, provenance, old execution scripts, and cleanup decisions: [evidence index](../../../aippt-studio/docs/playbook/evidence-index.md).
- Runtime and wrapper selection, exact CLI syntax, and Adapter invocation: the current Work's `workspace/aippt.sh` or `aippt.ps1`, [Adapter architecture](../../../aippt-studio/docs/ADAPTER_ARCHITECTURE.md), and the relevant command contract.
- Storage and minimal Work lifecycle: [workflow storage](../../../aippt-studio/docs/WORKFLOW_STORAGE.md).

## Invariants

- Never label image-based output as an editable master.
- Never hand-edit Studio state, approvals, comments, Adapter results, or build manifests.
- Keep Adapter candidates in task-local staging until Studio validates and promotes them.
- Current user instructions and current evidence outrank historical cases. A retrieved layout is a candidate, not proof it fits; the rendered output is the evidence of adaptation.
