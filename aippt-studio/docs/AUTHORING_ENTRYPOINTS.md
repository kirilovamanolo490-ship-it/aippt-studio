# Authoring entrypoints

Use this page to choose the work shape and minimum review for the current request. For exact command syntax, follow the linked command reference and the active Work wrapper. A task's authoring intent describes what must happen; `route_decision` describes which engine executes it. Do not force every existing PPTX into the template route.

## 1. Local revision

Choose this path when the user asks to correct, polish, or replace content in bounded pages or objects of an existing deck and accepted content should remain intact.

1. Identify the current editable PPTX, its matching build source, requested page/object scope, accepted edits, and any preservation constraints. For a follow-up, reuse its current Work.
2. Use source-hash-bound, object-specific edits through `pptx-inspect` and `pptx-patch`, or the documented `fast-revise` workflow when the request fits that contract. Keep the build source/configuration aligned with the edit so rebuilding does not erase accepted changes.
3. Inspect the complete changed page and relevant neighbors. Compare non-target areas when the edit could affect them. Publish to the same current PPTX path with the documented `revision-publish` operation.
4. Add structural, notes, OfficeCLI, native PowerPoint, playback, or full-deck checks only when the changed material or user request makes them relevant. A local static text/geometry correction normally needs inspection of the affected whole page; it does not inherit motion or full-deck review requirements without cause.

See [quick revisions](QUICK_REVISIONS.md) for source binding, publishing, and same-file update rules. If the requested work changes the deck's organizing story or layout system rather than bounded objects, move to template adaptation or new composition and record the expanded scope.

## 2. Real template adaptation

The Gaojie page/component collection has 31 persistent category guides（本地资源，未随公开仓库提供） and the [gaojie-layouts skill](../../.agents/skills/gaojie-layouts/SKILL.md). Use `library-layout-search --category '三段内容' --query 'current page message and evidence relationship' --json` or `library-style-search --collection gaojie` to retrieve a concrete source page with measured groups and actual sample observations. It supplements whole-deck selection. `library-style-inspect` and `library-style-image-inspect` read Gaojie sources too. `library-layout-build` supports a fully mapped single page or explicitly selected component groups; a gallery of header/text/metric alternatives is not a whole target slide. Set `target_page.layout_collection=gaojie` in page matching when this collection should supply a local layout, then use the existing reviewed hybrid workflow. Native chart/table/SmartArt data needs a current-data rebuild; a text-only map cannot adopt old internal data. After inspecting an actual adaptation, `library-layout-learn` records source/output/preview identities, the specific reused relationship and its limits for future retrieval.

The small page reference set is REUSABLE-LAYOUTS.json（本地资源，未随公开仓库提供）. It distinguishes `slot_experiment` (technical replacement evidence) from `design_reference` (an actual source-page layout judgment). `library-style-search --layout-examples --query '职业规划 实践 三张图片' --json` searches these pages directly; ordinary whole-template search also attaches matching source-SHA entries as `layout_examples`. Design entries include current source-page images, topic fit, relationships, image roles, strengths and limitations. They do not imply editable slots or a finished adaptation. User-rejected design examples are omitted from direct page recommendations; older attached records retain the negative judgment for context. Read current `design_review`, not a historical render pass. Consult the actual whole page before using any entry; tested text capacity applies only to its identified copy and geometry.

Choose this path when a supplied/selected editable PPTX or a library source page is meant to carry its actual layout, object relationships, theme/master, or page composition into the result. A visual reference, matching colors, or extracted media alone is not template application.

1. Establish audience, occasion, required page count, page messages, evidence, and preservation scope. Determine which parts of the source are inherited and which are replaced or adjusted.
2. Search by occasion and page function, then inspect real pages at usable scale. For a whole-deck choice, inspect the cover and substantive content page(s), and consider neighboring-page logic. When the user named the exact template, that instruction is the selection.
3. Bind the current source file/hash and selected pages. Map every inherited visible object that contains old text, facts, identity, or media to current material, a deliberate deletion, or a reasoned retain decision. Inspect images in slide/layout/master layers before replacing them. Identify unsupported content, animation, groups, or relationships before building.
4. Bind the reviewed source and mapping to the current job with `library-hybrid-bind`, then run the same job through its normal workflow. The job-bound TemplateAdapter uses the source-preserving engine; normal preview generation, candidate promotion, expression review, and final gates still apply. If no hybrid binding is present, the existing Automizer route remains in effect.
5. Keep all current replacement, placeholder, and unmapped-content issues explicit until resolved. Inspect the revised whole pages and their neighbors; check that source styling, image meaning, and current content agree.
6. Record only the validation required by the actual output and risk. The selection/confirmation, template mapping, editable result, actual page review, and any native review are distinct evidence. Structural validation does not establish visual quality or native PowerPoint correctness.

