# Expression review

Structural checks are useful signals. They do not decide whether a presenter can use the page. Inspect the complete rendered or visible slide at normal presentation scale, then compare it with its neighboring pages and the relevant source.

## Review the page as an argument

Ask these questions together:

| Dimension | Review question | Repair direction |
| --- | --- | ---|
| Message | Can the audience state the page's point without reading a hidden side note? | Make the claim concrete, then ensure the primary visual and evidence support it. |
| Source fidelity | Are names, quantities, units, dates, comparisons, and limits correct and meaningful? | Restore the source-grounded fact or qualify the claim. |
| Hierarchy | Does visual prominence follow the message, or is the real explanation trapped in a narrow, secondary column? | Rebalance composition and text roles; do not merely shrink or enlarge type. |
| Language | Does the slide sound like something the presenter would say to this audience? | Replace production prose, vague labels, and assistant commentary with audience-facing language. |
| Visual evidence | Does each image, chart, or diagram prove, explain, compare, or frame the page message? | Change the visual role or remove decoration that competes with meaning. |
| Geometry | Are alignment, spacing, crop, mask, border, and overlap visually coherent? | Adjust the actual objects and visible edges. |
| Continuity | Does the page advance the narrative and relate to the surrounding pages? | Repair repetition, gaps, or a sudden visual system change. |
| Delivery | Is it clear what is editable, rendered, inspected, or not verified? | Report the actual host result without inflating it. |

A checklist result is not a substitute for this review.

## Review the deck as a designed sequence

For an existing PPT redesigned throughout, compare the source and result page by page before judging polish. Confirm that required source facts, names, diagrams, images, tables, notes, and advanced objects still serve their intended role. A new visual system can reorganize material when authorized, but should not quietly erase evidence or flatten an editable or animated object.

For a deck made from Word, notes, or other source material, compare the planned page messages with the source coverage map. Confirm that the deck has a genuine beginning, development, and close appropriate to its audience, and that important source facts have an explicit place or a documented reason for omission. Do not turn document headings into slide titles mechanically.

Across either route, inspect the recurring design grammar: title and evidence hierarchy, alignment, text width, image treatment, contrast, navigation, and section rhythm. A government evidence page, competition proof page, business decision page, teaching example, and editorial portfolio case should not all inherit one generic card composition. Check purposeful consistency across the deck and meaningful variation from page to page.

## Audience-facing language

Visible content must be written for the meeting, classroom, defense, briefing, or ceremony. It is not a record of how an assistant made the page.

Do not place any of these on a slide or in speaker notes unless the user explicitly asks for them as content:

- instructions to revise, review, connect, verify, or hand off;
- AI or tool commentary;
- layout advice, prompt fragments, placeholder process descriptions, source locations, or internal status;
- generic “this project” statements that hide a precise subject, observation, responsibility, or action.

Specific business processes, project descriptions, and formal institutional language are appropriate when they state real information for the audience. Judge context rather than banning individual words.

## Hierarchy and small text

The defect is not simply a numeric font size. A page becomes hard to use when the audience must find the essential claim inside a visually subordinate rail, when several small captions act as the real narrative, or when a large heading has no meaningful relationship to the text beside it.

Repair the page's relationship:

- give the evidence and explanation a proportionate role;
- combine fragments that belong in one thought;
- replace a decorative heading with a useful claim;
- split a page when it carries two incompatible messages;
- use a diagram, image, comparison, or sequence when prose is trying to do visual work.

Do not impose a fixed size, number of cards, or title-to-body ratio. Meaning, whole-page rhythm, and the reference determine the appropriate balance.

## Meaningful numbers and ornaments

Default to no isolated 01, 02, 03, 04, empty numbered circles, attached lines, or arbitrary English labels. Preserve a number when it communicates a real quantity, page navigation, approved chapter structure, or a requested sequence. Preserve template navigation when it is actually used by the presentation.

Remove an ornament by repairing the composition, not by replacing it with a different empty ornament.

## Image geometry and overlap

Evaluate visible image edges, not only the shapes behind an image.

- A rectangular photo inside an oval or rounded backdrop remains rectangular unless the photo itself has a matching crop or mask.
- If the intended image treatment is rounded, elliptical, or irregular, apply the treatment to the visible image and inspect focal content after cropping.
- If source identity, document edges, or comparison detail must remain visible, choose a frame that preserves it rather than forcing a crop.
- Borders, shadows, and background shapes should reinforce the image geometry instead of exposing a conflicting rectangle.
- Ensure no text, data, captions, faces, or meaningful diagram area is inadvertently hidden by another object.
- Intentional overlap must make the layer relationship clearer; it must not look like an unreviewed collision.
- A web-sourced image should depict the intended real subject; a generated illustration should not look like documentary proof. Check the actual chosen image and crop, not only the asset plan or prompt.

## Adjacent pages and real viewing scale

Review changed slides beside the prior and next slide. Look for duplicated conclusions, missing transitions, sudden density shifts, repeated hero images, and visual motifs that no longer mean anything.

Where page images or previews are available, open them and make a concrete assessment. A successful save, export, or object scan does not prove expression quality. If a capability cannot render or inspect a slide, report that limitation rather than declaring it visually approved.

## Record a concise review

Use the aippt-expression-review-v1 schema in [handoff schemas](handoff-schemas.md). Each observation should name:

- the page or scope;
- what is visible;
- why it helps or harms the intended message;
- the repair performed or the reason no repair is needed;
- whether visual evidence was actually inspected.

Bind the review to an opaque document and slide revision when the host supplies one. Record the state of visual inspection, adjacent-page check, and preservation check as performed, not_performed, or not_comparable. State the non-target-slide comparison result rather than merely promising preservation.

A review becomes stale after a visible copy change, geometry change, image crop or mask change, theme or master change, source-content change, or save/reopen change. Record that invalidation and inspect the current revision again before describing the page as reviewed.

Do not turn the reviewer notes into slide text.
