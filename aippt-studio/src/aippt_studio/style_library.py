"""Read-only structural style and content observations for PPTX-family files.

No slide rendering or image-pixel inspection is performed. Media payloads are
never read; all claims are bounded to source OOXML evidence.
"""
from __future__ import annotations
import hashlib
import posixpath
from pathlib import Path
from typing import Any
from zipfile import ZipFile
import xml.etree.ElementTree as ET

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"p": P, "a": A, "r": R}
SCHEMA = "aippt.style-library.v1"
PARSER_VERSION = "style-library-3"
XML_PART_LIMIT = 32_000_000
XML_TOTAL_LIMIT = 512_000_000
OBJECT_TAGS = {"sp", "pic", "grpSp", "graphicFrame", "cxnSp"}
EXTENSIONS = {".pptx", ".ppsx", ".pptm"}

class StyleLibraryError(ValueError):
    """Malformed, oversized, or unsupported OOXML source."""

def _q(ns: str, name: str) -> str:
    return f"{{{ns}}}{name}"

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

class _Reader:
    def __init__(self, archive: ZipFile, xml_part_limit: int | None = None):
        self.archive = archive
        self.infos = {i.filename: i for i in archive.infolist()}
        self.xml_bytes = 0
        self.xml_part_limit = XML_PART_LIMIT if xml_part_limit is None else xml_part_limit
        self.rels_cache: dict[str, dict[str, dict[str, Any]]] = {}
        self.theme_cache: dict[str, dict[str, Any]] = {}
    def xml(self, part: str, required: bool = True) -> ET.Element | None:
        info = self.infos.get(part)
        if info is None:
            if required:
                raise StyleLibraryError(f"missing_part:{part}")
            return None
        if info.file_size > self.xml_part_limit:
            raise StyleLibraryError(f"oversized_xml_part:{part}:{info.file_size}")
        self.xml_bytes += info.file_size
        if self.xml_bytes > XML_TOTAL_LIMIT:
            raise StyleLibraryError("xml_parts_total_over_limit")
        try:
            with self.archive.open(info) as stream:
                return ET.parse(stream).getroot()
        except ET.ParseError as exc:
            raise StyleLibraryError(f"invalid_xml:{part}:{exc}") from exc
    def rels(self, owner: str) -> dict[str, dict[str, Any]]:
        if owner in self.rels_cache:
            return self.rels_cache[owner]
        parent, leaf = posixpath.split(owner)
        root = self.xml(posixpath.join(parent, "_rels", leaf + ".rels"), False)
        out = {}
        if root is not None:
            for node in root.findall(_q(PKG, "Relationship")):
                rid = node.get("Id")
                if rid:
                    external = node.get("TargetMode") == "External"
                    out[rid] = {"id": rid, "type": node.get("Type", "").rsplit("/", 1)[-1],
                                "target": None if external else _target(owner, node.get("Target", "")),
                                "external": external}
        self.rels_cache[owner] = out
        return out

def _target(owner: str, target: str) -> str | None:
    if not target or ":" in target.split("/", 1)[0]:
        return None
    resolved = posixpath.normpath(posixpath.join("" if target.startswith("/") else posixpath.dirname(owner), target.lstrip("/")))
    return None if resolved == ".." or resolved.startswith("../") else resolved.lstrip("/")

def _int(node: ET.Element | None, attr: str) -> int | None:
    try:
        return int(node.get(attr, "")) if node is not None else None
    except ValueError:
        return None

def _geometry(el: ET.Element) -> dict[str, Any] | None:
    x = el.find("p:spPr/a:xfrm", NS)
    if x is None: x = el.find("p:xfrm", NS)
    if x is None: x = el.find("p:grpSpPr/a:xfrm", NS)
    if x is None: return None
    off, ext = x.find("a:off", NS), x.find("a:ext", NS)
    return {"x_emu": _int(off, "x"), "y_emu": _int(off, "y"),
            "width_emu": _int(ext, "cx"), "height_emu": _int(ext, "cy"),
            "rotation_60000deg": _int(x, "rot"), "flip_h": x.get("flipH"), "flip_v": x.get("flipV")}