The exact binding contract, allowed job states, mapping rules, receipts, and current library scope are in [template-first workflow](TEMPLATE_FIRST_WORKFLOW.md). Job-bound hybrid binding is available only for indexed curated families (currently contest and career-planning collections); do not generalize it to the whole template library.

### Judge content layouts before selecting a source

Start with what this page must let its audience understand, and the material that can support it. The folder name, an award label, a parsed page, replaceable objects, or a clean render is not a design judgment. Inspect substantive source pages at readable scale; covers and chapter transitions cannot stand in for a complex body page. The current small inspected selection and explicit exclusions are in 选页判断（本地资源，未随公开仓库提供）; it is not a ranking of the entire library.

- **Topic and purpose:** a career page may connect practice, personal actions and job requirements; a venture page may explain a product mechanism, evidence of impact, or team responsibilities; a government page may organize policy, implementation and results. Choose the specific content relationship before choosing colors or decorative style.
- **Appropriate richness:** dense content can work when the claim, subgroups and evidence have a clear hierarchy. Do not treat minimalism as the universal standard, or confuse rich content with extra boxes, certificates and icons. A page's reading order and neighboring-page rhythm matter.
- **Two or three images:** assign an actual role to each—main scene plus details, two comparable states, one product plus an application, or different evidence for different claims. Size and crop follow those roles and the available photo proportions. Each image must explain something beyond filling a frame; a mood image is not evidence of results. Small assets should identify a concept or connect real steps.
- **Whole-page judgment:** state what the source does well and what must change: grouping, visual weight, readable text width, image-to-claim fit, contrast, crowding, excessive decoration, and whether the conclusion is supported. A useful structure can coexist with weak styling or weak argument; borrow only the justified part. Inspect the new whole page after adaptation, not just its individual objects.
- **Separate technical decisions:** an image-only award case can teach layout but requires reconstruction as editable objects. A motion template can teach a reveal or transition without being a good substantive content page. Source selection, editable adaptation, static review and actual playback are different results. Do not turn a source-page review into an automatic quality score or approval.

## 3. New composition

Choose this path when current material is organized into a new story and there is no source template whose structure must be inherited. Reusing one or more studied layouts as references does not change the path; explicitly binding a real source template does.

1. For a new production task, create one minimal Work using the supported project entry. For follow-up work, reuse the current Work. Read `workspace/runtime-environment.json` and use its verified runtime via the Work wrapper.
2. Begin with audience, occasion, page messages, evidence and neighboring-page logic. Preserve source facts, required names, units, comparison bases, citations and requested content. Separate factual evidence from illustrative imagery.
3. Select an occasion- and audience-appropriate style profile. Apply the content-layout judgment above when searching source pages, including useful image-only case references. Inspect actual pages before using them. Bind library media only when its meaning, rights disposition and visible use are clear. Search results and machine indexes are discovery evidence, not visual review.
4. Build editable text and shapes. Keep presenter-facing explanation in speaker notes and all provenance outside notes. Avoid adding decorative numbering or English labels without a communicative purpose.
5. Inspect real page images at presentation scale, including the complete slide composition and narrative neighbors. Revise expression defects before the relevant prototype/final review. Use structural, OfficeCLI, native PowerPoint, playback, or other specialized checks only where the request, output type, or observed risk requires them.

For exact source discovery and media contracts, use [template-first workflow](TEMPLATE_FIRST_WORKFLOW.md); for authoring principles and presentation-form guidance, use [production knowledge base](playbook/ppt-production-knowledge-base.md), [style profiles](STYLE_PROFILES.md), and [presentation quality](PRESENTATION_QUALITY.md).

### Two-level reference workflow

For a new deck or substantial redesign, a label such as `business_report` is only the starting point. Establish the specific reporting purpose, audience need, main conclusion, argument order and evidence limits. A report about a proposed costing method and trial preparation must not invent completed operational results; public cases support the reasoning and do not replace the user's own work status. Set the story before choosing diagrams or expanding to a target page count.

Use two separate reference decisions:

1. **Whole-deck reporting logic.** Search the relevant occasion and industry, then read actual agendas and substantive pages. Compare how the report introduces scope, evidence, problems, actions and next steps. Reject a same-industry example with a different purpose, such as a personal appraisal used for a departmental costing report. Do not borrow facts or force an annual-review narrative onto a method proposal.
2. **Page expression.** For each important page, identify the exact relationship, available evidence and information units. Search the relevant Gaojie category and inspect real images. Distinguish progression from allocation, comparison from independent definitions, and component galleries from full-page layouts. Record what spatial grouping, relative emphasis, proportions, text/image roles and reading order are worth borrowing, plus what must change for current content. A generic label such as "use a pie chart" is not source-specific design reasoning. A page can be composed without a donor when that is the better choice.

New minimal Works persist the current shared entrypoints in `workspace/authoring-context.json` before runtime discovery. This survives a new chat and is independent of all historical examples. Each task makes its own content and design choices. The initial request hash identifies startup context; current requirements are independently bound by Studio.

