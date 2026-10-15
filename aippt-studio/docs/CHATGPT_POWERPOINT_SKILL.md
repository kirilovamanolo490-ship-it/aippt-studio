# ChatGPT for PowerPoint portable skill integration

## Purpose

The folder `integrations/aippt-studio-powerpoint` is the portable, uploadable expression of the AI PPT Studio workbench for ChatGPT for PowerPoint. It deliberately contains reusable presentation judgment and structured handoff rules, not the private macOS execution environment or task archive.

The external product references are [ChatGPT for PowerPoint](https://help.openai.com/en/articles/20001242) and [Skills in ChatGPT](https://help.openai.com/en/articles/20001066). Product availability, upload scanning, and supported actions can change independently of this repository.

The source remains separate from the local Codex discovery entry at `.agents/skills/aippt-studio`:

| Layer | Responsibility |
| --- | --- |
| Portable PowerPoint skill | Requirement routing, style and form reasoning, whole-page expression review, honest capability reporting, and source-side structured learning handoff. |
| Local AI PPT Studio core | Work lifecycle, source and raw-conversation evidence, host-native runtime selection, native Office operations, QA receipts, case retrieval, archive learner, and guarded core-evolution pipeline. |
| Approved bridge or manual bundle | Transfers the smallest necessary self-contained source report between the two layers. |

This separation prevents an uploaded skill from containing client materials, raw chat traces, local paths, credentials, macOS-only binaries, historical virtual environments, or a claim that it can edit local Studio code.

## Upload artifact

Build the artifact with:

~~~sh
./.venv-macos/bin/python scripts/package-powerpoint-skill.py
~~~

The packager validates the skill, rejects portable-source leakage patterns, creates `dist/aippt-studio-powerpoint-skill.zip`, and writes its SHA-256 manifest beside it. The ZIP contains a top-level `aippt-studio-powerpoint` directory with `SKILL.md`, `agents/openai.yaml`, and the portable references. Upload that ZIP through the ChatGPT Skills interface. The product-side scan is authoritative; a locally valid ZIP is not a promise that the workspace will enable it.

The local release gate also verifies a closed, usable agent-manifest interface and policy, canonical schema bytes, safe local links, and the same portable text boundary used by the receiver. It rejects non-text controls, invalid decoded Unicode, local/runtime references, concrete credentials or personal contacts, and structured aliases or markup wrappers that could hide them. Public URL routes remain usable, while a URL path is still checked for a concrete contact, cookie/header carrier, Bearer value, embedded data URI, or visually disguised local/Studio location. This validates the source ZIP; it does not turn the package scan into a claim that any remote service accepted the upload.

ChatGPT's published PowerPoint guidance states that users can choose Skills with + or invoke a Skill with @ in the PowerPoint sidebar. Skills and app availability can still depend on the plan, workspace controls, permissions, and rollout. The portable skill must therefore never assume a particular app, local file, or advanced slide operation is available.

The release contains versioned machine contracts for presentation intent, revision-bound expression review, per-operation host capabilities, source-side learning handoff, a self-contained manual bundle, and sanitized guidance readback. The internal release manifest identifies its portable boundary: no local paths, runtime, credentials, client materials, automatic attachment transfer, or automatic core mutation.

## Practical use

1. Install the ChatGPT Microsoft add-in and sign into the intended ChatGPT account.
2. Upload and install the ZIP from the ChatGPT Skills area when the account or workspace exposes skill uploads.
3. Open PowerPoint, launch the ChatGPT sidebar, then choose the installed skill under + or invoke `@aippt-studio-powerpoint`.
4. Give the task brief and, when relevant, an active template, source material, slide scope, and preserved pages.
5. Let the skill classify the route before it edits. At review or archive, request an AIPPT handoff bundle.
6. If no approved direct bridge is visibly available, the PowerPoint task emits a `prepared_not_transmitted` self-contained source bundle. Save it and import it into the intended existing in-progress local Work with the `packet-import` command below.

For a supplied PPT that needs **overall beautification**, tell the skill the audience and occasion, the intended style family (for example competition defense, business report, or formal government briefing), what source objects must stay, and whether page order may change. The skill inventories the existing pages, chooses a deck-wide visual grammar, maps old pages to new roles, and reviews every revised page. For a **new PPT from Word, notes, or other material**, provide the source and presentation occasion; the skill maps source facts to page messages before composing, rather than converting each paragraph into a slide.

The portable skill also gives a concrete visual sourcing decision for each page: keep a supplied image, seek an identifiable real-source visual, generate a clearly illustrative scene, make an editable diagram/chart, or use no image. Web/image search, image generation, insertion, and connected portfolio access are conditional on the operations actually exposed in the current PowerPoint surface. The skill cannot grant itself missing tools or read this Mac's private portfolio merely because the ZIP has been uploaded. The user can supply representative portfolio decks or an approved connected source; the skill then extracts composition and image rules without copying their unrelated client facts. The official PowerPoint documentation confirms document-to-deck creation, revision of existing decks, skills, and connected apps where available, while noting that advanced edits and template adherence may vary by host.

Two ordinary requests can be short because the skill now carries the route details:

- “把当前这份 PPT 整体美化成竞赛答辩风格。保留原有事实、图表和可编辑对象；参考我提供的作品集样稿。缺少合适图片时查找真实素材，概念页可适当生成插画，逐页检查后再报告。”
- “根据这份 Word 新建工作汇报 PPT。先整理事实与页面主旨，再按正式汇报的节奏排版；图片只在能说明内容时使用，所有数据和名称以 Word 为准。”

The local archive learner may activate verified, scoped generic guidance as projects finish or feedback is processed; `guidance-export` produces a fresh read-only companion snapshot from that state. The already uploaded ZIP is a fixed release and does not silently change on ChatGPT's servers. After shared skill text or core rules change, rebuild and re-upload the ZIP; after a guidance refresh, provide the new snapshot to a later PowerPoint task through a current-task file or an actually authorized bridge. Archive status, guidance activation, ZIP release, remote installation, and a particular deck's visual approval remain separate evidence states.

## Guidance readback and local import receipt

The local Studio can prepare a current, de-identified guidance companion file:

~~~sh
./.venv-macos/bin/aippt guidance-export \
  --output dist/aippt-studio-active-guidance.json \
  --json
~~~

This output contains active generic rules and their applicability boundaries, but no raw user quotes, local paths, client materials, conversation archive, receipt locations, tool data, or user-acceptance claim. Its `learned_projection` field says whether it includes only requested base rules, includes a finite verified generic learned family, or withholds learned projection. It does not send or install itself. Review it, then provide it to the relevant PowerPoint task as a separate current-task file when desired.

For a manual PowerPoint handoff:

~~~sh
./.venv-macos/bin/aippt packet-import \
  --work-dir "<existing-in-progress-work-directory>" \
  --packet "<saved-aippt-handoff-bundle-v1.json>" \
  --origin chatgpt-powerpoint \
  --json
~~~

The importer accepts only an existing intake or in-progress canonical Work. It freezes exact bundle bytes under that Work with an independent local receipt, verifies that intent/capability/review IDs resolve within the bundle, prevents conflicting packet-ID reuse, and labels every feedback, tool, capability, visual-review, authorization, and acceptance assertion as external and unverified. Its receipt state starts as `received_external_unverified`.

The source bundle itself is always a source report: its outer bundle and inner packet have `claim_provenance` `source_report_unverified`; its packet can be only `prepared_not_transmitted` or `transmitted`; it has no receiver receipt, receiver lifecycle state, validation/rejection result, or attachment-transfer record. `transmitted` is permitted only for a current-user-authorized direct bridge with nonempty source send evidence and confirmed, evidenced `transmit_handoff`; it still does not prove receiver arrival. The local importer is the only component that writes its own receipt after accepting bytes.

Importing does not modify the Work state, mark user approval, alter the raw-conversation archive, add a learning event, activate a rule, run maintenance, prepare delivery, finish a Work, or repair Studio core. A local current-task review must corroborate any useful observation before the existing evidence-backed learning pipeline can consider it.

## Local receipt storage boundary

On macOS and Linux, a capable host stores packet bytes through a descriptor-bound directory chain. The importer opens the repository, `work`, canonical Work number, workspace, and receipt store one component at a time without following links; it rechecks that chain before it publishes a content-addressed packet directory. It writes a complete staged bundle and receipt, rereads both, then uses a no-replace directory commit. A link, reparse point, detached Work parent, malformed existing receipt, changed frozen bytes, or conflicting packet identity fails the import without promoting the source report.

The importer also leases the exact lifecycle record for the full packet transaction. A process-local keyed lease prevents two Studio imports in one Python process from accidentally releasing each other's POSIX record lock; the held lifecycle record and bound directory chain are rechecked before retained storage is created, before publication, and before a successful result is returned. This coordinates Studio and the documented lifecycle commands. It is not a substitute for the existing owner and filesystem trust requirements.

Windows uses **guarded exclusive-Work mode**, because ordinary Windows Python path APIs cannot provide the same descriptor-bound protection against an unrelated process that can independently rewrite the Work. This mode rejects symlinks and reparse points, requires stable file identities, locks cooperating imports, stages before publication, and rechecks the directory identities and retained bytes. It is an operator-constrained fallback: the code does **not** prove that a volume is local rather than mapped or shared, nor does it inspect Windows owner or ACL state. Before using it, the operator must independently establish that the Work is current-user-owned, exclusive, and on an appropriate local filesystem with known access control; do not use this mode for network/shared locations, unknown ACLs, removable filesystems without stable identities, or a Work that another non-cooperating process can modify. The automated suite exercises this branch through a controlled fallback on macOS; it does not substitute for a first probe on the target Windows NTFS/ReFS host, which must succeed before that host is treated as verified for production packet retention.

## Current product boundary

The portable skill is aimed at ChatGPT for PowerPoint. The current official Word add-in guidance says Word conversations, Memory, and Skills do not carry over to that add-in. Do not use a Word sidebar chat as the durable learning store for the workbench.

ChatGPT for PowerPoint itself has known limits around template adherence and advanced presentation edits. A saved edit or static export does not replace actual visible slide review. When the requested result depends on native animation or movie playback, obtain actual playback evidence in the capable local Studio path.

## Maintenance

- Keep portable instructions concise and conditional. Move mode-specific detail to references.
- Do not copy full local docs, Work packages, past decks, GitHub checkouts, dependency directories, source paths, raw conversations, or customer assets into the upload package.
- A feedback observation enters the inner learning packet only with its context, correction, verification state, and applicability boundary.
- The normal handoff is a self-contained source bundle with a sanitized structured summary inside it. Do not automatically transfer the deck, slide contents, notes, Word/PDF material, images, connected-app data, or raw conversation. V1 has no source attachment-transfer record.
- A user request to return the editable PPTX currently associated with the inspected page is an immediate artifact handoff, separate from a learning bundle or Work closure. Give the exact current file when the host can provide it; do not wait for a new capability matrix, archive packet, unrelated QA pass, or repeated approval. Identify the reviewed page scope and keep native PowerPoint validation as a separate claim.
- A PowerPoint host reports authoring, editing, and handoff capabilities per operation: reading the active slide, text edit, geometry edit, page rendering, save/reopen, PDF export or inspection, animation playback, and handoff transmission. For ordinary PPT creation, beautification, and small revisions, save the editable PPTX and inspect only the pages needed for the requested scope. When the assistant needs to see one page or the whole deck, local Studio can render internal PNGs directly from the current PPTX with `export-png` and optional `--slide N`, then read those images without a mouse save dialog or intermediate PDF. Its OfficeCLI HTML rendering is approximate, not native PowerPoint visual acceptance. Do not automatically deliver PDF, MP4 or PNG files. When the user explicitly requests PDF, MP4 or PNG export of an already finished PPTX, use a direct export operation exposed by the current host and return its identifiable nonempty result. This conversion bypasses the full capability report and does not repeat authoring or require full visual/media QA, handoff packets, archive, or Work-closure gates. The portable skill must not assume local CLI or filesystem access that the host does not expose. A Mac PowerPoint MP4 may be a slide-sequence preview and may omit object animations; motion playback verification is separate and applies when the user requests motion acceptance. A user-supplied PDF source may still be read when relevant. In a normal authoring capability report, set `export_or_inspect_pdf` according to the actual PDF operation (`confirmed` when evidenced, otherwise `not_attempted`). Do not infer one operation from another.
- Expression review binds observations to an observed document revision. A later copy, geometry, crop, theme, source, or save/reopen change invalidates the prior review.
- A suspected core defect remains a candidate until a local isolated reproduction, new meaningful regression, existing-suite validation, independent review, and current-source checks allow a shared-core change.
- Rebuild the ZIP after changing the portable skill and run the focused package tests. The root skill source can continue to evolve independently for local Codex use.
