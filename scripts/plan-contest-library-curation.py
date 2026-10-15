#!/usr/bin/env python3
"""Create review-only CONTENT-CURATION candidates from contest collection sidecars.

This tool never edits the live CONTENT-CURATION.jsonl or CATALOG.jsonl. It verifies
all referenced source bytes and writes a proposal JSONL plus an audit JSON.
"""
from __future__ import annotations
import argparse, collections, hashlib, json, re, sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
LIBRARY = PROJECT / "模板库"
CREATIVE = LIBRARY / "创赛" / "分析索引" / "creativecup-curation.jsonl"
CAREER_DIR = LIBRARY / "BB0086 大学生职业生涯规划大赛 PPT"
CAREER = CAREER_DIR / "分析索引" / "SOURCE-TAXONOMY.jsonl"
CREATIVE_STYLE = LIBRARY / "创赛" / "分析索引" / "STYLE-CATALOG.jsonl"
CAREER_STYLE = CAREER_DIR / "分析索引" / "STYLE-CATALOG.jsonl"
EXISTING = LIBRARY / "CONTENT-CURATION.jsonl"
GENERIC = re.compile(r"^(?:未命名|新建 microsoft powerpoint|presentation\d*|演示文稿\d*|ppt模板|模板|ppt|[\d\-_.（）() ]+)$", re.I)


def rows(path):
    with path.open(encoding="utf-8-sig") as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                try: yield json.loads(line)
                except Exception as e: raise ValueError(f"{path}:{n}: {e}")


