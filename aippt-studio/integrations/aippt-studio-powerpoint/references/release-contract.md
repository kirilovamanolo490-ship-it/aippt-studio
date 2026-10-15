# Portable release contract

This package is a portable PowerPoint decision and review skill. The machine-readable release statement is [release manifest](release-manifest.json). The packer verifies its exact file inventory, usable closed agent manifest, UTF-8 text, local links, strict contract schemas, and absence of workstation paths or runtime markers before creating an upload archive.

## What this release contains

- one PowerPoint-facing workflow in SKILL.md;
- full-deck and document-to-deck routing, presentation-form design grammars, conditional visual sourcing, expression-review, evidence, and bridge references;
- versioned JSON Schemas for intent, expression review, per-operation capability evidence, learning handoff, a self-contained source-side handoff bundle, and a sanitized guidance snapshot;
- no deck, attachment record, customer corpus, raw conversation, local file location, credential, native Office binary, or private runtime.

The release manifest identifies the current version of this portable contract. The external release manifest generated next to an archive records hashes and byte counts for a particular build. Neither manifest proves that a PowerPoint host ran a command, searched the web, generated an image, changed a presentation, saved a deck, or delivered a bundle.

## Compatibility and state contract

The intended surface is ChatGPT for PowerPoint. A host may expose fewer operations than the skill asks for. Before reporting authoring, editing, or structured learning handoff as complete, make an `aippt-bridge-capabilities-v1` report with evidence for each operation required by the current request. During authoring, direct page rendering may create internal PNGs for the assistant to inspect; those images are not automatically user-facing deliverables. If the user asks for the editable PPTX currently associated with an inspected image, return that exact available file without a new full capability report or learning packet; state the scope actually reviewed. For a request only to convert an already finished PPTX to PDF, MP4 or PNG, bypass the full capability report: confirm the export command succeeded and identify its nonempty output. Do not require a new slide review, save/reopen, animation playback, archive, or Work-completion operation as part of that conversion:

- read the active slide;
- edit text;
- edit geometry;
- render or inspect a page;
- save and reopen a PPTX;
- deliver a PDF, MP4 or PNG image set only when the user explicitly requests the format in the current task; a user-supplied PDF source may still be read when relevant;
- play an animation;
- transmit a handoff.

Each operation is `confirmed`, `unavailable`, `not_attempted`, or `unknown`. A broad statement such as “PowerPoint is available” is insufficient.

For ordinary PPT creation, beautification, and small revisions, save the editable PPTX and perform only the visual checks needed for the requested scope. Use direct code-rendered page images when the assistant needs to inspect a page or whole deck; on local Studio this is `export-png` with optional `--slide N`, and its output is approximate. Do not automatically deliver a PDF, MP4 or PNG set. For a current explicit PDF/MP4/PNG conversion request, use the existing PPTX and the direct export capability exposed by the host, then provide the result and report whether export succeeded. This conversion does not restart authoring or require full visual/media QA or archive/Work-closure gates. Do not claim local CLI access unless the current host actually exposes it. A Mac PowerPoint MP4 may be only a slide-sequence preview and may omit object animations; motion playback verification is a separate request for motion acceptance. A user-supplied PDF source may still be read when relevant. Set `export_or_inspect_pdf` according to the actual PDF operation: use `confirmed` when evidenced, and `not_attempted` when no PDF operation was performed.

The outer `aippt-handoff-bundle-v1` and inner `aippt-learning-packet-v1` both set `claim_provenance` to `source_report_unverified`. The inner packet has only two source-side transmission values:

1. `prepared_not_transmitted` — the packet exists only in the current surface or user-visible response, and `transmission_evidence` is empty.
2. `transmitted` — an evidenced, current-user-authorized direct bridge reports a send attempt. The bundle capability report must use `direct_bridge` and contain confirmed, evidenced `transmit_handoff`.

Neither value proves receiver arrival, structural validation, local evidence, active guidance, delivery, or user acceptance. V1 source contracts have no receiver state, receiver receipt, validation/rejection field, attachment record, or attachment-transfer field. A local importer that accepts exact bytes separately writes `received_external_unverified`; this receipt starts every source claim as untrusted.

User acceptance has separate enum states and verification evidence. A tool receipt, archive action, source packet transmission, or local structural import never upgrades user acceptance.

## Privacy and transfer rules

The normal manual unit is an `aippt-handoff-bundle-v1` whose inner packet is a sanitized structured summary. It embeds complete structured intent/capability/review records instead of dangling local-only references. It never embeds a PPTX, active deck contents, speaker notes, Word or PDF material, images, assets, raw conversation, credentials, local locations, receiver receipt, or file-transfer record.

Do not automatically export, synchronize, or transfer any PowerPoint or connected-app content through a learning packet. V1 does not model file transfer. Move any separate file only through an independently user-visible, authorized process outside this contract.

Portable source and handoff text must remain ordinary, valid text. Do not hide a value in a control character, invalid Unicode scalar, encoded wrapper, YAML alias or tag, unusual code fence, comment, markup attribute, URI parameter, or credential-shaped URL route. A public remote path can retain ordinary route naming, but it cannot conceal an explicit contact, cookie pair, Bearer value, embedded data URI, or visually disguised local or Studio path; query and fragment data never receive that path exception. The release and receiving boundaries inspect raw and decoded structured text. A legitimate documentation example may describe a field declaratively; it must not carry a concrete secret.

Use a direct bridge only when the current host visibly exposes it, the user authorized this current structured-summary transfer, `transmit_handoff` has relevant source evidence, and the bridge capability report is `direct_bridge`. A source bundle still records only its send attempt. Otherwise use a `prepared_not_transmitted` manual bundle.

## Guidance readback

A local workbench can offer an `aippt-guidance-snapshot-v1` only as a sanitized, read-only current-task file or an evidenced direct-bridge result. The snapshot contains active generic rules with their triggers and exceptions. It contains no raw feedback, user quotations, source paths, receipt locations, private project history, or client material.

Its mandatory `learned_projection` reports whether the snapshot has only requested base rules, includes a finite verified generic projection, or withholds learned projection because integrity is not ready or no generic projection exists. It must not claim the snapshot was current, loaded, or applied without the supplied snapshot or bridge evidence. The packet records the disposition of considered rules; it does not edit the guidance source.

## Review and preservation rules

Expression review binds visible observations to the document revision that was reviewed. It records the pre-review and reviewed revisions when available, the change categories that invalidate a review, and the evidence for the actual visible inspection.

For an exact local revision, perform a preservation check on non-target slides when the host permits comparison. Record performed, not_comparable, or not_performed with evidence. A later change to content, geometry, crop or mask, theme or master, source material, or host save/reopen state can invalidate the prior review and requires a fresh review before claiming current expression quality.