def _color(fill: ET.Element | None) -> dict[str, Any] | None:
    if fill is None: return None
    c = next((n for n in list(fill) if _local(n.tag) in {"srgbClr","schemeClr","sysClr","prstClr","scrgbClr","hslClr"}), None)
    if c is None: return {"kind": "none_or_inherited"}
    return {"kind": _local(c.tag), **dict(c.attrib),
            "transforms": [{"name": _local(n.tag), **dict(n.attrib)} for n in list(c)]}

def _shape_style(el: ET.Element) -> dict[str, Any]:
    sp = el.find("p:spPr", NS)
    if sp is None: return {"fill": None, "line": None}
    fill = line = None
    for n in list(sp):
        tag = _local(n.tag)
        if tag in {"solidFill","gradFill","noFill","blipFill","pattFill","grpFill"}:
            fill = {"type": tag, "color": _color(n),
                    "gradient_stops": [{"position": _int(s, "pos"), "color": _color(s)} for s in n.findall("a:gs", NS)]}
        if tag == "ln":
            dash = n.find("a:prstDash", NS)
            line = {"width_emu": _int(n, "w"), "cap": n.get("cap"),
                    "dash": dash.get("val") if dash is not None else None,
                    "fill": next((_shape_style_fill(c) for c in list(n)
                                  if _local(c.tag) in {"solidFill","gradFill","noFill"}), None)}
    return {"fill": fill, "line": line}

def _shape_style_fill(n: ET.Element) -> dict[str, Any]:
    return {"type": _local(n.tag), "color": _color(n)}

def _text(el: ET.Element) -> tuple[str, list[dict], list[dict]]:
    texts, runs, paras = [], [], []
    for para in el.iter(_q(A, "p")):
        chunks = []
        for child in list(para):
            tag = _local(child.tag)
            if tag == "br": chunks.append("\n"); continue
            if tag not in {"r", "fld"}: continue
            value = "".join(t.text or "" for t in child.iter(_q(A, "t")))
            chunks.append(value)
            prop = child.find("a:rPr", NS)
            latin = prop.find("a:latin", NS) if prop is not None else None
            ea = prop.find("a:ea", NS) if prop is not None else None
            runs.append({"text": value, "font_size_pt": _int(prop,"sz")/100 if _int(prop,"sz") is not None else None,
                         "bold": prop.get("b") if prop is not None else None,
                         "italic": prop.get("i") if prop is not None else None,
                         "underline": prop.get("u") if prop is not None else None,
                         "latin_typeface": latin.get("typeface") if latin is not None else None,
                         "east_asian_typeface": ea.get("typeface") if ea is not None else None,
                         "color": _color(prop.find("a:solidFill", NS) if prop is not None else None)})
        value = "".join(chunks); texts.append(value)
        ppr = para.find("a:pPr", NS)
        paras.append({"text": value, "properties": dict(ppr.attrib) if ppr is not None else {}})
    return "\n".join(texts), runs, paras

def _id_name(el: ET.Element) -> tuple[str | None, str | None]:
    node = next((n for n in el.iter() if _local(n.tag) == "cNvPr"), None)
    return (node.get("id"), node.get("name")) if node is not None else (None, None)

