# AI PPT Studio

**A local workflow for editable PowerPoint production, revision and review.**

[中文](README.md) · [Real project examples](docs/SHOWCASE.md) · [Getting started](docs/GETTING_STARTED.md) · [Architecture](docs/ARCHITECTURE.md)

AI PPT Studio connects source materials, presentation planning, editable authoring, template adaptation and review. The assistant makes content and design decisions; the workbench executes operations and records file identity, scope and evidence.

## Selected portfolio examples

These pages come from existing portfolio files retained in the local reference library. They show two deliberate visual directions: **red-and-gold cultural storytelling** and **technology-blue service design**. They are rendered from the existing PDF exports, not newly generated mockups.

### Cultural heritage and jade

![Jade project: red-and-gold solution page](docs/assets/jade-07.png)

### Digital driving-school service platform

![Digital mobility services: curved service chain](docs/assets/mobility-03.png)

See the [showcase](docs/SHOWCASE.md) for four full-page examples and the [source manifest](docs/showcase-manifest.json) for source identities. These legacy portfolio pages are not presented as one-click output of this software release or verified operational claims. Original decks and third-party asset collections are not redistributed.

## Main capabilities

- Inspect, create, copy and selectively revise PPTX files.
- Bind local changes to source hashes and preserve non-target content.
- Plan the audience, central message, evidence and relationships between slides.
- Adapt actual editable templates while tracking theme, master and media relationships.
- Author through HTML-to-PPTX, native PptxGenJS scenes and template adapters.
- Review structure, image geometry, page expression and provenance separately.
- Keep each production task in a Work package with source material, build source, output and necessary QA.
- Provide a portable PowerPoint skill and machine-readable handoff schemas.

## Quick start on macOS

Requirements: Python 3.11+, `uv`, and Node.js 22.12+ for the Node toolchain.

```sh
git clone https://github.com/kirilovamanolo490-ship-it/aippt-studio.git
cd aippt-studio/aippt-studio
uv venv .venv-macos --python 3.13
UV_PROJECT_ENVIRONMENT=.venv-macos uv sync --frozen --extra dev
npm ci --ignore-scripts
cd ..
./aippt-studio/.venv-macos/bin/aippt --help
./aippt-studio/.venv-macos/bin/aippt pptx-new ./blank.pptx
./aippt-studio/.venv-macos/bin/aippt pptx-inspect ./blank.pptx --json
./aippt-studio/.venv-macos/bin/aippt pptx-save-as ./blank.pptx --output ./copy.pptx
```

Basic file operations do not open PowerPoint. Rendering, native PowerPoint automation, video and optional generation engines require their respective tools. The Python/npm setup does not install OfficeCLI, PowerPoint, a browser, upstream repositories or private template libraries. PowerShell 7.3+ is required for the shared wrapper scripts.

## Repository layout

`aippt-studio/src/aippt_studio/` contains the Python core; `aippt-studio/node/` contains the Node execution scripts; `aippt-studio/tests/` contains regression tests. Root `scripts/` provides Work and runtime helpers. `docs/` contains public guides and examples, and `.agents/skills/` contains assistant workflow entry points.

This is a public source snapshot of the current working tree. It includes core source and tests, not private client inputs, full historical Work folders, runtime binaries or third-party asset collections. See [release scope](docs/RELEASE_SCOPE.md) and [validation](docs/VALIDATION.md).

No general open-source license has been selected for original project code in this release. Third-party components retain their own licenses, and showcase images are not a freely reusable asset pack. See [NOTICE](NOTICE.md), [third-party notes](docs/THIRD_PARTY.md) and [contribution guidance](CONTRIBUTING.md).
