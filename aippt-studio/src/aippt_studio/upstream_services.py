"""Configured service calls; credentials never enter a Work request/receipt."""
import os
import shutil
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zipfile import ZipFile, BadZipFile

import requests

from .models import JobBrief
from .storage import job_dir, load_job, read_model, write_json


def prepare_pptagent(request: dict, output: Path, source: Path) -> tuple[Path, Path]:
    job = load_job(request["job"])
    brief = read_model(job_dir(job.id) / "planning/brief.json", JobBrief)
    if not brief.cloud_allowed:
        raise ValueError("model engine calls are disabled for this job")
    configured = os.environ.get("AIPPT_PPTAGENT_PYTHON", "")
    if not configured or not os.environ.get("PPTAGENT_MODEL"):
        raise ValueError("PPTAgent requires AIPPT_PPTAGENT_PYTHON and PPTAGENT_MODEL; configure model endpoint/key in environment")
    python = Path(configured).expanduser().absolute()
    if not python.is_file() or source.resolve() in python.resolve().parents:
        raise ValueError("PPTAgent Python must exist outside the source repository")
    steps = request.get("steps")
    allowed = {"list_templates", "set_template", "create_slide", "write_slide", "generate_slide", "save_generated_slides"}
    if not isinstance(steps, list) or not 1 <= len(steps) <= 80 or any(s.get("name") not in allowed for s in steps):
        raise ValueError("PPTAgent steps must be 1-80 allowed template/slide MCP calls")
    names = [step["name"] for step in steps]
    if names.count("set_template") > 1 or names.count("save_generated_slides") > 1 or ("save_generated_slides" in names and names[-1] != "save_generated_slides"):
        raise ValueError("select a template once and save once as the final step; upstream resets its slide state")
    # PPTAgent may create cache files beside its bundled templates. A complete
    # runtime source copy keeps those writes outside the checked-out repository.
    runtime_source = output / "runtime-source"
    runtime_source.mkdir()
    for name in ("pptagent", "deeppresenter"):
        package = source / name
        if any(path.is_symlink() for path in package.rglob("*")):
            raise ValueError("PPTAgent runtime package contains a symlink")
        shutil.copytree(package, runtime_source / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(source / "LICENSE", runtime_source / "LICENSE")
    return python, runtime_source


def generate_presenton(request: dict, output: Path) -> dict:
    job = load_job(request["job"])
    brief = read_model(job_dir(job.id) / "planning/brief.json", JobBrief)
    if not brief.cloud_allowed:
        raise ValueError("remote engine calls are disabled for this job")
    base = os.environ.get("AIPPT_PRESENTON_URL", "").rstrip("/")
    parsed = urlparse(base)
    if not base:
        raise ValueError("Presenton service is not configured; set AIPPT_PRESENTON_URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"} or not parsed.hostname:
        raise ValueError("Presenton URL must be a service origin without credentials or query")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
        raise ValueError("Presenton requires HTTPS or a local loopback HTTP service")
    content = request.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Presenton requires explicit content; no job files are uploaded implicitly")
    payload = {"content": content, "n_slides": brief.target_pages, "language": brief.language,
               "template": request.get("template", "general"), "instructions": request.get("instructions"),
               "web_search": False, "export_as": "pptx", "trigger_webhook": False}
    session = requests.Session()
    headers = {}
    if token := os.environ.get("AIPPT_PRESENTON_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = session.post(base + "/api/v1/ppt/presentation/generate", json=payload,
                                headers=headers, timeout=(10, 300), allow_redirects=False)
        if response.status_code != 200:
            raise ValueError(f"Presenton generation returned HTTP {response.status_code}")
        result = response.json()
        path = result.get("path", "")
        # Upstream publishes export paths through /app_data or /static.
        # A backend filesystem path, a redirect, or another origin is not a download authority.
        url = urljoin(base + "/", path)
        if urlparse(url).netloc != parsed.netloc or urlparse(url).scheme != parsed.scheme or not urlparse(url).path.startswith(("/app_data/", "/static/")):
            raise ValueError("Presenton returned an unsupported artifact URL; inspect the service export configuration")
        download = session.get(url, headers=headers, timeout=(10, 120), allow_redirects=False, stream=True)
        if download.status_code != 200:
            raise ValueError(f"Presenton artifact returned HTTP {download.status_code}")
        target = output / "candidate.pptx"
        size = 0
        with target.open("wb") as stream:
            for chunk in download.iter_content(65536):
                size += len(chunk)
                if size > 100 * 1024 * 1024:
                    raise ValueError("Presenton artifact exceeds 100 MB")
                stream.write(chunk)
        with ZipFile(target) as archive:
            if "ppt/presentation.xml" not in archive.namelist():
                raise ValueError("Presenton artifact is not a PPTX")
        safe_result = {k: result.get(k) for k in ("presentation_id", "path", "edit_path")}
        write_json(output / "service-result.json", safe_result)
        return {"artifact": "candidate.pptx", "service_result": "service-result.json",
                "status": "candidate_requires_review", "job_id": job.id,
                "note": "Service generation only; route, approvals, evidence and Office QA remain in Studio."}
    except (requests.RequestException, BadZipFile) as error:
        raise ValueError(f"Presenton service failed: {type(error).__name__}") from error
    finally:
        session.close()