def _objects(root: ET.Element, rels: dict[str, dict], infos: dict[str, Any]) -> list[dict]:
    tree = root.find("p:cSld/p:spTree", NS)
    result = []
    if tree is None: return result
    def walk(parent: ET.Element, group_path: list[dict]) -> None:
        for el in list(parent):
            typ = _local(el.tag)
            if typ not in OBJECT_TAGS: continue
            oid, name = _id_name(el)
            text, runs, paras = ("", [], []) if typ == "grpSp" else _text(el)
            ph = el.find("p:nvSpPr/p:nvPr/p:ph", NS)
            rec = {"order": len(result)+1, "id": oid, "name": name, "type": typ,
                   "group_path": group_path, "geometry": _geometry(el), "text": text,
                   "text_runs": runs, "paragraphs": paras, "shape_style": _shape_style(el),
                   "placeholder_type": ph.get("type") if ph is not None else None}
            image_fills = []
            if typ == "pic":
                own_fills = [el.find("p:blipFill", NS)]
            elif typ in {"sp", "cxnSp"}:
                own_fills = [el.find("p:spPr/a:blipFill", NS)]
            elif typ == "grpSp":
                own_fills = [el.find("p:grpSpPr/a:blipFill", NS)]
            else:
                own_fills = []
            for fill in (candidate for candidate in own_fills if candidate is not None):
                blip = fill.find("a:blip", NS)
                if blip is None:
                    continue
                rid = blip.get(_q(R, "embed")) or blip.get(_q(R, "link"))
                relation = rels.get(rid or "")
                info = infos.get(relation.get("target")) if relation else None
                image_fills.append({
                    "relationship_id": rid,
                    "relationship_type": relation.get("type") if relation else None,
                    "media_part": relation.get("target") if relation else None,
                    "external": relation.get("external") if relation else None,
                    "declared_bytes": info.file_size if info else None,
                    "media_bytes_read": False,
                })
            if image_fills:
                # Includes normal shapes and grouped shapes, not only p:pic.
                rec["image_fills"] = image_fills
            gd = el.find(".//a:graphicData", NS)
            if gd is not None:
                uri = gd.get("uri","")
                kind = "table" if uri.endswith("/table") else "smartart" if "diagram" in uri else "chart" if "chart" in uri else "graphic"
                rows = gd.findall(".//a:tr", NS)
                rec["graphic"] = {"kind": kind, "uri": uri,
                                  "rows": len(rows), "columns": max((len(r.findall("a:tc", NS)) for r in rows), default=0)}
                graphic_rels = []
                for node in el.iter():
                    for key, value in node.attrib.items():
                        if key.startswith("{" + R + "}"):
                            relation = rels.get(value)
                            if relation:
                                info = infos.get(relation.get("target"))
                                graphic_rels.append({"relationship_id": value,
                                    "relationship_type": relation["type"], "part": relation["target"],
                                    "declared_bytes": info.file_size if info else None,
                                    "media_bytes_read": False})
                rec["graphic"]["relationships"] = graphic_rels
            if typ == "pic":
                blip = el.find(".//a:blip", NS)
                rid = blip.get(_q(R,"embed")) or blip.get(_q(R,"link")) if blip is not None else None
                relation = rels.get(rid or "")
                info = infos.get(relation.get("target")) if relation else None
                linked = []
                for node in el.iter():
                    for key, value in node.attrib.items():
                        if key.startswith("{" + R + "}"):
                            rel = rels.get(value)
                            if rel:
                                part_info = infos.get(rel.get("target"))
                                linked.append({"relationship_id":value,"relationship_type":rel["type"],
                                    "media_part":rel["target"],"external":rel["external"],
                                    "declared_bytes":part_info.file_size if part_info else None,
                                    "media_bytes_read":False})
                rec["picture"] = {"relationship_id": rid, "relationship_type": relation.get("type") if relation else None,
                                  "media_part": relation.get("target") if relation else None,
                                  "external": relation.get("external") if relation else None,
                                  "declared_bytes": info.file_size if info else None, "media_bytes_read": False,
                                  "relationships": linked}
            result.append(rec)
            if typ == "grpSp": walk(el, group_path + [{"id": oid, "name": name}])
    walk(tree, [])
    return result


def _shared_layers(reader: _Reader, layout_parts: set[str], master_parts: set[str]) -> dict[str, dict]:
    """Parse each referenced layout/master object tree once per deck."""
    layers: dict[str, dict] = {}
    for kind, parts in (("slide_layout", layout_parts), ("slide_master", master_parts)):
        for part in sorted(parts):
            root = reader.xml(part, False)
            if root is None:
                layers[part] = {"kind": kind, "status": "unavailable", "objects": []}
                continue
            objects = _objects(root, reader.rels(part), reader.infos)
            layers[part] = {"kind": kind, "status": "parsed", "objects": objects,
                            "image_count": sum(len(obj.get("image_fills", [])) for obj in objects)}
    return layers

