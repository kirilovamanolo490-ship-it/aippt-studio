from __future__ import annotations

import html
import base64
import hashlib
import json
import re
import sys
from pathlib import Path

from .models import DeckPlan, PresentationForm, SlideSpec, SourceDeckManifest, SourceRecord
from .assets.acquisition import AssetLedger
from .storage import job_dir, load_job, read_model, write_json
from .visual_system import css_variable_block
from .style_profiles import style_profile, style_profile_css


# The explicit 13 1/3 × 7.5 inch export maps 1920 CSS px to 960 pt.
# Thus body 48 px = 24 pt; title 72 px = 36 pt; caption 36 px = 18 pt.
# These sizes are starting points, not an expression or aesthetic acceptance
# rule. This HTML author does not itself prove rendered clipping or overlap;
# downstream PPTX geometry checks and actual whole-page review remain required.
CSS = """
*{box-sizing:border-box}html,body{margin:0;background:var(--vs-canvas);font-family:'Microsoft YaHei','Source Han Sans CN','PingFang SC',Arial,sans-serif;color:var(--vs-ink)}
body{display:flex;flex-direction:column;align-items:center;gap:48px;padding:48px}
.slide{position:relative;width:1920px;height:1080px;overflow:visible;background:var(--vs-surface);flex:none}
.content{position:absolute;inset:var(--page-inset-y,96px) var(--page-inset-x,96px);padding-top:18px;display:flex;flex-direction:column;gap:var(--page-title-gap,52px);min-height:0}
h1,h2,h3,p,figure{margin:0}h1,h2{font-family:var(--page-heading-font,inherit);font-size:var(--page-title-size,72px);line-height:1.16;font-weight:700;color:var(--vs-heading);overflow-wrap:anywhere;flex:none}
h1{font-size:var(--page-cover-title-size,96px);line-height:1.12}.body{flex:1;min-height:0;position:relative}
.qa-text{overflow-wrap:anywhere;word-break:normal}.body-text{font-family:var(--page-body-font,inherit);font-size:var(--page-body-size,48px);line-height:var(--page-body-leading,1.42);color:var(--vs-ink)}.caption-text,[data-text-role="caption"]{font-family:var(--page-label-font,inherit);font-size:var(--page-caption-size,30px)}
.cover{background:var(--vs-cover)}.cover h1,.cover .body-text{color:var(--vs-cover-ink)}.cover .content{gap:var(--page-cover-gap,64px);justify-content:center}.cover .body{flex:0 1 auto;min-height:0}.cover .text-stack{gap:var(--page-item-gap,30px)}
.text-stack,.image-points,.icon-points{display:flex;flex-direction:column;gap:var(--page-item-gap,32px)}
.fact-list,.agenda-list,.takeaway-list,.sequence-list{display:flex;flex-direction:column;gap:0}
.fact-line{padding:28px 0;}.fact-line:first-child{padding-top:0}.fact-line:last-child{padding-bottom:0}
.agenda-list .fact-line{padding:30px 0}.takeaway-list{padding-left:48px}.takeaway-list .fact-line{}
.sequence-list{padding-left:42px}.sequence-list .fact-line{padding:20px 0}
.dual{display:grid;grid-template-columns:1fr 1fr;gap:var(--page-grid-gap,80px);height:100%;align-content:start;padding-top:24px}.dual-item{padding-top:0}.divider{height:6px;background:var(--vs-accent);margin-bottom:36px}
.editorial-columns{display:grid;grid-template-columns:repeat(var(--column-count),minmax(0,1fr));gap:var(--page-grid-gap,72px);height:100%;align-content:center}.editorial-columns .fact-line{border-top:4px solid var(--vs-accent);padding:28px 0 0}
.statement-focus{height:100%;display:flex;align-items:center;padding-left:52px}.statement-text{font-family:var(--page-body-font,inherit);font-size:var(--page-statement-size,var(--page-title-size,72px));line-height:1.3}
.image-layout,.icon-layout{display:grid;grid-template-columns:minmax(0,var(--page-image-left,1.08fr)) minmax(0,var(--page-image-right,1fr));gap:var(--page-grid-gap,48px);height:100%;min-height:0;align-items:stretch}.image-points,.icon-points{justify-content:center;min-height:0}
.image-layout.image-left,.icon-layout.image-left{grid-template-columns:minmax(0,var(--page-image-right,1fr)) minmax(0,var(--page-image-left,1.08fr))}.image-left .image-points,.image-left .icon-points{grid-column:2;grid-row:1}.image-left .image-visual,.image-left .icon-visual,.image-left .visual-stack{grid-column:1;grid-row:1}
.image-visual,.icon-visual{display:flex;align-items:center;justify-content:center;min-width:0;min-height:0;height:100%}.image-visual img{display:block;width:100%;height:100%;object-fit:contain;border-radius:0}.icon-visual img{display:block;max-width:320px;max-height:320px;width:100%;height:100%;object-fit:contain;border-radius:0}
.cinematic-still{background:var(--vs-cover)}.cinematic-still .content{inset:58px 72px;gap:30px;justify-content:normal}.cinematic-still h1,.cinematic-still h2{color:var(--vs-cover-ink);font-weight:600}.cinematic-still.cover .body{flex:1;min-height:0}.cinematic-composition{display:flex;flex-direction:column;gap:26px;height:100%;min-height:0}.cinematic-composition .image-visual{flex:1;min-height:0;background:#101114}.cinematic-composition .image-visual img{width:100%;height:100%;object-fit:contain;border-radius:0}.cinematic-claim{flex:none;color:var(--vs-cover-ink);padding:0 8px 8px}
.composition-competition-photo-cover .content{inset:72px 84px;padding-top:0;display:grid;grid-template-columns:minmax(0,.9fr) minmax(0,1.1fr);align-items:stretch;gap:56px}
.portfolio-cover-copy{display:flex;flex-direction:column;justify-content:center;gap:44px;min-width:0}.portfolio-cover-details{display:flex;flex-direction:column;gap:22px;min-width:0}
.composition-competition-photo-cover .body,.portfolio-cover-art,.portfolio-cover-art .image-visual{min-width:0;min-height:0;width:100%;height:100%;flex:initial}.portfolio-cover-art .image-visual img{object-fit:contain;border-radius:0}
.visual-stack{display:grid;grid-template-rows:repeat(var(--asset-count),minmax(240px,1fr));gap:var(--page-item-gap,22px);min-height:0;height:100%}
.conclusion{background:var(--vs-conclusion)}
/* Presentation forms change composition and density, not a universal palette.
   Explicit style_direction and real templates remain the authority for visual
   identity; these hooks keep different occasions from receiving the same
   generic page grammar. */
.presentation-form-government_briefing .fact-line{padding:24px 0}.presentation-form-government_briefing .body-text{line-height:var(--page-body-leading,1.46)}
.presentation-form-business_report .dual{gap:var(--page-grid-gap,52px)}.presentation-form-business_report .fact-line{padding:24px 0}
.presentation-form-competition_defense .sequence-list{padding-left:0}.presentation-form-competition_defense .sequence-list .fact-line{border-top:2px solid var(--vs-line);padding:24px 0}
.presentation-form-academic_defense .fact-line{padding:22px 0}
.presentation-form-teaching .body-text{line-height:var(--page-body-leading,1.48)}
.presentation-form-technical_explanation .sequence-list{padding-left:0}.presentation-form-technical_explanation .sequence-list .fact-line{border-left:6px solid var(--vs-accent);padding:18px 0 18px 28px}
.presentation-form-ceremonial_story .cover .content{justify-content:flex-end;padding-bottom:132px}
.presentation-form-premium_brand_portfolio .image-points{gap:var(--page-item-gap,22px)}
/* Page roles are emitted by the planner and give each presentation form a
   different narrative grammar.  They intentionally alter relationships,
   density and image priority instead of assigning a canned colour scheme. */
.briefing-overview-list,.decision-overview-list,.argument-map-list,.research-map-list,.learning-map-list,.mechanism-map-list,.story-map-list,.portfolio-map-list{display:grid;gap:26px;min-height:0}
.briefing-overview-list{grid-template-columns:repeat(2,minmax(0,1fr));border-top:4px solid var(--vs-accent)}.briefing-overview-list .fact-line{padding:26px 30px;border-bottom:1px solid var(--vs-line)}
.decision-overview-list{grid-template-columns:repeat(3,minmax(0,1fr));align-items:stretch}.decision-overview-list .fact-line{padding:30px;border-top:6px solid var(--vs-accent);background:var(--vs-accent-soft)}
.argument-map-list{grid-template-columns:minmax(0,.8fr) minmax(0,1.2fr);border-left:8px solid var(--vs-accent);padding-left:34px}.argument-map-list .fact-line{padding:24px 0;border-bottom:2px solid var(--vs-line)}
.research-map-list{grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.research-map-list .fact-line{padding:28px;border:1px solid var(--vs-line)}
.learning-map-list{grid-template-columns:repeat(2,minmax(0,1fr));gap:22px}.learning-map-list .fact-line{padding:26px 28px;background:var(--vs-accent-soft)}
.mechanism-map-list{grid-template-columns:minmax(0,1fr) minmax(0,1.35fr);gap:36px}.mechanism-map-list .fact-line{padding:26px;border-top:4px solid var(--vs-accent)}
.story-map-list{grid-template-columns:1fr;gap:12px}.story-map-list .fact-line{padding:28px 0;border-bottom:1px solid var(--vs-line)}
.portfolio-map-list{grid-template-columns:1.35fr .65fr;gap:42px}.portfolio-map-list .fact-line{padding:34px 0;border-top:2px solid var(--vs-heading)}.portfolio-map-list .fact-line:first-child{font-weight:700}
.briefing-fact-list,.decision-fact-list,.argument-fact-list,.research-fact-list,.learning-fact-list,.mechanism-fact-list,.story-fact-list,.portfolio-fact-list{display:flex;flex-direction:column;min-height:0}
.briefing-fact-list .fact-line{padding:26px 0;border-bottom:1px solid var(--vs-line)}.decision-fact-list .fact-line{padding:26px 28px;border-left:6px solid var(--vs-accent);margin-bottom:14px;background:var(--vs-accent-soft)}.argument-fact-list .fact-line{padding:24px 0;border-top:2px solid var(--vs-line)}.research-fact-list .fact-line{padding:24px 0;border-bottom:1px solid var(--vs-line)}.learning-fact-list .fact-line{padding:26px 28px;margin-bottom:16px;background:var(--vs-accent-soft)}.mechanism-fact-list .fact-line{padding:20px 0 20px 30px;border-left:6px solid var(--vs-accent)}.story-fact-list .fact-line{padding:28px 0;border-bottom:1px solid var(--vs-line)}.portfolio-fact-list .fact-line{padding:34px 0;border-top:2px solid var(--vs-heading)}
.page-role-portfolio-case .image-visual img,.page-role-portfolio-evidence .image-visual img{object-position:center}.page-role-argument-evidence .image-layout{grid-template-columns:.9fr 1.1fr}.page-role-briefing-evidence .image-points{justify-content:flex-start}
"""


