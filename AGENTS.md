# AI PPT Studio contributor guidance

This repository contains the public Studio source snapshot. Keep private client inputs, Work folders, credentials, third-party template libraries and local runtime binaries outside version control.

For presentation production, read `.agents/skills/aippt-studio/SKILL.md`. For changes to Studio itself, inspect its source and relevant contracts; do not create a presentation Work just to maintain the core.

Preserve the `aippt-studio/` and root `scripts/` layout. Use current host-native dependencies and keep tests relevant to the change. Do not borrow runtime environments from historical Work folders.

Content planning begins with the audience, occasion, page message, evidence and neighboring-page logic. Preserve source facts and the scope of requested edits. Speaker notes contain presenter-facing talk tracks only; source paths belong in external ledgers.

Deliver editable PPTX by default. Distinguish static previews, native PowerPoint display, animation playback, generated files and user acceptance. A test result does not prove visual quality or acceptance. Existing cases are demonstrations, not reusable factual content for a new task.

Record actual shared implementation changes once in `aippt-studio/docs/CORE_CHANGELOG.md`. Do not copy raw conversations or private incident records into this public repository.