def _theme(reader: _Reader, part: str | None) -> dict:
    if part and part in reader.theme_cache:
        return reader.theme_cache[part]
    root = reader.xml(part, False) if part else None
    if root is None: return {"part": part, "status": "unavailable", "palette": {}, "fonts": {}}
    colors = root.find("a:themeElements/a:clrScheme", NS); palette = {}
    if colors is not None:
        palette = {_local(c.tag): _color(c) for c in list(colors)}
    scheme = root.find("a:themeElements/a:fontScheme", NS); fonts = {}
    if scheme is not None:
        for key in ("majorFont","minorFont"):
            node = scheme.find(f"a:{key}", NS)
            if node is not None:
                fonts[key] = {script: (node.find(f"a:{script}",NS).get("typeface") if node.find(f"a:{script}",NS) is not None else None)
                              for script in ("latin","ea","cs")}
                fonts[key]["script_fonts"] = {f.get("script"): f.get("typeface") for f in node.findall("a:font",NS)}
    result = {"part": part, "status": "parsed", "palette": palette, "fonts": fonts}
    if part:
        reader.theme_cache[part] = result
    return result

def _roles(content: list[str], order: int) -> list[dict]:
    text = "\n".join(content).casefold()
    rules = {"agenda": ("目录","提纲"), "background_problem": ("背景","现状","痛点","问题"),
             "goal_positioning": ("目标","定位","愿景","职业目标"),
             "method_approach": ("方案","路径","方法","策略"),
             "process_architecture": ("流程","规划","体系","成长计划"),
             "evidence_results": ("成果","实践","成效","数据","证书"),
             "team_organization": ("团队","成员","个人优势","自我认知"),
             "timeline_plan": ("计划","进度","阶段","实施"), "closing_outlook": ("展望","感谢")}
    out = [{"role": role, "basis": "literal_text_keyword_heuristic",
            "evidence": [word for word in terms if word.casefold() in text]}
           for role, terms in rules.items() if any(word.casefold() in text for word in terms)]
    if order == 1: out.insert(0, {"role":"cover", "basis":"presentation_order_heuristic", "evidence":["first_slide"]})
    return out

