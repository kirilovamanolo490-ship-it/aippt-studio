# Evidence and learning

A presentation workbench becomes more capable only when it separates facts, feedback, verified outcomes, and future hypotheses. A polished summary is not enough.

## Keep four layers distinct

| Layer | What belongs there | What it can prove |
| --- | --- | --- |
| Original task record | The supplied brief, source material, actual user feedback, and available conversation trace. | What was requested and observed. |
| Change and verification record | The edit plan, tool result, visible review, source comparison, and artifact identity when available. | What ran, what changed, and what was actually checked. |
| Conditional guidance | A concise rule with its trigger, scope, exceptions, and supporting evidence. | How a later task should reason in a similar situation. |
| Core-repair candidate | A suspected systemic defect linked to a reproducible failure and a validation plan. | A problem worth investigating, not a completed fix. |

Never substitute one layer for another. A successful tool result is not user acceptance. An archived conversation is not a complete project retrospective. A user preference on one page is not automatically a global style rule. A code patch is not proof that a slide reads well.

## Convert feedback into a usable learning event

When the user corrects a result, capture the actual issue before generalizing it:

1. Preserve the user's essential wording or a faithful short quotation.
2. Identify the task context: request route, presentation form, page role, source boundary, and affected scope.
3. Record the observed defect, failed approach, and requested correction.
4. Record the concrete revision and what was visibly or technically checked.
5. Decide the current status: verified, unverified, rejected, needs more context, or local only.
6. State when the lesson applies and when it does not.

For example, “the image frame looks wrong” becomes a reusable lesson only after identifying whether the cause was an unmatched crop, a hidden rectangle, a meaningful source edge that had been cut off, or a visual form that did not fit the page. Do not convert the phrase itself into a blunt global rule.

## Archive handoff bundle

When the user asks to archive, complete, hand off, or retain a task, create one self-contained `aippt-handoff-bundle-v1` using [handoff schemas](handoff-schemas.md). Its inner `aippt-learning-packet-v1` records feedback and state; the bundle also carries the full presentation intent, mandatory capability record, and any referenced expression review.

The bundle is a source-side structured handoff for an authorized archive or local Studio system. Both the outer bundle and inner packet declare `claim_provenance` `source_report_unverified`. It contains no hidden reasoning, credentials, raw local paths, or customer material beyond what the user intentionally provides in the current surface. It should include:

- the presentation intent and scope;
- actual feedback and the contextual cause or uncertainty;
- edits and review evidence that are genuinely available;
- conditional lessons, exclusions, and unresolved items;
- clear separation of tool execution, artifact review, and user acceptance;
- a bridge capability record that includes `transmit_handoff`.

The default transfer scope is a sanitized structured summary only. Do not automatically copy the open deck, visible slide content, notes, Word/PDF material, images, app data, or raw conversation into a bundle. V1 has no attachment or attachment-transfer field. If a separate file needs to move, keep it outside this source contract and require an independently user-visible, authorized process.

Keep the source handoff state honest:

| Source packet state | Meaning |
| --- | --- |
| `prepared_not_transmitted` | The packet was prepared in the current surface only. Its transmission evidence is empty and `transmit_handoff` cannot be confirmed. |
| `transmitted` | An authorized, evidenced direct bridge reports a source-side send attempt. The capability record must have `bridge_mode` `direct_bridge` and confirmed, evidenced `transmit_handoff`. |

A source handoff never claims `received`, validation, rejection, or a receiver receipt. When a local importer accepts exact bytes, it creates a separate local `received_external_unverified` receipt. That receipt freezes the bundle and labels source feedback, tool, visual-review, authorization, and acceptance claims untrusted; it does not promote a lesson or alter the Work state.

If the host cannot persist or export a bundle, provide it as structured text the user can save or pass to the authorized local workbench. Do not pretend the bundle has already been ingested.

## How durable improvement should work

A connected local Studio may process a bundle with this sequence:

1. Preserve the bundle bytes immutably as external evidence and write a local `received_external_unverified` receipt.
2. Keep original conversation provenance separate. Do not insert the bundle into a raw-conversation archive or treat its prose as a verified user message.
3. Let a current local task explicitly corroborate a useful observation with actual sources, visible review, and user direction before it becomes a local lesson.
4. Activate only narrowly scoped, source-grounded guidance for later tasks.
5. Export portable guidance only as fixed base rules and, when local integrity permits, finite generic learned families. Its `learned_projection` state makes inclusion or withholding explicit; raw learned wording never leaves the local workbench.
6. Treat potential software defects as candidates. Reproduce the failure against the actual shared code, add a meaningful regression, preserve existing tests, independently review the change, and apply only when all checks hold.
7. Keep tool execution, artifact review, delivery, and user acceptance as separate evidence states.
8. Retry incomplete background maintenance quietly and audibly record pending work; do not describe a quota-limited or context-limited run as complete.

This package does not itself perform those local actions. Its responsibility is to make the handoff complete enough that a local workbench can safely decide what to learn.

## A durable archive loop

The intended improvement loop is deliberate rather than self-asserting:

1. Preserve the original source record and a bounded source-side handoff separately.
2. Freeze the received handoff with a local receipt, while keeping every source claim unverified.
3. Compare the observation with the current task's evidence, actual page review, and later user direction.
4. Promote only a narrow, source-grounded conditional rule when its trigger, exception, and verification are clear.
5. Treat a repeated workbench defect as a separate code candidate. It changes shared core only after an isolated before-failing/after-passing regression, existing-suite checks, independent review, and current-source validation.

This lets later presentation work benefit from a verified pattern without turning an archive summary, a local receipt, or an apparent successful edit into an automatic code change.

## Do not overlearn

A good learning rule has an explicit trigger and exception. Examples of bad generalization:

- turning one client's preferred palette into a universal style;
- treating a single page's requested wording as a ban on the same word in every context;
- assuming every image should be rounded because one photo needed a rounded crop;
- treating an archived or completed task as user-accepted without evidence;
- treating an upstream repository's presence as proof that it executed in a client deck.

When the same defect appears across independent, well-contextualized tasks, raise the confidence of the lesson. When context is missing, retain needs-context rather than inventing a cause.

## Conversation boundary

Do not assume this PowerPoint interaction has access to an earlier ChatGPT conversation, local archive, or user memory. Use only the active presentation, supplied materials, approved connected sources, and the user's current instruction. Ask for a relevant handoff bundle or a concise prior decision only when it is genuinely needed.