def _image_uri(path: Path) -> str:
    # Local SVG URLs can render in Chrome yet disappear during dom-to-pptx
    # conversion. A self-contained SVG is reliable in both renderers.
    if path.suffix.lower() == ".svg":
        return "data:image/svg+xml;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    return path.as_uri()


def _asset_info(root: Path, source_id: str) -> tuple[str, str] | None:
    if source_id.startswith("library:"):
        asset_id = source_id.split(":", 1)[1]
        ledger_path = root / "assets" / "library" / "ledger.json"
        if not asset_id or not ledger_path.is_file():
            return None
        from .library_assets import LibraryAssetLedger
        ledger = read_model(ledger_path, LibraryAssetLedger)
        asset = next((item for item in ledger.assets if item.id == asset_id), None)
        if asset is None:
            return None
        stored = (root / asset.stored_path).resolve()
        if (root.resolve() not in stored.parents or not stored.is_file()
                or stored.stat().st_size > 20 * 1024 * 1024):
            return None
        digest = hashlib.sha256()
        with stored.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return (_image_uri(stored), "image") if digest.hexdigest() == asset.media_sha256 else None
    if source_id.startswith("acquired:"):
        asset_id = source_id.split(":", 1)[1]
        ledger_path = root / "assets" / "asset-ledger.json"
        if not ledger_path.is_file():
            return None
        ledger = read_model(ledger_path, AssetLedger)
        asset = next((item for item in ledger.assets if item.id == asset_id), None)
        if asset is None:
            return None
        stored = (root / asset.stored_path).resolve()
        return (_image_uri(stored), asset.kind) if root.resolve() in stored.parents and stored.is_file() else None
    if source_id.startswith("extracted:"):
        _, deck_source_id, asset_id = source_id.split(":", 2)
        manifest_path = root / "extraction" / deck_source_id / "source-deck-manifest.json"
        if not manifest_path.is_file():
            return None
        manifest = read_model(manifest_path, SourceDeckManifest)
        asset = next((item for item in manifest.assets if item.id == asset_id), None)
        if not asset or asset.extraction_status != "extracted" or not asset.stored_path:
            return None
        stored = (root / asset.stored_path).resolve()
        return (_image_uri(stored), "image") if root.resolve() in stored.parents and stored.is_file() else None
    path = root / "normalized" / "sources" / f"{source_id}.json"
    if not path.is_file():
        return None
    record = read_model(path, SourceRecord)
    stored = (root / record.stored_path).resolve()
    return (_image_uri(stored), "image") if root.resolve() in stored.parents and stored.is_file() and record.kind == "image" else None


