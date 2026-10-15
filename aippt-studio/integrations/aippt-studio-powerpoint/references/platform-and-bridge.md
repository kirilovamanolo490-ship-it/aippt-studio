# Platform and bridge boundary

This package is portable by design. It carries decision rules and schemas, not a private workstation, an Office automation runtime, an archive database, or a promise that every PowerPoint operation is supported in the current host.

## Capability before claim

Before an authoring, editing, or structured learning handoff operation, establish the relevant capability. During authoring, use an available direct page-rendering operation to see a slide or the full deck and inspect those images; internal PNGs are not automatically a user-facing export. If the user asks for the current PPTX after reviewing its image, return the exact file available in the host without treating that file return as structured learning transmission or Work closure. A request solely to convert an already finished PPTX to PDF, MP4 or PNG uses the direct export path: the export command's successful result and identifiable nonempty output are sufficient. Do not build the full capability matrix or a handoff packet for that conversion alone.

For authoring, editing, or handoff, create an `aippt-bridge-capabilities-v1` record before reporting completion. It has one evidence-backed state for each operation below.

| Operation | Evidence needed before claiming success |
| --- | --- |
| Read active slide | The host exposes the relevant active slide, text, or presentation state. |
| Edit text | The host confirms the edit and the changed text is visible in the active deck or returned artifact. |
| Edit geometry | The host confirms the changed crop, mask, shape, position, or layout and the result is visible. |
| Render page | A page preview, actual visible slide, or comparable host result is available for review. |
| Save and reopen PPTX | A saved artifact and a successful reopen are both evidenced. |
| Export or inspect PDF | For an explicitly requested PDF output, evidence that the export succeeded and an identifiable result; visible page inspection is separate and only needed when requested. For a user-supplied PDF source, evidence that the source was actually read. |
| Animation playback | Actual slideshow or movie playback evidence, including the relevant intermediate state when timing matters. |
| Transmit handoff | A current user-authorized, host-visible direct handoff action has run; source-side evidence records that send attempt. |

Use `confirmed`, `unavailable`, `not_attempted`, or `unknown`. If the capability is absent, offer the best supported result: a slide plan, visible-content revision, review findings, or a self-contained handoff bundle. Do not falsely claim native Office validation, direct access to other local files, external-app execution, a successful export, or a received local import.

For ordinary PPT creation, beautification, or small revisions, save the editable PPTX and inspect only the pages needed for the requested scope. Local Studio can render internal PNG previews from the current PPTX by code for one page or all pages, without mouse-driven saving or an intermediate PDF; its OfficeCLI HTML result is approximate and must not be called native PowerPoint visual acceptance. Do not automatically deliver PDF, MP4 or PNG files. If the user explicitly asks to export the finished deck as PDF, MP4 or PNG, use the existing PPTX and a direct export operation exposed by the current host, provide the result, and report whether export succeeded. Do not repeat authoring or add full visual/media QA, archive, or Work-closure steps to this conversion request. The portable skill cannot assume access to a local CLI or filesystem that the host does not expose. A Mac PowerPoint MP4 may be only a slide-sequence preview and may omit object animations; motion playback verification is separate and applies when the user requests motion acceptance. A PDF supplied by the user as source material may still be read when relevant; set `export_or_inspect_pdf` according to the actual operation (`confirmed` when evidenced, otherwise `not_attempted`).

Web/image search, image generation, image insertion, and access to a portfolio or connected source are **separate current-task capabilities**. The v1 bridge capability schema does not include them, so record what is actually visible and what actually ran in the working plan or source-side evidence, not as a fabricated `aippt-bridge-capabilities-v1` operation. An uploaded skill cannot activate these tools on its own. When one is unavailable, follow [visual sourcing](visual-sourcing.md) and return a precise visual brief or use an authorized supplied asset.

## Three bridge modes

### Direct bridge

Use a direct bridge only when an approved app or integration explicitly exposes a Studio handoff action, the current user authorized this sanitized structured-summary transfer, and the capability record has `bridge_mode` `direct_bridge` plus confirmed, evidenced `transmit_handoff`. The source packet may then use `transmitted` with source-side send-attempt evidence.

A v1 source packet still cannot contain a receiver receipt, receiver state, validation/rejection result, or attachment record. `transmitted` does not establish receiver arrival. If a local Studio importer accepts exact bundle bytes, it writes its own separate `received_external_unverified` receipt and treats all source claims as unverified.

### Manual bridge

When no approved direct handoff action is available, produce one concise `aippt-handoff-bundle-v1` in structured text. It must embed the full presentation intent, mandatory capability report, any referenced expression review, and its `aippt-learning-packet-v1` rather than leaving opaque references behind. The user may save and import the bundle into an authorized local Studio flow. Mark the bundle and inner packet `prepared_not_transmitted`, leave transmission evidence empty, and set `transmit_handoff` to `not_attempted` or another truthful non-confirmed state.

A generated bundle, a source send attempt, a local receipt, structural validation, active guidance, and a core-repair candidate are different evidence states.

### Isolated session

When the host cannot access old conversations, local archives, or the required sources, work from the active deck and current materials only. Explain any limit that changes the result. Do not infer that a historical lesson was applied.

## Portable versus local responsibilities

| Portable PowerPoint skill | Authorized local Studio workbench |
| --- | --- |
| Routes the task, guides editing, checks expression, and emits source-side handoff data. | Stores private raw records, runs approved local tools, validates host-native runtimes, and maintains auditable project state. |
| Uses only active deck state, supplied files, and apps actually available in the current surface. | May use a private archive, local file system, native PowerPoint automation, and scheduled maintenance when separately configured. |
| States limits when the host lacks an operation. | Can verify runtime identity, tool receipts, artifact hashes, and regression evidence. |
| Never self-modifies code, receives a local receipt, or silently imports client history. | Can freeze a source bundle with a separate external-unverified receipt, then propose a core repair only after reproduction, tests, independent review, and safe application. |

The boundaries protect both reliability and client privacy. The portable layer remains useful even when no local workbench is connected.

## Apps, skills, and privacy

Connected apps are separate from skills. Availability depends on the current product surface, account plan, workspace controls, app authorization, and source permissions. Do not request or assume access to an app merely because it exists in another ChatGPT surface.

Read only the minimum connected-app scope needed for the current task. App visibility does not grant broad search, export, synchronization, or transmission permission. Do not automatically include app content or source inventories in a learning packet or handoff bundle.

Keep sensitive client information to the minimum required for the task. Do not embed local file locations, credentials, archive internals, or unrequested source inventories in slides, notes, or handoff bundles. The normal inner packet carries structured summary fields only. It never embeds the active deck, speaker notes, Word/PDF body, images, assets, raw conversations, receiver receipts, or attachment records. A v1 source bundle has no attachment-transfer mechanism; use a separately user-visible, separately authorized process outside this contract if a file must move.

## Advanced presentation operations

Complex chart, animation, shape, formatting, and slide-management operations can differ by host and may be unavailable. Preserve existing advanced content unless an edit is confirmed and visible. For motion, require actual slideshow or video playback evidence when the requested outcome depends on timing; static exports and object lists do not prove it.