def inspect_style_deck(path: Path, relative_path: str, *, xml_part_limit: int | None = None) -> dict:
    """Inspect one PPTX/PPSX/PPTM without loading media or imposing a ZIP-size cap."""
    path = Path(path)
    digest = None
    try:
        xml_part_limit = XML_PART_LIMIT if xml_part_limit is None else xml_part_limit
        if type(xml_part_limit) is not int or not XML_PART_LIMIT <= xml_part_limit <= 64_000_000:
            raise StyleLibraryError("xml_part_limit_must_be_default_through_64MB")
        st = path.stat()
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS: raise StyleLibraryError("unsupported_file_type")
        digest = _sha256(path)
        with ZipFile(path) as archive:
            reader = _Reader(archive, xml_part_limit); pres = reader.xml("ppt/presentation.xml")
            prels = reader.rels("ppt/presentation.xml"); size_el = pres.find("p:sldSz", NS)
            size = {"width_emu":_int(size_el,"cx"), "height_emu":_int(size_el,"cy")}
            slides = []
            layout_parts: set[str] = set()
            master_parts: set[str] = set()
            for order, sid in enumerate(pres.findall("p:sldIdLst/p:sldId",NS),1):
                rel = prels.get(sid.get(_q(R,"id"),""),{}); part = rel.get("target")
                if rel.get("type") != "slide" or not part:
                    slides.append({"order":order,"part":part,"status":"unresolved_relationship","objects":[]}); continue
                root = reader.xml(part,False)
                if root is None:
                    slides.append({"order":order,"part":part,"status":"missing_slide_part","objects":[]}); continue
                slide_rels = reader.rels(part)
                lr = next((r for r in slide_rels.values() if r["type"]=="slideLayout"),None); layout = lr.get("target") if lr else None
                mr = next((r for r in reader.rels(layout).values() if r["type"]=="slideMaster"),None) if layout else None
                master = mr.get("target") if mr else None
                if layout: layout_parts.add(layout)
                if master: master_parts.add(master)
                tr = next((r for r in reader.rels(master).values() if r["type"]=="theme"),None) if master else None
                theme_part = tr.get("target") if tr else None; theme = _theme(reader,theme_part)
                objs = _objects(root,slide_rels,reader.infos); textobjs=[o for o in objs if o["text"].strip()]
                title = next((o for o in textobjs if o["placeholder_type"] in {"title","ctrTitle"}),None)
                if title is None and textobjs: title=max(textobjs,key=lambda o:max((x["font_size_pt"] or 0 for x in o["text_runs"]),default=0))
                content=[o["text"] for o in objs if o["text"].strip()]
                counts={}
                for o in objs:
                    k="text_shape" if o["type"]=="sp" and o["text"].strip() else o["type"]
                    counts[k]=counts.get(k,0)+1
                slides.append({"order":order,"part":part,"slide_id":sid.get("id"),
                    "hidden":sid.get("show") in {"0","false"},"status":"parsed",
                    "title":title["text"].splitlines()[0] if title else "",
                    "content_sequence":content,"objects":objs,
                    "composition":{"basis":"OOXML object types and explicit geometry","object_counts":counts,
                        "text_object_count":len(textobjs),"hierarchy":[{"id":o["id"],"name":o["name"],
                        "role":"title" if o is title else "text_or_visual","text":o["text"]} for o in objs]},
                    "role_evidence":_roles(content,order),
                    "style_context":{"slide_layout_part":layout,"slide_layout_status":"referenced" if layout in reader.infos else "unavailable",
                        "slide_master_part":master,"slide_master_status":"referenced" if master in reader.infos else "unavailable",
                        "theme":theme,"evidence_layers":{"slide":"explicit_object_properties_only",
                        "layout":"part_reference_and_parse_status","master":"part_reference_and_parse_status",
                        "theme":"parsed_palette_and_font_scheme"}}})
            shared_layers = _shared_layers(reader, layout_parts, master_parts)
        return {"schema":SCHEMA,"parser_version":PARSER_VERSION,"path":relative_path,
                "source_format":path.suffix.lower()[1:],
                "presentation_kind":"slideshow" if path.suffix.lower()==".ppsx" else "presentation",
                "macro_execution":"never",
                "compatibility_boundary":"OOXML-only inspection; VBA is not inspected or executed; PPSX/PPTM package features are not executed.",
                "source_sha256":digest,"source_bytes":st.st_size,"mtime_ns":st.st_mtime_ns,
                "status":"parsed","parse_status":"parsed","inspection":"ooxml_structure_only",
                "presentation_size":size,"slide_count":len(slides),"slides":slides,
                "shared_layers":shared_layers,
                "limits":{"xml_part_bytes":xml_part_limit,"xml_cumulative_bytes":XML_TOTAL_LIMIT,
                    "expanded_zip_total_cap":None,"media_payloads_read":False},
                "visual_reviewed":False,"image_meaning_understood":False,"errors":[]}
    except Exception as exc:
        try: st=path.stat(); n=st.st_size; mt=st.st_mtime_ns
        except OSError: n=mt=None
        return {"schema":SCHEMA,"parser_version":PARSER_VERSION,"path":relative_path,
                "source_format":path.suffix.lower().lstrip("."),
                "presentation_kind":"slideshow" if path.suffix.lower()==".ppsx" else "presentation",
                "macro_execution":"never","source_sha256":digest,
                "source_bytes":n,"mtime_ns":mt,"status":"unsupported","parse_status":"unsupported",
                "inspection":"ooxml_structure_only","slide_count":0,"slides":[],
                "visual_reviewed":False,"image_meaning_understood":False,
                "errors":[f"{type(exc).__name__}:{exc}"]}