def _asset_uri(root: Path, source_id: str) -> str | None:
    info = _asset_info(root, source_id)
    return info[0] if info else None


def _overview_list_class(presentation_form: PresentationForm) -> str:
    return {
        PresentationForm.GOVERNMENT_BRIEFING: "briefing-overview-list",
        PresentationForm.BUSINESS_REPORT: "decision-overview-list",
        PresentationForm.COMPETITION_DEFENSE: "argument-map-list",
        PresentationForm.ACADEMIC_DEFENSE: "research-map-list",
        PresentationForm.TEACHING: "learning-map-list",
        PresentationForm.TECHNICAL_EXPLANATION: "mechanism-map-list",
        PresentationForm.CEREMONIAL_STORY: "story-map-list",
        PresentationForm.PREMIUM_BRAND_PORTFOLIO: "portfolio-map-list",
    }.get(presentation_form, "agenda-list")


def _form_fact_list_class(presentation_form: PresentationForm) -> str:
    return {
        PresentationForm.GOVERNMENT_BRIEFING: "briefing-fact-list",
        PresentationForm.BUSINESS_REPORT: "decision-fact-list",
        PresentationForm.COMPETITION_DEFENSE: "argument-fact-list",
        PresentationForm.ACADEMIC_DEFENSE: "research-fact-list",
        PresentationForm.TEACHING: "learning-fact-list",
        PresentationForm.TECHNICAL_EXPLANATION: "mechanism-fact-list",
        PresentationForm.CEREMONIAL_STORY: "story-fact-list",
        PresentationForm.PREMIUM_BRAND_PORTFOLIO: "portfolio-fact-list",
    }.get(presentation_form, "fact-list")