def sha256(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()


def clean_name(text):
    s=Path(text).stem
    s=re.sub(r"\s+", " ", s).strip(" _-.【】[]")
    s=re.sub(r"(?:副本|拷贝|copy|\(\d+\)|（\d+）)$", "", s, flags=re.I)
    s=re.sub(r"^(?:\d+[、.\-_ ]+|[（(]\d+[）)]\s*)", "", s)
    s=s.strip(" _-.（）()【】[]")
    if not s or GENERIC.fullmatch(s) or len(s)<2: return None
    return s[:100]


def title_from_sidecar(source, family, style_by_sha):
    if family == "creative":
        for page in source.get("sampled_slide_text", []):
            if page.get("slide") == 1:
                title = clean_name(page.get("text", "").replace("\n", " "))
                if title: return title, "cover_slide_text"
    else:
        detail=style_by_sha.get(source.get("sha256"), {})
        for page in detail.get("style_profile", {}).get("page_role_sequence", []):
            if page.get("slide") == 1:
                title=clean_name(page.get("title", ""))
                if title: return title, "cover_structural_title"
    return None, None


def structured_name_for_generic(source, rel, family, style_by_sha):
    """Name generic files from SHA-bound full detail or manifest/legacy samples."""
    digest=source.get("sha256"); style=style_by_sha.get(digest,{})
    manifest=style.get("_manifest",{}); legacy=style.get("_legacy",{})
    info={"name":None,"quality":"low","evidence":[],"off_topic":False,"off_topic_basis":None}
    detail=None; detail_basis=None
    detail_ref=style.get("detail_ref")
    if detail_ref:
        detail_path=(LIBRARY/detail_ref).resolve()
        try: detail_path.relative_to(LIBRARY.resolve())
        except ValueError: detail_path=None
        if detail_path and detail_path.is_file():
            candidate=json.loads(detail_path.read_text(encoding="utf-8"))
            if candidate.get("source_sha256")==digest: detail=candidate;detail_basis="style_detail_full_text"
    if detail is None and legacy.get("detail_ref"):
        legacy_path=(CAREER_DIR/legacy["detail_ref"]).resolve()
        try: legacy_path.relative_to(CAREER_DIR.resolve())
        except ValueError: legacy_path=None
        if legacy_path and legacy_path.is_file():
            candidate=json.loads(legacy_path.read_text(encoding="utf-8"))
            if candidate.get("source_sha256")==digest: detail=candidate;detail_basis="legacy_style_measurements"
    if detail and detail_basis=="style_detail_full_text":
        slides=detail.get("slides",[])
        text_by_slide=[]
        for slide in slides:
            bits=[str(slide.get("title","")),*[str(x) for x in slide.get("content_sequence",[])],*[str(o.get("text","")) for o in slide.get("objects",[]) if o.get("text")]]
            text_by_slide.append(" ".join(bits))
        profile=style.get("style_profile",{})
        page_count=len(slides)
    else:
        # Legacy .ppt conversion detail keeps measurements but manifest has sampled
        # text. Use only those declared samples and label the evidence scope honestly.
        samples=manifest.get("page_samples",[])
        slides=[{"slide":x.get("slide"),"title":"","content_sequence":re.split(r"\s*/\s*",str(x.get("text","")))} for x in samples]
        text_by_slide=[str(x.get("text","")) for x in samples]
        profile={}
        page_count=detail.get("slide_count",manifest.get("slide_count",source.get("slide_count",0))) if detail else manifest.get("slide_count",source.get("slide_count",0))
    if not text_by_slide and manifest.get("page_samples"):
        samples=manifest["page_samples"]
        slides=[{"slide":x.get("slide"),"title":"","content_sequence":re.split(r"\s*/\s*",str(x.get("text","")))} for x in samples]
        text_by_slide=[str(x.get("text","")) for x in samples]
        page_count=manifest.get("slide_count",source.get("slide_count",0))
        detail_basis="manifest_sampled_pages"
    if not text_by_slide:
        info["evidence"].append("no_sha_bound_page_text_available")
        # A concise truthful name still avoids leaking a numeric stem; low confidence
        # and the explicit lack of page text remain visible in audit.
        tax=source.get("refined_taxonomy",{})
        label="职业规划模板" if tax.get("asset_type") in {"blank_template","template_with_example_content","general_presentation_template"} else "职业规划参考素材"
        info["name"]=f"{label}（页面文本未解析｜{page_count or '页数待核'}）"
        return info
    all_text=" ".join(text_by_slide)
    annual_patterns=[r"年度工作概述",r"年度工作总结",r"工作完成情况",r"成功项目展示",r"明年工作计划",r"年终总结"]
    annual_counts={p:len(re.findall(p,all_text)) for p in annual_patterns}
    annual_score=sum(annual_counts.values())
    career_terms=["护士职业规划","教师职业规划","职业生涯规划","职业规划设计书","求职简历","职业目标","生涯规划"]
    career_score=sum(len(re.findall(re.escape(t),all_text,re.I)) for t in career_terms)
    off_topic=family=="career" and detail_basis=="style_detail_full_text" and annual_score>=6 and annual_counts.get("年度工作概述",0)+annual_counts.get("年度工作总结",0)>=2
    if off_topic:
        info["off_topic"]=True
        info["off_topic_basis"]={"annual_heading_counts":annual_counts,"career_term_hits":career_score,"detail_sha256_verified":True}
        topic="年度工作总结与计划";info["quality"]="high"
    else:
        # Use a specific major/job only when the same literal wording appears on
        # the cover/early pages; taxonomy candidate lists alone can contain noisy
        # extracted phrases such as placeholders or example text.
        early_text=" ".join(text_by_slide[:4])
        explicit_topic=None
        professions=("护士","教师","会计","医生","律师","程序员","数据分析师","信息管理","机械工程","护理","幼师","公务员","高级项目经理","高级技术管理")
        for term in professions:
            if term in early_text and not re.search(r"某某|请替换|输入.{0,5}内容",early_text):
                if re.search(re.escape(term)+r".{0,12}(?:专业|职业规划|生涯规划|职业目标)",early_text) or re.search(r"(?:专业|职业规划|生涯规划|职业目标).{0,12}"+re.escape(term),early_text):
                    explicit_topic=term;break
        if explicit_topic:
            topic=explicit_topic+"职业规划";info["quality"]="medium"
        elif career_score or re.search(r"职业生涯规划|职业规划设计书|职业目标",all_text):
            topic="职业生涯规划";info["quality"]="medium" if detail_basis=="style_detail_full_text" else "low"
        else:
            topic="竞赛项目展示" if family=="creative" else "职业规划"
            info["quality"]="low"
    section_candidates=[]
    ignore=re.compile(r"^(?:目\s*录|contents?|agenda|part\s*\d*|第[一二三四五六七八九十\d]+(?:章|部分)|[0-9一二三四五六七八九十壹贰叁]+|职|业|生|涯|规|划|职业生涯规划|职业规划|汇报人.*|日期.*|logo|your title)$",re.I)
    placeholder_heading=re.compile(r"单击|点击|请在|请于|此处|添加|输入|待完善|相关标题|标题文字|文字标题|文本|过渡页|会议主题|投入\d+%|\d{1,2}月份|示例内容|默认内容|第一部分\s*$",re.I)
    for slide in slides[1:]:
        candidates=[str(slide.get("title","")),*[str(x) for x in slide.get("content_sequence",[])[:10]]]
        for val in candidates:
            val=re.sub(r"\s+"," ",val).strip(" \n\t-:：")
            if 3<=len(val)<=22 and not ignore.fullmatch(val) and re.search(r"[\u4e00-\u9fff]",val) and not placeholder_heading.search(val) and not re.search(r"请输入|请替换|添加相关标题|点击此处|输入文本内容|文字内容|感谢聆听|批评指导|标题文本|XXX|Lorem|年度工作概述|职业生涯规划/人生规划PPT|目录\s*/\s*CONTENTS|工作概述|完成情况|嘉宾|学员|某某|无名氏|延迟符|职务或头衔|操作说明|请在此输入|添加标题|请填写|汇报人|专业年级|日期|输入文本|文本内容",val,re.I):
                if val not in section_candidates: section_candidates.append(val)
            if len(section_candidates)>=3: break
        if len(section_candidates)>=3: break
    if off_topic:
        section_candidates=[name for name in ("年度工作概述","工作完成情况","成功项目展示","明年工作计划") if annual_counts.get(re.escape(name),0)>0 or (name=="年度工作概述" and annual_counts.get("年度工作概述",0)>0)]
        if not section_candidates: section_candidates=["工作总结","计划展示"]
    section_label="、".join(section_candidates[:3])
    roles=[]
    role_map={"background_problem":"问题背景","goal_positioning":"目标定位","method_approach":"实施方法","evidence_results":"成果证据","timeline_plan":"计划历程","process_architecture":"流程结构","team_organization":"团队组织","closing_outlook":"总结展望"}
    for rec in profile.get("page_role_sequence",[]):
        for role in rec.get("roles",[]):
            if role in role_map and role_map[role] not in roles: roles.append(role_map[role])
    if detail_basis=="legacy_style_measurements":
        narrative=manifest.get("taxonomy",{}).get("page_narrative_roles",[])
        role_map_legacy={"self_assessment":"自我认知","career_or_industry_research":"职业认知","goal_and_path":"目标路径","practice_and_evidence":"实践成果","review_and_adjustment":"复盘调整"}
        for pg in narrative:
            for role in pg.get("roles",[]):
                if role in role_map_legacy and role_map_legacy[role] not in roles: roles.append(role_map_legacy[role])
    role_label="、".join(roles[:3])
    palette=profile.get("theme_palette",{})
    color=palette.get("accent1") if isinstance(palette,dict) else None
    if not color and detail_basis=="legacy_style_measurements" and detail:
        for slide in detail.get("slides",[])[:3]:
            fill=next((x[0] for x in slide.get("dominant_explicit_fills",[]) if re.fullmatch(r"[0-9A-Fa-f]{6}",str(x[0]))),None)
            if fill: color=fill;break
    color=color if isinstance(color,str) and re.fullmatch(r"[0-9A-Fa-f]{6}",color) else None
    if off_topic: name="年度工作总结与计划参考"
    else:
        name=(f"{topic}模板" if family=="career" else f"{topic}项目展示参考")
    descriptors=[]
    if section_label: descriptors.append("结构"+section_label)
    elif role_label: descriptors.append("页型"+role_label)
    if color: descriptors.append("配色线索#"+color.upper())
    if page_count: descriptors.append(f"{page_count}页")
    if descriptors: name += "（"+"｜".join(descriptors)+"）"
    if not section_label and not off_topic: info["quality"]="low"
    info["name"]=name
    info["evidence"]=[detail_basis or "manifest_sampled_pages",f"cover_text={text_by_slide[0][:240] if text_by_slide else ''}",f"section_headings={section_candidates[:3]}",f"role_sequence={roles[:3]}",f"theme_accent1={color}",f"slide_count={page_count}",f"annual_heading_counts={annual_counts}",f"career_term_hits={career_score}","visual_review=not_performed"]
    return info


def early_instructional_override(rel, source, style_by_sha):
    """Require both a guide-like basename and instructional text near the front."""
    basename=Path(rel).name
    basename_hit=bool(re.search(r"指南|教程|使用说明|操作手册|操作指南|评分标准|大赛规则|比赛规则|章程|培训课件|备赛攻略",basename))
    if not basename_hit: return False, []
    style=style_by_sha.get(source.get("sha256"),{})
    detail_ref=style.get("detail_ref")
    if not detail_ref: return False, ["guide_basename_match_no_style_detail"]
    detail_path=(LIBRARY/detail_ref).resolve()
    try: detail_path.relative_to(LIBRARY.resolve())
    except ValueError: return False, ["guide_basename_match_invalid_detail_path"]
    if not detail_path.is_file(): return False, ["guide_basename_match_detail_missing"]
    detail=json.loads(detail_path.read_text(encoding="utf-8"))
    if detail.get("source_sha256")!=source.get("sha256"): return False, ["guide_basename_match_detail_sha_mismatch"]
    pages=detail.get("slides",[])[:3]
    text=" ".join(str(x) for slide in pages for x in [slide.get("title", ""),*slide.get("content_sequence",[]),*[o.get("text","") for o in slide.get("objects",[]) if o.get("text")]])
    matches=re.findall(r"备赛指南|评分标准|比赛规则|大赛规则|操作手册|使用说明|操作指南|具体步骤|请按照|请根据.{0,8}(?:完成|提交|准备)|点击.{0,10}(?:菜单|按钮|选项)",text)
    return bool(matches), [f"guide_basename={basename}",f"early_instructional_phrases={matches[:12]}"]


def classify(source, rel, family, style_by_sha):
    if family=="creative":
        role=str(source.get("material_role", "")); typ=str(source.get("material_type", "")); cat=str(source.get("category", ""))
        evidence=["collection=创赛",f"material_role={role or 'missing'}",f"material_type={typ or 'missing'}",f"category={cat or 'missing'}","visual_review=not_performed"]
        if "指南" in typ or "教程" in typ or "备赛资料" in typ:
            category,sub,asset="教育与培训","竞赛备赛指南","guide"
        elif "模板" in typ or "框架" in typ:
            category,sub,asset="竞赛与答辩","创新创业路演","template"
        elif "已填写项目稿" in typ:
            category,sub,asset="竞赛与答辩","创新创业参赛案例","portfolio"
        elif "项目内容稿" in typ:
            category,sub,asset="竞赛与答辩","创新创业项目参考","reference"
        elif "挑战杯" in role or "研究/调查" in role:
            category,sub,asset="竞赛与答辩","挑战杯研究与调查","reference"
        elif "乡村振兴" in role or "乡村振兴" in cat:
            category,sub,asset="竞赛与答辩","乡村振兴项目路演","reference"
        else:
            category,sub,asset="竞赛与答辩","创新创业项目展示","reference"
        confidence="high" if asset in {"guide","template","portfolio"} else ("low" if "待判" in role or "待判" in typ else "medium")
        name=clean_name(rel); name_basis="source_filename" if name else None
        if not name: name,name_basis=title_from_sidecar(source,family,style_by_sha)
        evidence.append("display_name="+str(name_basis) if name else "display_name=generic_filename_filtered")
        return category,sub,asset,confidence,name,evidence

    # Career labels follow refined_taxonomy.asset_type; ancestor folder names do not
    # determine whether a specific deck is a guide.
    tax=source.get("refined_taxonomy") or {}; signals=tax.get("classification_signals",{})
    refined=str(tax.get("asset_type", ""))
    guide_override,guide_evidence=early_instructional_override(rel,source,style_by_sha)
    if guide_override or refined=="rules_or_manual":
        category,sub,asset="教育与培训","职业规划与赛事指南","guide"
    elif refined in {"blank_template","template_with_example_content","general_presentation_template"}:
        category,sub,asset="人物与职业","职业发展规划模板","template"
    elif refined=="completed_example":
        category,sub,asset="人物与职业","职业发展规划案例","portfolio"
    elif refined=="interview_evidence":
        category,sub,asset="人物与职业","职业访谈与体验","reference"
    elif refined in {"career_reference_unclear_completion","career_content_reference"}:
        category,sub,asset="人物与职业","职业规划参考素材","reference"
    else:
        category,sub,asset="人物与职业","职业规划待判参考素材","reference"
    evidence=["collection=大学生职业生涯规划大赛",f"refined_taxonomy.asset_type={refined or 'missing'}",f"track={tax.get('track','unspecified')}",f"classification_basis={tax.get('classification_basis_level','unknown')}",f"guide_override={guide_override}",*guide_evidence,"visual_review=machine_only_not_visually_reviewed"]
    style=style_by_sha.get(source.get("sha256"),{})
    objects=(style.get("style_profile") or {}).get("object_counts") or {}
    page_count=style.get("slide_count") or 0
    flattened=page_count>=3 and objects.get("pic",0)>=page_count and sum(objects.values())<=page_count*1.2
    if flattened:
        category,sub,asset="人物与职业","整页图片案例参考","reference"
        evidence.append(f"flattened_page_structure=full_deck_pages:{page_count},objects:{objects}")
    name_info=structured_name_for_generic(source,rel,family,style_by_sha) if clean_name(rel) is None else None
    if name_info and name_info["off_topic"]:
        category,sub,asset="工作汇报","年度工作总结与计划","reference"
    basis=tax.get("classification_basis_level","")
    confidence="medium" if basis.startswith("full_") or basis=="full_ooxml_deck_structure" else "low"
    if name_info and name_info["off_topic"]: confidence="high"
    elif flattened: confidence="high"
    elif refined=="rules_or_manual" or guide_override or refined in {"blank_template","template_with_example_content","completed_example","interview_evidence"}: confidence="high"
    name=clean_name(rel); name_basis="source_filename" if name else None
    if not name and name_info and name_info.get("name"):
        name,name_basis=name_info["name"],"sha_bound_structural_description"
    if not name: name,name_basis=title_from_sidecar(source,family,style_by_sha)
    if not name:
        candidates=tax.get("profession_candidates",[])
        candidate=clean_name(str(candidates[0])) if candidates else None
        if candidate and not re.search(r"数据分析师|职业规划|求职|规划",candidate): name=candidate
    evidence.append("display_name="+str(name_basis) if name else "display_name=generic_filename_filtered")
    if name_info:
        evidence.extend("name_evidence="+x for x in name_info.get("evidence",[]))
        evidence.append("name_quality="+name_info.get("quality","low"))
        if name_info.get("off_topic"):
            evidence.append("off_topic=true")
            evidence.append("off_topic_evidence="+json.dumps(name_info.get("off_topic_basis"),ensure_ascii=False,sort_keys=True))
    return category,sub,asset,confidence,name,evidence


def conflict_evidence(digest, source_rows, style_by_sha):
    """Inspect full structural pages for duplicate-SHA groups with disagreements."""
    family="creative" if any(r.get("path","").startswith("创赛/") for r in source_rows) else "career"
    fallback=("竞赛与答辩","创新创业待判参考素材") if family=="creative" else ("人物与职业","职业规划待判参考素材")
    style=style_by_sha.get(digest)
    result={"decision":"unresolved_reference","asset_type":"reference","confidence":"low","category":fallback[0],"subcategory":fallback[1],"evidence":[]}
    if not style:
        result["evidence"].append("no_style_catalog_record")
        return result
    detail_path=(LIBRARY / style.get("detail_ref","")).resolve()
    try: detail_path.relative_to(LIBRARY.resolve())
    except ValueError:
        result["evidence"].append("detail_path_outside_library"); return result
    if not detail_path.is_file():
        result["evidence"].append("style_detail_missing"); return result
    detail=json.loads(detail_path.read_text(encoding="utf-8"))
    if detail.get("source_sha256")!=digest:
        result["evidence"].append("style_detail_source_sha_mismatch"); return result
    slides=detail.get("slides",[]); all_text=[]; placeholder_slides=0; tutorial_slides=0
    placeholders=re.compile(r"请输入|请填写|待补充|待完善|在这里|关键词语|xxx+|姓名/学校|替换为|请替换|点击添加.{0,8}标题文字|your title|添加相关内容",re.I)
    tutorial=re.compile(r"教程|操作指南|使用说明|本教程|本节将|软件界面|安装步骤|下载步骤|点击.{0,8}(?:菜单|按钮|选项|图标)|在.{0,8}(?:菜单|选项).{0,8}选择|如图.{0,4}所示",re.I)
    for slide in slides:
        bits=[str(slide.get("title", ""))]
        bits.extend(str(x) for x in slide.get("content_sequence",[]))
        bits.extend(str(o.get("text", "")) for o in slide.get("objects",[]) if o.get("text"))
        text=" ".join(bits); all_text.append(text)
        if placeholders.search(text): placeholder_slides+=1
        if tutorial.search(text): tutorial_slides+=1
    joined=" ".join(all_text)
    placeholder_hits=len(placeholders.findall(joined)); tutorial_matches=tutorial.findall(joined); tutorial_hits=len(tutorial_matches); tutorial_terms=dict(collections.Counter(tutorial_matches))
    coverage=lambda n: round(n/max(1,len(slides)),3)
    roles=collections.Counter(role for rec in style.get("style_profile",{}).get("page_role_sequence",[]) for role in rec.get("roles",[]))
    titles=[str(s.get("title","")) for s in slides[:3] if s.get("title")]
    result["evidence"]=["detail_sha256_verified",f"full_pages={len(slides)}",f"cover_titles={titles}",f"placeholder_hits={placeholder_hits}",f"placeholder_slide_coverage={coverage(placeholder_slides)}",f"tutorial_term_hits={tutorial_hits}",f"tutorial_terms={tutorial_terms}",f"tutorial_slide_coverage={coverage(tutorial_slides)}",f"page_roles={dict(roles.most_common(12))}"]
    taxonomies=[r for r in source_rows]
    types=" ".join(str(r.get("material_type", ""))+" "+str((r.get("refined_taxonomy") or {}).get("asset_type","")) for r in taxonomies)
    narrative=bool(re.search(r"career_narrative_content|已填写项目稿|完成案例|completed_case|作品展示",types))
    # Resolve only when full-page text has sufficiently strong positive evidence.
    if tutorial_hits>=3 and coverage(tutorial_slides)>=0.15:
        result.update(decision="tutorial_content_evidence",asset_type="guide",confidence="high")
        result["category"],result["subcategory"]="教育与培训",("竞赛备赛指南" if family=="creative" else "职业规划与赛事指南")
    elif placeholder_hits>=3 and coverage(placeholder_slides)>=0.10 and tutorial_hits<3:
        result.update(decision="placeholder_evidence",asset_type="template",confidence="high")
        result["category"],result["subcategory"]=("竞赛与答辩","创新创业路演") if family=="creative" else ("人物与职业","职业发展规划模板")
    elif narrative and placeholder_hits<=1 and len(slides)>=4 and (roles.get("method_approach",0)+roles.get("evidence_results",0)+roles.get("timeline_plan",0))>=2 and tutorial_hits<3:
        result.update(decision="completed_narrative_evidence",asset_type="portfolio",confidence="medium")
        result["category"],result["subcategory"]=("竞赛与答辩","创新创业参赛案例") if family=="creative" else ("人物与职业","职业发展规划案例")
    else:
        result["decision"]="insufficient_or_conflicting_full_deck_evidence"
        result["evidence"].append(f"narrative_signal={narrative}")
        result["category"],result["subcategory"]=("竞赛与答辩","创新创业待判参考素材") if family=="creative" else ("人物与职业","职业规划待判参考素材")
    result["evidence"].append("decision_uses_structural_text_and_roles_only_visual_review_not_performed")
    return result


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",type=Path,default=Path("/private/tmp/contest-library-curation-proposal.jsonl"))
    ap.add_argument("--audit",type=Path,default=Path("/private/tmp/contest-library-curation-audit.json"))
    args=ap.parse_args()
    existing={r["path"]:r for r in rows(EXISTING)}
    style_by_sha={}
    for sf in (CREATIVE_STYLE, CAREER_STYLE):
        for sr in rows(sf): style_by_sha[sr.get("source_sha256")] = sr
    for mr in rows(CAREER_DIR/"分析索引"/"SOURCE-MANIFEST.jsonl"):
        style_by_sha.setdefault(mr.get("sha256"),{})["_manifest"]=mr
    for lr in rows(CAREER_DIR/"分析索引"/"legacy-近似结构"/"LEGACY-STYLE-CATALOG.jsonl"):
        style_by_sha.setdefault(lr.get("source_sha256"),{})["_legacy"]=lr
    proposal=[]; problems=[]; hashes=collections.defaultdict(list); checked=0; existing_target=0
    sources=[]
    for family, sidecar, base, path_key in (("creative",CREATIVE,PROJECT,"path"),("career",CAREER,CAREER_DIR,"source_path")):
        for src in rows(sidecar):
            raw=src.get(path_key)
            p=(base / raw).resolve()
            try: p.relative_to(LIBRARY.resolve())
            except ValueError: problems.append({"source":family,"path":raw,"error":"path_outside_library"}); continue
            rel=p.relative_to(LIBRARY.resolve()).as_posix()
            expected=src.get("sha256") or src.get("inventory_sha256")
            if not p.is_file(): problems.append({"source":family,"path":rel,"error":"missing_source"}); continue
            actual=sha256(p); checked+=1
            if expected!=actual: problems.append({"source":family,"path":rel,"error":"sha256_mismatch","expected":expected,"actual":actual}); continue
            if rel in existing:
                prior=existing[rel]
                prior_family=(prior.get("classification_observation") or {}).get("source_collection")
                if (prior.get("sha256")!=actual or prior_family!=family
                        or prior.get("review_status")!="machine_classified_review_required"):
                    problems.append({"source":family,"path":rel,"error":"existing_path_collision","existing_sha256":prior.get("sha256"),"source_sha256":actual}); continue
                existing_target+=1
            category,sub,asset,confidence,name,evidence=classify(src,rel,family,style_by_sha)
            row={"path":rel,"sha256":actual,"display_name":name or ("待人工命名（"+p.stem[:70]+"）"),"category":category,"subcategory":sub,"asset_type":asset,"confidence":confidence,"review_status":"machine_classified_review_required","evidence_basis":"sidecar_structural_and_path_evidence","evidence_ref":str(sidecar.relative_to(PROJECT)),"classification_observation":{"pages":src.get("pages_inventory",src.get("slide_count",0)),"naming_basis":"sha_bound_structural_description" if any(x=="display_name=sha_bound_structural_description" for x in evidence) else ("source_filename" if name else "generic_source_filename"),"source_collection":family,"evidence":evidence,"visual_review":"not_performed","off_topic": "off_topic=true" in evidence,"flattened_page_structure":any(x.startswith("flattened_page_structure=") for x in evidence)}}
            hashes[actual].append(row); sources.append((src,row))
    # For same-byte copies with disagreeing initial labels, use the full structural
    # detail record and source cover text. Weak/ambiguous cases stay low-confidence
    # reference entries, flagged for human review.
    duplicate_conflicts=[]
    source_groups=collections.defaultdict(list)
    for src,row in sources: source_groups[row["sha256"]].append((src,row))
    for digest, pairs in source_groups.items():
        group=[row for _,row in pairs]
        off_topic_rows=[r for r in group if "off_topic=true" in r["classification_observation"]["evidence"]]
        if off_topic_rows:
            canonical=off_topic_rows[0]
            for r in group:
                r["category"],r["subcategory"],r["asset_type"]="工作汇报","年度工作总结与计划","reference"
                r["confidence"]="high"
                r["classification_observation"]["off_topic"] = True
                if r is not canonical:
                    r["classification_observation"]["evidence"].append("off_topic=true")
                    evidence=next((x for x in canonical["classification_observation"]["evidence"] if x.startswith("off_topic_evidence=")),None)
                    if evidence: r["classification_observation"]["evidence"].append(evidence)
                    r["classification_observation"]["duplicate_sha_off_topic_resolution"]="same_sha_full_deck_off_topic_evidence"
        labels={(r["category"],r["subcategory"],r["asset_type"]) for r in group}
        if len(labels)>1 and not off_topic_rows:
            srcs=[src for src,_ in pairs]
            decision=conflict_evidence(digest,srcs,style_by_sha)
            paths=[r["path"] for r in group]
            resolved=decision["decision"] in {"tutorial_content_evidence","placeholder_evidence","completed_narrative_evidence"}
            conflict_record={"sha256":digest,"paths":paths,"initial_labels":[list(x) for x in sorted(labels)],**decision,"unresolved":not resolved}
            duplicate_conflicts.append(conflict_record)
            for r in group:
                r["category"],r["subcategory"],r["asset_type"]=decision["category"],decision["subcategory"],decision["asset_type"]
                r["confidence"]=decision["confidence"]
                r["classification_observation"]["duplicate_sha_conflict"]={"flag":True,"resolved_by_full_deck_evidence":resolved,"decision":decision["decision"],"evidence":decision["evidence"]}
        if len(group)>1:
            confidence_rank={"low":0,"medium":1,"high":2}
            conservative=min((r["confidence"] for r in group),key=lambda c:confidence_rank.get(c,0))
            structural=next((r for r in group if r["classification_observation"].get("naming_basis")=="sha_bound_structural_description"),None)
            name_source=structural or max(group,key=lambda r:len(r["display_name"]) if not r["display_name"].startswith("待人工命名") else 0)
            for r in group:
                r["confidence"]=conservative
                if r["display_name"]!=name_source["display_name"]:
                    r["display_name"]=name_source["display_name"]
                    r["classification_observation"]["naming_basis"]="same_sha_name_unified"
                    r["classification_observation"]["evidence"].append("duplicate_sha_name_unified")
        for r in group:
            r["classification_observation"]["duplicate_sha_paths"]=[x["path"] for x in group] if len(group)>1 else []
    proposal=[r for _,r in sources]
    args.output.parent.mkdir(parents=True,exist_ok=True); args.audit.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text("".join(json.dumps(r,ensure_ascii=False,sort_keys=True)+"\n" for r in proposal),encoding="utf-8")
    audit={"mode":"review_only_no_global_mutation","expected_rows":2533,"proposed_rows":len(proposal),"sources":{"creativecup":sum(1 for s,_ in sources if s.get("category") is not None),"career":sum(1 for s,_ in sources if s.get("source_path") is not None)},"source_bytes_hash_verified":checked,"existing_curation_rows":len(existing),"existing_target_rows":existing_target,"proposal_diff_paths":[r["path"] for r in proposal if r["path"] in existing and any(existing[r["path"]].get(k)!=r.get(k) for k in ("category","subcategory","asset_type","confidence","display_name"))],"path_collisions":sum(x.get("error")=="existing_path_collision" for x in problems),"sha_mismatches":sum(x.get("error")=="sha256_mismatch" for x in problems),"missing_sources":sum(x.get("error")=="missing_source" for x in problems),"duplicate_sha_groups":sum(len(v)>1 for v in hashes.values()),"duplicate_sha_semantic_conflicts":len(duplicate_conflicts),"duplicate_sha_semantic_conflicts_unresolved":sum(x["unresolved"] for x in duplicate_conflicts),"duplicate_sha_conflict_groups":duplicate_conflicts,"original_generic_filename_rows":sum(clean_name(Path(r["path"]).name) is None for r in proposal),"descriptive_names_generated_from_style":sum("display_name=sha_bound_structural_description" in r["classification_observation"]["evidence"] for r in proposal),"unresolved_generic_names":sum(r["display_name"].startswith("待人工命名") for r in proposal),"ambiguous_structural_names":sum("name_quality=low" in r["classification_observation"]["evidence"] for r in proposal),"ambiguous_name_rows":[{"path":r["path"],"display_name":r["display_name"],"evidence":[x for x in r["classification_observation"]["evidence"] if x.startswith("name_evidence=") or x.startswith("name_quality=")]} for r in proposal if "name_quality=low" in r["classification_observation"]["evidence"]],"off_topic_reference_rows":[{"path":r["path"],"display_name":r["display_name"],"category":r["category"],"asset_type":r["asset_type"],"evidence":[x for x in r["classification_observation"]["evidence"] if x.startswith("off_topic")]} for r in proposal if "off_topic=true" in r["classification_observation"]["evidence"]],"label_counts":{"categories":dict(collections.Counter(r["category"] for r in proposal)),"asset_types":dict(collections.Counter(r["asset_type"] for r in proposal)),"confidence":dict(collections.Counter(r["confidence"] for r in proposal)),"by_collection":{family:dict(collections.Counter(r["asset_type"] for r in proposal if r["classification_observation"]["source_collection"]==family)) for family in ("creative","career")}},"output":str(args.output),"problems":problems}
    args.audit.write_text(json.dumps(audit,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    summary={k:v for k,v in audit.items() if k not in {"problems","unresolved_name_rows","ambiguous_name_rows","off_topic_reference_rows","duplicate_sha_conflict_groups"}}
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    if len(proposal)!=2533 or problems: return 1
    return 0
if __name__=="__main__": sys.exit(main())