For versioned new Works, complete the following assistant planning inside the existing lifecycle:

1. After the brief is established, call `design-plan <job> --json`. It creates/reads the current plan, exposes missing decisions and the two-level reference context, and returns a request skeleton for every current page.
2. Author `narrative_intent`, `whole_deck_reference_decision`, and `page_design` for this task. Use current facts and inspect relevant real source pages. Record them with `design-plan <job> --request <author-plan.json> --json`. This is not a form for the user or a new user approval.
3. Native and template/other full-build routes check this recorded plan before generating. A missing plan asks the assistant to complete it. `native-scene` binds the authored objects to that plan; a changed plan requires rebinding. Existing whole-page expression review still judges actual images and neighboring narrative.

A compact teaching example illustrates the schema; real requests include every current page ID, with relationships that fit that content:

```json
{
  "narrative_intent": {
    "task_specific_type": "面向初学者的原理教学",
    "audience_need": "从可观察的现象理解过程，并能解释一个生活例子",
    "core_message": "通过现象与步骤理解这一过程的形成条件",
    "storyline": "观察现象→示例解释→练习→回顾",
    "evidence_boundary": "使用已提供的教材与实验资料，不添加未经验证的结论"
  },
  "whole_deck_reference_decision": {
    "mode": "new_composition",
    "reason": "本课按已有教学目标与活动安排组织，不强套经营汇报主线"
  },
  "page_design": {
    "slide-01": {
      "page_job": "先让学习者描述观察到的变化，再解释原因",
      "content_relationship": "同一对象的前后状态与变化条件相对应",
      "layout_categories": ["二段内容", "图文排版"],
      "reference_decision": {
        "mode": "new_composition",
        "reason": "当前两张实验图片需要保持同一观察尺度，以前后对应自建布局"
      }
    }
  }
}
```

`reference_decision.mode` is `new_composition`, `preserve_existing`, or `reference`. Every choice needs a specific `reason`. Preserving an existing structure requires an already bound template/edit target. For `reference`, provide library-relative `source_path`, `source_sha256`, one-based `source_slide` (or `source_pages` for the whole-deck choice), `borrowed_relationship`, actual `observation`, and the proposed content/geometry `adaptation`. Each item in `previews` contains workspace-contained `path`, `sha256`, `source_sha256`, and `source_slide`; the pictures must cover the declared source pages. Source/page/image identity is checked. These records establish what was referenced, not whether the assistant actually made a good visual judgment.

The optional whole-deck `query` refines the current topic and occasion. Nine presentation forms have localized discovery entries; topic terms come from the current brief or explicit query, not a fixed industry list. Known mismatches are rejected, compatible reporting families are distinguished, and unknown occasion tags remain explicitly unknown. Career-planning and venture competitions are separated when specified. No fixed narrative or layout is imposed by the broad form.

For native scenes, locate the selected reference's corresponding authored objects in `reference_applications` with `slide_id`, matching `source_path`/`source_slide`, and zero-based `target_element_indexes`. This locates objects for comparison; it does not establish visual quality or source inheritance. Do not claim source fidelity for a new composition.

`planning/design-context.json` is the current advisory context for discovery and comparison, while the design decisions remain in the existing deck plan. No category means no sweep of all 31 categories; at most two relevant categories are searched per page. No suitable donor or unavailable search is a valid reason to compose directly. Changing the request, brief, slide content, decisions, source file or selected source image makes the recorded decision stale. An identical current submission preserves rendered artifacts and review state; restoring the original valid inputs can resume a design-only pause. Older unversioned jobs are not rewritten by this command, and local edits/faithful reconstruction do not require whole-deck replanning.

The normal expression-review packet includes this context and its current/stale status. The assistant compares the chosen reference with the actual whole output page and neighboring narrative: did the specific relationship survive, do proportions and text capacity fit, and is the key message easier to understand? Finding an object index, passing a render or writing a reference map cannot answer these questions. This is assistant self-review, not an additional user confirmation.

## Delivery and Work state

Editable PPTX is the default deliverable. If the user accepts the image currently being viewed and requests the deck, identify and hand over the exact editable PPTX that produced that image immediately at the next safe file boundary. If needed, use the documented `pptx-save-as` operation to place that current file in Work `output`, replacing the existing destination only when the user asked for the current version there. Report if the image shows only one page or if the source deck cannot be tied to it. This handoff is not native PowerPoint acceptance, review of unseen pages, or Work completion.

New minimal Works retain materials, reproducible code/configuration, final artifact, and essential identity/QA evidence. Do not create a Work for Studio maintenance. Preparing a Work for review and finishing/archiving it are lifecycle steps, separate from delivering an already requested PPTX. Apply legacy closure instructions only to legacy Works whose format still requires them; do not run legacy narrative retrospectives for minimal Works. A user delivery request does not by itself accept unseen pages or authorize finishing the Work.