def _same_visible_message(title: str, point: str) -> bool:
    """Return whether a title already says this source point in full.

    The planner retains the full point for provenance and speaker notes.  The
    renderer should not print the exact same sentence again beneath the title:
    that creates a big headline plus redundant body line without adding an
    evidence or explanation relationship.
    """

    def normalize(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip(" \t\r\n。！？!?；;，,：:")

    normalized_title, normalized_point = normalize(title), normalize(point)
    return bool(normalized_title and normalized_point and normalized_title == normalized_point)


def render_slide(
    root: Path,
    slide: SlideSpec,
    index: int,
    total: int,
    *,
    deck_title: str = "",
    presentation_form: PresentationForm = PresentationForm.GENERAL,
    expression_pattern: str = "auto",
    composition_recipe: str = "",
    composition_source: str = "",
) -> str:
    """Render a source-faithful default layout, leaving final fit to the browser.

    Source locators and planning instructions belong in the evidence ledger, not
    decorative microcopy. Every point and asset remains represented; the browser
    rejects overfull layouts instead of truncating text or reducing its size.
    """
    title = html.escape(slide.title)
    raw_points = list(slide.points)
    title_repeats_first_point = bool(raw_points and _same_visible_message(slide.title, raw_points[0]))
    visible_points = raw_points[1:] if title_repeats_first_point else raw_points
    points = [html.escape(point) for point in visible_points]
    point_blocks = "".join(f'<div class="body-text qa-text" data-text-role="body">{point}</div>' for point in points)
    # Purpose, revision notes and style are authoring metadata. They neither
    # become visible copy nor imply a contrast/sequence absent from the content.
    role = f'{slide.title} {" ".join(raw_points)}'
    assets = [_asset_info(root, ref) for ref in slide.asset_refs]
    missing_assets = sum(asset is None for asset in assets)
    resolved = [(ref, asset) for ref, asset in zip(slide.asset_refs, assets, strict=True) if asset is not None]
    cinematic_selected = (expression_pattern == "cinematic_still" and len(resolved) == 1
                          and resolved[0][1][1] not in {"icon", "logo"} and len(points) <= 1
                          and slide.layout in {"cover", "content", "evidence"})
    portfolio_cover_selected = (
        composition_recipe == "competition_photo_cover"
        and presentation_form == PresentationForm.COMPETITION_DEFENSE
        and expression_pattern == "cover" and slide.layout == "cover"
        and len(resolved) == 1 and len(slide.asset_refs) == 1
        and resolved[0][1][1] not in {"icon", "logo"} and len(points) <= 2
    )
    if set(slide.asset_geometry) - set(slide.asset_refs):
        raise ValueError("asset_geometry must name an exact asset_ref on this slide")
    title_tag = "h1" if slide.layout == "cover" else "h2"
    heading = f'<{title_tag} class="qa-text" data-text-role="title" data-layout-zone="title">{title}</{title_tag}>'
    if resolved:
        icon_only = len(resolved) == 1 and resolved[0][1][1] in {"icon", "logo"}
        layout_class, visual_class = ("icon-layout", "icon-visual") if icon_only else ("image-layout", "image-visual")
        pictures = ""
        for i, (ref, (uri, _)) in enumerate(resolved):
            geometry = slide.asset_geometry.get(ref, {"fit": "contain", "mask": "rect"})
            # GIF and other legacy formats retain their existing contained HTML
            # rendering; explicit native geometry requires PNG/JPEG/SVG input.
            supported = uri.startswith("data:image/svg+xml;") or Path(uri).suffix.lower() in {".png", ".jpg", ".jpeg", ".svg"}
            if ref in slide.asset_geometry and not supported:
                raise ValueError("asset_geometry supports PNG, JPEG and SVG sources")
            contract = f' data-image-geometry="{html.escape(json.dumps(geometry, ensure_ascii=False), quote=True)}"' if supported else ""
            pictures += f'<figure class="{visual_class}" data-layout-zone="visual-{i}"><img src="{html.escape(uri, quote=True)}"{contract} alt="" /></figure>'
        if len(resolved) > 1:
            pictures = f'<div class="visual-stack" style="--asset-count:{len(resolved)}">{pictures}</div>'
        if portfolio_cover_selected:
            heading = (f'<div class="portfolio-cover-copy" data-layout-zone="copy">{heading}'
                       f'<div class="portfolio-cover-details">{point_blocks}</div></div>')
            body = f'<div class="portfolio-cover-art">{pictures}</div>'
        elif cinematic_selected:
            claim = f'<div class="body-text qa-text cinematic-claim" data-text-role="body" data-layout-zone="claim">{points[0]}</div>' if points else ""
            body = f'<div class="cinematic-composition">{pictures}{claim}</div>'
        else:
            if expression_pattern == "image_left":
                layout_class += " image-left"
            body = f'<div class="{layout_class}"><div class="{"icon-points" if icon_only else "image-points"}" data-layout-zone="body">{point_blocks}</div>{pictures}</div>'
    elif not points:
        body = ""
    elif slide.layout == "summary":
        layout_class = _overview_list_class(presentation_form)
        rows = "".join(f'<div class="fact-line"><div class="body-text qa-text" data-text-role="body">{point}</div></div>' for point in points)
        body = f'<div class="{layout_class}" data-layout-zone="body">{rows}</div>'
    elif slide.layout == "conclusion":
        layout_class = "takeaway-list" if presentation_form == PresentationForm.GENERAL else _form_fact_list_class(presentation_form)
        rows = "".join(f'<div class="fact-line"><div class="body-text qa-text" data-text-role="body">{point}</div></div>' for point in points)
        body = f'<div class="{layout_class}" data-layout-zone="body">{rows}</div>'
    elif slide.layout == "cover":
        body = f'<div class="text-stack" data-layout-zone="body">{point_blocks}</div>'
    elif expression_pattern == "statement" and len(points) == 1:
        body = f'<div class="statement-focus" data-layout-zone="body"><div class="statement-text qa-text" data-text-role="body">{points[0]}</div></div>'
    elif expression_pattern == "comparison" and len(points) == 2:
        items = "".join(f'<div class="dual-item" data-layout-zone="comparison-{i}"><div class="divider" aria-hidden="true"></div><div class="body-text qa-text" data-text-role="body">{point}</div></div>' for i, point in enumerate(points))
        body = f'<div class="dual">{items}</div>'
    elif expression_pattern == "sequence" and len(points) >= 2:
        rows = "".join(f'<div class="fact-line"><div class="body-text qa-text" data-text-role="body">{point}</div></div>' for point in points)
        body = f'<div class="sequence-list" data-layout-zone="body">{rows}</div>'
    elif expression_pattern == "editorial_columns" and len(points) in {2, 3}:
        rows = "".join(f'<div class="fact-line"><div class="body-text qa-text" data-text-role="body">{point}</div></div>' for point in points)
        body = f'<div class="editorial-columns" style="--column-count:{len(points)}" data-layout-zone="body">{rows}</div>'
    elif presentation_form != PresentationForm.GENERAL and slide.layout in {"content", "evidence"}:
        rows = "".join(f'<div class="fact-line"><div class="body-text qa-text" data-text-role="body">{point}</div></div>' for point in points)
        body = f'<div class="{_form_fact_list_class(presentation_form)}" data-layout-zone="body">{rows}</div>'
    elif len(points) == 1 and not title_repeats_first_point:
        body = f'<div class="statement-focus" data-layout-zone="body"><div class="statement-text qa-text" data-text-role="body">{points[0]}</div></div>'
    elif len(points) == 2 and re.search(r"对比|比较|差异|前后|两种|两类|\bvs\b", role, re.I):
        items = "".join(f'<div class="dual-item" data-layout-zone="comparison-{i}"><div class="divider" aria-hidden="true"></div><div class="body-text qa-text" data-text-role="body">{point}</div></div>' for i, point in enumerate(points))
        body = f'<div class="dual">{items}</div>'
    else:
        is_sequence = bool(re.search(r"步骤|流程|顺序|阶段|先.{0,24}(?:再|后)", role + " ".join(slide.points)))
        layout_class = "sequence-list" if is_sequence else "fact-list"
        rows = "".join(f'<div class="fact-line"><div class="body-text qa-text" data-text-role="body">{point}</div></div>' for point in points)
        body = f'<div class="{layout_class}" data-layout-zone="body">{rows}</div>'
    form_class = f"presentation-form-{presentation_form.value}"
    page_role_class = f" page-role-{slide.page_role}" if slide.page_role else ""
    slide_class = (f"slide {form_class} {slide.layout}{page_role_class}"
                   f"{' cinematic-still' if cinematic_selected else ''}"
                   f"{' composition-competition-photo-cover' if portfolio_cover_selected else ''}")
    page_role = html.escape(slide.page_role, quote=True)
    recipe_id = "competition_photo_cover" if portfolio_cover_selected else ""
    source_id = composition_source if portfolio_cover_selected else ""
    return f'<section class="{slide_class}" data-slide-id="{html.escape(slide.id, quote=True)}" data-presentation-form="{presentation_form.value}" data-page-role="{page_role}" data-expression-pattern="{html.escape(expression_pattern, quote=True)}" data-composition-recipe="{recipe_id}" data-composition-source="{html.escape(source_id, quote=True)}" data-authoring-policy="presentation-expression-v3" data-unresolved-assets="{missing_assets}"><div class="content">{heading}<div class="body">{body}</div></div></section>'


def write_deck_html(job_id: str, *, prototype: bool = False, dirty_only: set[str] | None = None) -> Path:
    root = job_dir(job_id)
    recipe_receipt = root / "build" / "portfolio-recipe-resolution.json"
    receipt_in_progress = {"schema_version": 1, "stage": "html_generation",
                           "status": "in_progress", "pptx_export_verified": False,
                           "applied_slide_ids": []}
    # A failed re-render must not leave a previous successful recipe receipt
    # looking current, even if loading the job now fails. Avoid creating a
    # phantom job directory when the requested job does not exist.
    if recipe_receipt.is_file():
        write_json(recipe_receipt, receipt_in_progress)
    job = load_job(job_id)
    if not recipe_receipt.is_file():
        write_json(recipe_receipt, receipt_in_progress)
    plan = read_model(root / "planning" / "deck-plan.json", DeckPlan)
    intent_payload = plan.planning_strategy.get("presentation_intent", {})
    try:
        presentation_form = PresentationForm(intent_payload.get("presentation_form", PresentationForm.GENERAL.value))
    except (TypeError, ValueError):
        presentation_form = PresentationForm.GENERAL
    font = json.dumps(plan.font_family, ensure_ascii=False).replace("<", r"\3c ").replace(">", r"\3e ")
    visual_contract = plan.planning_strategy.get("visual_system", {})
    profile = plan.planning_strategy.get("visual_style_profile")
    if not isinstance(profile, dict) or not profile.get("profile_id"):
        profile = style_profile(presentation_form)
    from .config import WORKSPACE_ROOT
    from .template_designs import resolve_planned_template_profile
    profile, template_design_status = resolve_planned_template_profile(
        WORKSPACE_ROOT, plan.planning_strategy, profile,
    )
    design_packet = plan.planning_strategy.get("template_design_language", {})
    planned_design = design_packet.get("selected") if isinstance(design_packet, dict) else None
    platform_default_font = "PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei"
    explicit_plan_font = plan.font_family != platform_default_font
    profile_css = (
        style_profile_css(profile, include_typography=not explicit_plan_font)
        if profile.get("application") == "baseline_new_composition" else ""
    )
    expression_plan = plan.planning_strategy.get("expression_plan", {})
    expression_slides = (
        expression_plan.get("slides", {})
        if isinstance(expression_plan, dict) and expression_plan.get("application") == "baseline_new_composition"
        and profile.get("application") == "baseline_new_composition"
        else {}
    )
    portfolio_metadata = plan.planning_strategy.get("portfolio_expression_examples", {})
    planned_recipes = portfolio_metadata.get("slide_recipes", {}) if isinstance(portfolio_metadata, dict) else {}
    planned_recipes = planned_recipes if expression_slides and isinstance(planned_recipes, dict) else {}
    effective_recipes: dict[str, dict] = {}
    recipe_skipped: list[dict] = []
    if planned_recipes:
        # A planning-time citation is not a permanent render license. Recheck
        # the catalog and exact source bytes before applying its geometry.
        from .portfolio_examples import select_portfolio_examples
        current = select_portfolio_examples(WORKSPACE_ROOT, expression_plan)
        if current["slide_recipes"] == planned_recipes:
            effective_recipes = current["slide_recipes"]
        recipe_skipped = current["skipped"]
    css = (css_variable_block(visual_contract) + CSS + profile_css).replace("font-family:", f"font-family:{font},", 1)
    visual_source = "unknown"
    if isinstance(visual_contract, dict) and isinstance(visual_contract.get("source"), str):
        visual_source = visual_contract["source"]
    slides = plan.slides
    if prototype:
        from .models import select_prototype_slides
        slides = select_prototype_slides(plan)
    total = len(plan.slides)
    slide_fragments: list[str] = []
    applied_recipe_slides: list[str] = []
    for slide in slides:
        index = plan.slides.index(slide) + 1
        fragment = render_slide(
            root,
            slide,
            index,
            total,
            deck_title=plan.title,
            presentation_form=presentation_form,
            expression_pattern=expression_slides.get(slide.id, {}).get("pattern", "auto"),
            composition_recipe=effective_recipes.get(slide.id, {}).get("recipe", ""),
            composition_source=effective_recipes.get(slide.id, {}).get("example_id", ""),
        )
        if (effective_recipes.get(slide.id, {}).get("recipe") == "competition_photo_cover"
                and 'data-composition-recipe="competition_photo_cover"' in fragment):
            applied_recipe_slides.append(slide.id)
        slide_path = root / "build" / "slides" / f"{slide.id}.html"
        if not prototype and (dirty_only is None or slide.id in dirty_only
                              or slide.id in planned_recipes or planned_design or not slide_path.is_file()):
            slide_path.write_text(f'<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head><body data-visual-system-source="{html.escape(visual_source, quote=True)}">{fragment}</body></html>', encoding="utf-8")
        slide_fragments.append(fragment)
    rendered_recipe_slides = set(effective_recipes).intersection(slide.id for slide in slides)
    not_applied = sorted(rendered_recipe_slides - set(applied_recipe_slides))
    recipe_status = (
        "not_requested" if not planned_recipes else
        "suppressed_stale_or_changed_source" if not effective_recipes else
        "no_recipe_in_render_scope" if not rendered_recipe_slides else
        "source_verified_but_slide_incompatible" if not_applied else
        "applied_to_rendered_slides"
    )
    name = "prototype.html" if prototype else "deck.html"
    output = root / "build" / name
    # This is an explicit execution-scope marker, not inferred from plan labels
    # by the browser auditor. It is present only when both selected systems
    # actually apply as the new-composition baseline.
    composition_baseline = (
        not prototype
        and isinstance(profile, dict)
        and profile.get("application") == "baseline_new_composition"
        and isinstance(expression_plan, dict)
        and expression_plan.get("application") == "baseline_new_composition"
    )
    baseline_attr = ' data-composition-baseline="new_composition"' if composition_baseline else ""
    output.write_text(f'<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head><body data-visual-system-source="{html.escape(visual_source, quote=True)}"{baseline_attr}>{"".join(slide_fragments)}</body></html>', encoding="utf-8")
    write_json(root / "build/template-design-resolution.json", {
        "schema_version": 1, "stage": "html_generation", "status": template_design_status,
        "planned_id": planned_design.get("id") if isinstance(planned_design, dict) else None,
        "effective_profile_id": profile.get("profile_id"),
        "source_facts_reused": False, "source_media_reused": False,
        "html_path": f"build/{name}",
        "html_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    })
    write_json(recipe_receipt, {
        "schema_version": 1, "stage": "html_generation", "status": recipe_status,
        "pptx_export_verified": False, "html_path": f"build/{name}",
        "html_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "rendered_slide_ids": [slide.id for slide in slides],
        "planned": planned_recipes, "source_verified": effective_recipes,
        "applied_slide_ids": applied_recipe_slides, "not_applied_slide_ids": not_applied,
        "skipped": recipe_skipped,
    })
    return output
