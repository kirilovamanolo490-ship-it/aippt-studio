from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest


STUDIO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = STUDIO_ROOT / "scripts" / "package-powerpoint-skill.py"
SKILL_DIR = STUDIO_ROOT / "integrations" / "aippt-studio-powerpoint"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "package_powerpoint_skill", SCRIPT_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _copy_skill(tmp_path: Path) -> Path:
    target = tmp_path / SKILL_DIR.name
    shutil.copytree(SKILL_DIR, target)
    return target


def test_portable_skill_source_is_valid_and_has_exact_inventory():
    module = _load_module()

    inventory = module.validate_skill_source(SKILL_DIR)

    assert [entry.path for entry in inventory] == sorted(module.EXPECTED_FILES)
    assert all(entry.bytes > 0 for entry in inventory)


@pytest.mark.parametrize(
    ("raw_suffix", "yaml_suffix"),
    [
        (b"\x00", None),
        (None, '\nextra: "\\0"\n'),
    ],
)
def test_portable_skill_rejects_raw_and_decoded_nonportable_control_characters(
    tmp_path: Path, raw_suffix: bytes | None, yaml_suffix: str | None
):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    manifest = candidate / "agents" / "openai.yaml"
    if raw_suffix is not None:
        manifest.write_bytes(manifest.read_bytes() + raw_suffix)
    else:
        assert yaml_suffix is not None
        manifest.write_text(manifest.read_text(encoding="utf-8") + yaml_suffix, encoding="utf-8")

    with pytest.raises(module.PortableSkillError, match="forbidden text character"):
        module.validate_skill_source(candidate)


def test_portable_skill_rejects_a_decoded_yaml_unpaired_surrogate(tmp_path: Path):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    manifest = candidate / "agents" / "openai.yaml"
    content = manifest.read_text(encoding="utf-8")
    manifest.write_text(
        content.replace('display_name: "AI PPT Studio for PowerPoint"', 'display_name: "\\uD800"'),
        encoding="utf-8",
    )

    with pytest.raises(module.PortableSkillError, match="forbidden text character"):
        module.validate_skill_source(candidate)


@pytest.mark.parametrize(
    "addition",
    [
        "!!binary L1VzZXJzL21hYy9wcml2YXRlL2RlY2sucHB0eA==",
        "!!binary dG9rZW46IHNlY3JldHZhbHVl",
        "!!binary YWxpY2VAZXhhbXBsZS5jb20=",
        "!!binary dmFsdWU6ICZ2IHNlY3JldHZhbHVlCnRva2VuOiAqdg==",
    ],
)
def test_portable_skill_rejects_privacy_values_decoded_from_yaml_binary(
    tmp_path: Path, addition: str
):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    skill_md = candidate / "SKILL.md"
    skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\n" + addition + "\n", encoding="utf-8")

    with pytest.raises(module.PortableSkillError, match="macOS absolute user path|credential value|personal contact"):
        module.validate_skill_source(candidate)


@pytest.mark.parametrize("addition", ["!!binary AA==", "!!binary AQ==", "!!binary fw=="])
def test_portable_skill_rejects_forbidden_controls_decoded_from_yaml_binary(
    tmp_path: Path, addition: str
):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    skill_md = candidate / "SKILL.md"
    skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\n" + addition + "\n", encoding="utf-8")

    # The shared semantic scanner rejects a decoded binary control before the
    # source package can treat opaque YAML as harmless Markdown prose.
    with pytest.raises(module.PortableSkillError, match="credential value"):
        module.validate_skill_source(candidate)


@pytest.mark.parametrize(
    "content",
    [
        "foo: bar\n",
        "{}\n",
        "interface: {}\n",
        "interface:\n  display_name: x\n",
    ],
)
def test_portable_skill_rejects_incomplete_or_unusable_agent_manifest(tmp_path: Path, content: str):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    (candidate / "agents" / "openai.yaml").write_text(content, encoding="utf-8")

    with pytest.raises(module.PortableSkillError, match="agent manifest|interface is incomplete"):
        module.validate_skill_source(candidate)


def test_portable_skill_release_has_one_sanitized_top_level_directory(tmp_path: Path):
    module = _load_module()
    archive_path = tmp_path / "release.zip"

    release = module.build_release(SKILL_DIR, archive_path)

    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == [
            f"{SKILL_DIR.name}/{path}" for path in sorted(module.EXPECTED_FILES)
        ]

    manifest_path = release["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["skill_name"] == SKILL_DIR.name
    assert manifest["archive_sha256"]
    assert [entry["path"] for entry in manifest["files"]] == sorted(
        module.EXPECTED_FILES
    )


def test_portable_skill_rejects_local_path_leakage(tmp_path: Path):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    skill_md = candidate / "SKILL.md"
    skill_md.write_text(
        skill_md.read_text(encoding="utf-8") + "\nfile://" + "/private/input.pptx\n",
        encoding="utf-8",
    )

    with pytest.raises(module.PortableSkillError, match="file URI"):
        module.validate_skill_source(candidate)


def test_portable_skill_rejects_windows_absolute_path_leakage(tmp_path: Path):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    skill_md = candidate / "SKILL.md"
    skill_md.write_text(
        skill_md.read_text(encoding="utf-8") + "\nC:\\\\private\\\\input.pptx\n",
        encoding="utf-8",
    )

    with pytest.raises(module.PortableSkillError, match="Windows absolute path"):
        module.validate_skill_source(candidate)


def test_portable_skill_rejects_posix_home_path_leakage(tmp_path: Path):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    skill_md = candidate / "SKILL.md"
    skill_md.write_text(
        skill_md.read_text(encoding="utf-8") + "\n/home/alice/private.pptx\n",
        encoding="utf-8",
    )

    with pytest.raises(module.PortableSkillError, match="POSIX local path"):
        module.validate_skill_source(candidate)


def test_portable_skill_allows_remote_url_with_double_slash_path(tmp_path: Path):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    skill_md = candidate / "SKILL.md"
    skill_md.write_text(
        skill_md.read_text(encoding="utf-8")
        + (
            "\nhttps://example.invalid//Users/alice/reference"
            "\nhttps://example.invalid///server/share/reference"
            "\nhttps://example.invalid/%2F%2Fserver/share/reference\n"
            "https://example.invalid/assets;v=1/Users/logo.png\n"
            "https://example.invalid/;work/37\n"
            "https://localhost/Users/alice/reference\n"
            "https://127.0.0.1/Users/alice/reference\n"
            "https://[::1]/Users/alice/reference\n"
            "https://example.invalid:8443/Users/alice/reference\n"
            "https://例子.测试/Users/alice/reference\n"
        ),
        encoding="utf-8",
    )

    assert module.validate_skill_source(candidate)


def test_portable_skill_rejects_missing_local_reference(tmp_path: Path):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    reference = candidate / "references" / "intake-routing.md"
    reference.write_text(
        reference.read_text(encoding="utf-8") + "\n[missing](not-present.md)\n",
        encoding="utf-8",
    )

    with pytest.raises(module.PortableSkillError, match="missing local reference"):
        module.validate_skill_source(candidate)


def test_portable_skill_rejects_missing_schema_reference(tmp_path: Path):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    schema_path = candidate / "references" / "schemas" / "aippt-handoff-bundle-v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["properties"]["packet"]["$ref"] = "missing-contract.schema.json"
    schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(module.PortableSkillError, match="does not match the local canonical schema"):
        module.validate_skill_source(candidate)


def test_portable_skill_rejects_an_invalid_machine_contract(tmp_path: Path):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    schema_path = candidate / "references" / "schemas" / "aippt-learning-packet-v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["$id"] = "urn:wrong"
    schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(module.PortableSkillError, match="does not match the local canonical schema"):
        module.validate_skill_source(candidate)


def test_portable_skill_rejects_a_schema_that_diverges_from_the_local_receiver(tmp_path: Path):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    schema_path = candidate / "references" / "schemas" / "aippt-learning-packet-v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["properties"]["transmission_state"]["enum"].append("received")
    schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(module.PortableSkillError, match="does not match the local canonical schema"):
        module.validate_skill_source(candidate)


@pytest.mark.parametrize(
    ("addition", "match"),
    [
        ("https://example.invalid/api?access_token=secret-value-123456789", "credential value"),
        ("client.private@example.com", "personal contact"),
        ("Internal trace: /root/private-client/deck.pptx", "POSIX local path"),
        ("Internal trace: /mnt/confidential/report.pptx", "POSIX local path"),
        ("Internal trace: /workspace/private/deck.pptx", "POSIX local path"),
        ("file:/root/private-client/deck.pptx", "file URI"),
        ("~alice/private-client/deck.pptx", "tilde local path"),
        ("source:~alice/private-client/deck.pptx", "tilde local path"),
        (r"source:\\server\share\private-client.pptx", "UNC local path"),
        ("//server/share/private-client.pptx", "UNC local path"),
        (r"\\server/share/private-client.pptx", "UNC local path"),
        (r"//server\share\private-client.pptx", "UNC local path"),
        (r"\\server/share\private-client.pptx", "UNC local path"),
        ("smb://server/share/private-client.pptx", "network file URI"),
        ("https://example.invalid/?source=/Users/alice/private-client.pptx", "macOS absolute user path"),
        ("https://example.invalid/?source=//server/share/private-client.pptx", "UNC local path"),
        ("https://example.invalid/#source=/Users/alice/private-client.pptx", "macOS absolute user path"),
        ("[remote](https://example.invalid/assets)/Users/alice/private-client.pptx", "macOS absolute user path"),
        ("[remote](https://example.invalid/assets)/work/37", "task Work path"),
        ("https:///Users/alice/private-client.pptx", "malformed web URI carrying local path"),
        ("http:///root/private-client/deck.pptx", "malformed web URI carrying local path"),
        ("wss:///Users/alice/private-client.pptx", "malformed web URI carrying local path"),
        ("https:////server/share/private-client.pptx", "malformed web URI carrying UNC path"),
        ("https:%2F%2F%2FUsers%2Falice%2Fprivate-client.pptx", "malformed web URI carrying local path"),
        ("https://@/Users/alice/private-client.pptx", "macOS absolute user path"),
        ("https://user@/Users/alice/private-client.pptx", "macOS absolute user path"),
        ("https://user:password@/Users/alice/private-client.pptx", "macOS absolute user path"),
        ("https://:password@/Users/alice/private-client.pptx", "macOS absolute user path"),
        ("https://./Users/alice/private-client.pptx", "macOS absolute user path"),
        ("https://../Users/alice/private-client.pptx", "macOS absolute user path"),
        ("https://%40/Users/alice/private-client.pptx", "macOS absolute user path"),
        ("https://example.invalid:not-a-port/Users/alice/private-client.pptx", "macOS absolute user path"),
        ("https://(host)/Users/alice/private-client.pptx", "macOS absolute user path"),
        ("https://(/Users/alice/private-client.pptx)", "macOS absolute user path"),
        ("https://{host}/Users/alice/private-client.pptx", "macOS absolute user path"),
        ("https://,/Users/alice/private-client.pptx", "macOS absolute user path"),
        (r"/Users\alice\private-client.pptx", "macOS absolute user path"),
        (r"\Users\alice\private-client.pptx", "Windows rooted path"),
        (r"\work\37\workspace", "Windows rooted path"),
        (r"\node_modules\private", "Windows rooted path"),
        (r"C:Users\alice\private-client.pptx", "Windows drive-relative path"),
        (r"C:work\37\workspace", "Windows drive-relative path"),
        (r"%USERPROFILE%\private\deck.pptx", "Windows environment path"),
        (r"%HOMEDRIVE%%HOMEPATH%\private\deck.pptx", "Windows environment path"),
        ("$HOME/private/deck.pptx", "POSIX environment path"),
        ("${HOME}/private/deck.pptx", "POSIX environment path"),
        ("$TMPDIR/private/deck.pptx", "POSIX environment path"),
        (r"$env:USERPROFILE\private\deck.pptx", "Windows environment path"),
        (r"https://example.invalid\Users\alice\private-client.pptx", "Windows rooted path"),
        ("//Users/alice/private-client.pptx", "macOS absolute user path"),
        (r"\Device\HarddiskVolume4\Users\alice\private-client.pptx", "Windows native device path"),
        (r"\??\C:\Users\alice\private-client.pptx", "Windows native device path"),
        ("file:%2F%2F%2FUsers%2Falice%2Fprivate-client.pptx", "file URI"),
        ("C:%5CUsers%5Calice%5Cprivate-client.pptx", "Windows absolute path"),
        ("%2FUsers%2Falice%2Fprivate-client.pptx", "macOS absolute user path"),
        ("work%2F37", "task Work path"),
        ("[work/37]", "task Work path"),
        ("`work/37`", "task Work path"),
        ("work/37.", "task Work path"),
        ("foo/work/37", "task Work path"),
        ("jobs%2Farchive", "legacy job path"),
        ("outputs%2Fdeck.pptx", "local output path"),
        ("node_modules%2Fdependency", "node dependency path"),
        (".venv%2Fbin", "Python environment path"),
        (".tools%2Fcache", "local tool path"),
        ("../private-client/deck.pptx", "relative local path"),
        ("./private-client/deck.pptx", "relative local path"),
        ("`../private-client/deck.pptx`", "relative local path"),
        ("[./private-client/deck.pptx]", "relative local path"),
        ("{../private-client/deck.pptx}", "relative local path"),
        ("path=./private-client/deck.pptx", "relative local path"),
        ("source:../private-client/deck.pptx", "relative local path"),
        ("/Us<span>ers</span>/alice/private-client.pptx", "macOS absolute user path"),
        ("/Us**ers**/alice/private-client.pptx", "macOS absolute user path"),
        ("/Us\u200bers/alice/private-client.pptx", "macOS absolute user path"),
        ("/Us\u180eers/alice/private-client.pptx", "macOS absolute user path"),
        ("fi\u200ble:///Users/alice/private-client.pptx", "file URI"),
        ("／Ｕｓｅｒｓ／alice／private-client．pptx", "macOS absolute user path"),
        ("%EF%BC%8FUsers%EF%BC%8Falice%EF%BC%8Fprivate-client.pptx", "macOS absolute user path"),
        ("work<span>/</span>37/workspace", "task Work path"),
        ("wo**rk**/37/workspace", "task Work path"),
        ("work\u200b/37/workspace", "task Work path"),
        ("ｗｏｒｋ／３７／ｗｏｒｋｓｐａｃｅ", "task Work path"),
        ("Set-Cookie: session=abcdefghijklmnopqrstuvwxyz0123456789", "credential value"),
        ("Cookie: JSESSIONID=secretvalue", "credential value"),
        ("Set-Cookie: csrf=secretvalue; Path=/", "credential value"),
        ("Cookie<span>:</span>sid=secretvalue", "credential value"),
        ("Cookie\u00a0:\u00a0JSESSIONID=secretvalue", "credential value"),
        ("Bearer\u00a0secretvalue", "credential value"),
        ("session\u00a0=\u00a0secretvalue", "credential value"),
        ("Cookie\u200e:\u200eJSESSIONID=secretvalue", "credential value"),
        ("Bearer\u200esecretvalue", "credential value"),
        ("https://token:abcdefghijklmnop@localhost/api", "credential value"),
        ("gho_abcdefghijklmnopqrstuvwxyz0123456789", "credential value"),
        ("xoxb-abcdefghijklmnopqrstuvwxyz0123456789", "credential value"),
        ("AIzaabcdefghijklmnopqrstuvwxyz0123456789", "credential value"),
        ("-----BEGIN PRIVATE KEY-----", "credential value"),
        ("13800138000", "personal contact"),
        ("tel:+442079460958", "personal contact"),
        ("+44 20 7946 0958", "personal contact"),
        ("+86 138 0013 8000", "personal contact"),
        ("+86\u00a0138\u00a01234\u00a05678", "personal contact"),
        ("+86\u2011138\u20111234\u20115678", "personal contact"),
        ("＋８６ １３８ １２３４ ５６７８", "personal contact"),
        ("138-0013-8000", "personal contact"),
        ("(212) 555-1234", "personal contact"),
        ("client%40private%2Eexample", "personal contact"),
        ("alice<span>@</span>example.com", "personal contact"),
        ("alice**@**example.com", "personal contact"),
        ("alice\u200b@example.com", "personal contact"),
        ("alice\u2066@example.com", "personal contact"),
        ("alice\u180e@example.com", "personal contact"),
        ("alice\u034f@example.com", "personal contact"),
        ("alice\ufe0f@example.com", "personal contact"),
        ("alice\U000e0001@example.com", "personal contact"),
        (r"\u002fUsers\u002falice\u002fprivate-client.pptx", "macOS absolute user path"),
        (r"\u002e\u002e\u002fprivate-client\u002fdeck.pptx", "relative local path"),
        (r"work\u002f37", "task Work path"),
        (r"\x2fUsers\x2falice\x2fprivate-client.pptx", "macOS absolute user path"),
    ],
)
def test_portable_skill_rejects_credentials_and_personal_contacts(tmp_path: Path, addition: str, match: str):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    skill_md = candidate / "SKILL.md"
    skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\n" + addition + "\n", encoding="utf-8")

    with pytest.raises(module.PortableSkillError, match=match):
        module.validate_skill_source(candidate)


@pytest.mark.parametrize(
    "addition",
    [
        "https://docs.example.com/contact/alice@example.com",
        "https://docs.example.com/contact/alice%40example.com",
        "https://docs.example.com/contact/%2B8613812345678",
        "https://docs.example.com/contact/Cookie:SID=secret",
        "https://docs.example.com/contact/Set-Cookie:SID=secret;Path=/",
        "https://docs.example.com/contact/Bearer%20secretvalue",
        "https://docs.example.com/contact/data%3Atext/plain;base64,c2VjcmV0",
        "https://docs.example.com/?contact=alice@example.com",
        "https://docs.example.com/#contact=13800138000",
        "alice@example.中国",
        "alice@例子.中国",
        "alice@exämple.com",
    ],
)
def test_portable_skill_rejects_private_remote_paths_and_internationalized_contacts(
    tmp_path: Path, addition: str
):
    """The copied upload package shares the runtime privacy boundary."""

    module = _load_module()
    candidate = _copy_skill(tmp_path)
    skill_md = candidate / "SKILL.md"
    skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\n" + addition + "\n", encoding="utf-8")

    with pytest.raises(module.PortableSkillError, match="credential value|personal contact|embedded data URI"):
        module.validate_skill_source(candidate)


@pytest.mark.parametrize(
    ("addition", "match"),
    [
        ("∕Users∕alice∕private-client.pptx", "macOS absolute user path"),
        ("⁄Users⁄alice⁄private-client.pptx", "macOS absolute user path"),
        ("⧸Users⧸alice⧸private-client.pptx", "macOS absolute user path"),
        ("∖Users∖alice∖private-client.pptx", "Windows rooted path"),
        ("⧵Users⧵alice⧵private-client.pptx", "Windows rooted path"),
        ("work∕37∕workspace", "task Work path"),
    ],
)
def test_portable_skill_rejects_visual_path_separator_confusables(
    tmp_path: Path, addition: str, match: str
):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    skill_md = candidate / "SKILL.md"
    skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\n" + addition + "\n", encoding="utf-8")

    with pytest.raises(module.PortableSkillError, match=match):
        module.validate_skill_source(candidate)


@pytest.mark.parametrize(
    "addition",
    [
        "vscode:/Users/alice/private-client.pptx",
        "vscode-insiders:/work/37/workspace",
        "https:/Users/alice/private-client.pptx",
        "http:/Users/alice/private-client.pptx",
        "wss:/work/37/workspace",
        "https::/Users/alice/private-client.pptx",
        "Macintosh HD:Users:alice:private-client:deck.pptx",
        "Macintosh HD:work:37:workspace",
    ],
)
def test_portable_skill_rejects_local_application_uris_and_hfs_paths(tmp_path: Path, addition: str):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    skill_md = candidate / "SKILL.md"
    skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\n" + addition + "\n", encoding="utf-8")

    with pytest.raises(module.PortableSkillError, match="path"):
        module.validate_skill_source(candidate)


@pytest.mark.parametrize(
    "addition",
    [
        "!binary L1VzZXJzL21hYy9wcml2YXRlL2RlY2sucHB0eA==",
        "!vault L1VzZXJzL21hYy9wcml2YXRlL2RlY2sucHB0eA==",
        "config: !vault L1VzZXJzL21hYy9wcml2YXRlL2RlY2sucHB0eA==",
    ],
)
def test_portable_skill_rejects_encoded_unknown_yaml_tag_payloads(tmp_path: Path, addition: str):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    skill_md = candidate / "SKILL.md"
    skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\n" + addition + "\n", encoding="utf-8")

    with pytest.raises(module.PortableSkillError, match="credential value"):
        module.validate_skill_source(candidate)


@pytest.mark.parametrize(
    "addition",
    [
        "```yaml\ntoken:\n  default: credentialvalue123\n```",
        "```yaml\napi_key: {examples: [credentialvalue123]}\n```",
        "```yaml\npassword: {const: credentialvalue123}\n```",
        "```yaml\ntoken: {x-private-value: credentialvalue123}\n```",
        "```yaml\ntoken: {properties: {api_key: {default: credentialvalue123}}}\n```",
    ],
)
def test_portable_skill_rejects_concrete_values_hidden_in_sensitive_schema_descriptors(
    tmp_path: Path, addition: str
):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    skill_md = candidate / "SKILL.md"
    skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\n" + addition + "\n", encoding="utf-8")

    with pytest.raises(module.PortableSkillError, match="credential value"):
        module.validate_skill_source(candidate)


@pytest.mark.parametrize(
    ("addition", "match"),
    [
        (r"\client-secret\deck.pptx", "Windows rooted path"),
        (r"\Documents and Settings\alice\deck.pptx", "Windows rooted path"),
        (r"${Env:APPDATA}\private\deck.pptx", "Windows environment path"),
        (r"$env:ProgramFiles(x86)\private\deck.pptx", "Windows environment path"),
        (r"%CD%\client-secret.pptx", "Windows environment path"),
        (r"%PUBLIC%\secret.pptx", "Windows environment path"),
        (r"%ALLUSERSPROFILE%\secret.pptx", "Windows environment path"),
        (r"%HOMESHARE%\secret.pptx", "Windows environment path"),
        (r"%__APPDIR__%\secret.pptx", "Windows environment path"),
        (r"$env:CD\secret.pptx", "Windows environment path"),
        (r"${env:CD}\secret.pptx", "Windows environment path"),
        (r"$env:HOME\secret.pptx", "Windows environment path"),
        (r"$env:PUBLIC\secret.pptx", "Windows environment path"),
        ("$OLDPWD/private/deck.pptx", "POSIX environment path"),
        ("${OLDPWD}/private/deck.pptx", "POSIX environment path"),
        ("$XDG_RUNTIME_DIR/private/deck.pptx", "POSIX environment path"),
        (r"\secret.pptx", "Windows rooted path"),
        (r"\private-client.pptx", "Windows rooted path"),
        (r"\Users", "Windows rooted path"),
        (r"\Windows", "Windows rooted path"),
        (r"\Documents", "Windows rooted path"),
        (r"\private", "Windows rooted path"),
        ("~+/private/deck.pptx", "tilde local path"),
        ("~-/private/deck.pptx", "tilde local path"),
        ("/c/client-a/private-deck.pptx", "Windows POSIX drive path"),
        ("/d/client-b/secret.pptx", "Windows POSIX drive path"),
        ("/D/work/37/secret.pptx", "Windows POSIX drive path"),
        ("/e/Users/alice/private.pptx", "Windows POSIX drive path"),
        ("/c/Program Files (x86)/App/app.exe", "Windows POSIX drive path"),
        ("/cygdrive/c/client-a/private-deck.pptx", "Cygwin drive path"),
        ("C:private.pptx", "Windows drive-relative path"),
        ("C:client-secret", "Windows drive-relative path"),
        ("file:C:private.pptx", "file URI"),
        ("%2Fc%2Fclient-a%2Fprivate-deck.pptx", "Windows POSIX drive path"),
        ("&#x2F;cygdrive&#x2F;c&#x2F;client-a&#x2F;private-deck.pptx", "Cygwin drive path"),
        ("https://alice:pw@localhost/private-preview", "credential value"),
        ("https://alice:a@127.0.0.1/x", "credential value"),
        ("https://alice:pw@[::1]/x", "credential value"),
        ("https://alice@localhost/x", "credential value"),
        ("Bearer x", "credential value"),
        ("Bearer%20x", "credential value"),
        ("Bearer&#32;x", "credential value"),
        ("Bearer 'x'", "credential value"),
        ('Bearer "x"', "credential value"),
        ("Set-Cookie: session=x", "credential value"),
        ("Cookie: token=x", "credential value"),
        ("session=x", "credential value"),
        ("session_id=x", "credential value"),
        ("session='x'", "credential value"),
        ('session="x"', "credential value"),
        ('token: "x"', "credential value"),
        ('password: "x"', "credential value"),
        ('access_token: "x"', "credential value"),
        ('api_key: "x"', "credential value"),
        ('authorization: "x"', "credential value"),
        ("token: &value x", "credential value"),
        ("session: &value x", "credential value"),
        ("password: !!str x", "credential value"),
        ("'token': &value x", "credential value"),
        ("token: |\n  x", "credential value"),
        ("session: >\n  x", "credential value"),
        ("Bearer &value x", "credential value"),
        ("value: &value x\ntoken: *value", "credential value"),
        ("value: &value >\n  x\ntoken: *value", "credential value"),
        ("value: &value |\n  x\nAuthorization: *value", "credential value"),
        ("credential: x", "credential value"),
        ("credentials: x", "credential value"),
        ("private_key: x", "credential value"),
        ("privateKey: x", "credential value"),
        ("secret_key: x", "credential value"),
        ("authToken: x", "credential value"),
        ("bearer: x", "credential value"),
        ("https://docs.example.com/?session=x", "credential value"),
        ("https://docs.example.com/#token=x", "credential value"),
        ("https://example.com/token=x", "credential value"),
        ("https://example.com/password=secret", "credential value"),
        ("https://example.com/api_key=x", "credential value"),
        ("https://example.com/session/x", "credential value"),
        ("https%3A%2F%2Falice%3Apw%40localhost%2Fx", "credential value"),
        ('{"session":"x"}', "credential value"),
    ],
)
def test_portable_skill_rejects_cross_platform_local_and_short_credential_forms(
    tmp_path: Path, addition: str, match: str
):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    skill_md = candidate / "SKILL.md"
    skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\n" + addition + "\n", encoding="utf-8")

    with pytest.raises(module.PortableSkillError, match=match):
        module.validate_skill_source(candidate)


@pytest.mark.parametrize(
    "addition",
    [
        "/a/b is a documented relative API route.",
        "/v1/users is a documented endpoint.",
        "https://a:80/path",
        "https://docs.example.com/c/Users/alice/reference",
        "https://docs.example.com/cygdrive/c/Users/alice/reference",
        "https://docs.example.com/session=overview",
        "https://docs.example.com/token=release-notes-2026-with-long-title",
        "https://docs.example.com/api_key=public-api-docs-v2",
        "https://docs.example.com/token/overview",
        "https://docs.example.com/guides/bearer-token",
        "https://docs.example.com/user@guide",
        "https://docs.example.com/reference/contact-routing",
        "https://docs.example.com/2026/1380013800-guide",
        "https:∕∕docs.example.com∕c∕Users∕alice",
        "!vault ordinary-prose",
        "https://docs.example.com?topic=foo@bar",
        "https://docs.example.com#topic=foo@bar",
        "Cookie handling is outside this workflow.",
        "Record whether the host session is available.",
        "Authorization is required before transmission.",
        "A bearer token is never requested.",
        "Bearer authentication may be unavailable.",
        "Bearer scheme review is complete.",
        "Bearer credentials are never requested.",
        "token=<token>",
        "Bearer <token>",
        "Figure A:2024 results.",
        "Slide B:3 summarizes the evidence.",
        "The ratio is A:B.",
        "ISBN A:123456 is a placeholder label.",
    ],
)
def test_portable_skill_allows_public_url_paths_and_plain_security_prose(tmp_path: Path, addition: str):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    skill_md = candidate / "SKILL.md"
    skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\n" + addition + "\n", encoding="utf-8")

    assert module.validate_skill_source(candidate)


def test_portable_skill_rejects_duplicate_json_keys_in_a_public_handoff_example(tmp_path: Path):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    handoff = candidate / "references" / "handoff-schemas.md"
    content = handoff.read_text(encoding="utf-8")
    original = '  "packet_id": "office-packet-001",'
    assert original in content
    handoff.write_text(
        content.replace(original, original + '\n  "packet_id": "office-packet-001",', 1),
        encoding="utf-8",
    )

    with pytest.raises(module.PortableSkillError, match="JSON example"):
        module.validate_skill_source(candidate)


def test_portable_skill_rejects_a_json_escaped_local_path_in_a_fenced_contract_example(
    tmp_path: Path,
):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    handoff = candidate / "references" / "handoff-schemas.md"
    content = handoff.read_text(encoding="utf-8")
    original = '      "active deck",'
    assert original in content
    handoff.write_text(
        content.replace(original, '      "\\/Users\\/alice\\/private.pptx",', 1),
        encoding="utf-8",
    )

    with pytest.raises(module.PortableSkillError, match="macOS absolute user path"):
        module.validate_skill_source(candidate)


@pytest.mark.parametrize(
    ("replacement", "match"),
    [
        ('"\\/Users\\/alice\\/private.pptx"', "macOS absolute user path"),
        ('"C:\\\\Users\\\\alice\\\\private.pptx"', "Windows absolute path"),
    ],
)
def test_portable_skill_rejects_a_json_escaped_local_path_in_manifest(
    tmp_path: Path, replacement: str, match: str
):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    manifest = candidate / "references" / "release-manifest.json"
    content = manifest.read_text(encoding="utf-8")
    original = '"source_report_bundle_only_manual_prepared_or_authorized_evidenced_direct_transmitted"'
    assert original in content
    manifest.write_text(content.replace(original, replacement, 1), encoding="utf-8")

    with pytest.raises(module.PortableSkillError, match=match):
        module.validate_skill_source(candidate)


@pytest.mark.parametrize("escape", [r"\x2f", r"\u002f"])
def test_portable_skill_rejects_a_yaml_escaped_local_path_in_openai_manifest(
    tmp_path: Path, escape: str
):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    app_manifest = candidate / "agents" / "openai.yaml"
    content = app_manifest.read_text(encoding="utf-8")
    prompt_lines = [line for line in content.splitlines() if line.startswith("  default_prompt: ")]
    assert len(prompt_lines) == 1
    original = prompt_lines[0]
    app_manifest.write_text(
        content.replace(
            original,
            f'  default_prompt: "{escape}Users{escape}alice{escape}private.pptx"',
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(module.PortableSkillError, match="macOS absolute user path"):
        module.validate_skill_source(candidate)


@pytest.mark.parametrize(
    ("addition", "match"),
    [
        ("&#x2F;Users&#x2F;alice&#x2F;private.pptx", "macOS absolute user path"),
        ("client&#64;private&#46;example", "personal contact"),
        ("&#46;&#46;&#x2F;private-client&#x2F;deck.pptx", "relative local path"),
    ],
)
def test_portable_skill_rejects_html_character_reference_disguises(
    tmp_path: Path, addition: str, match: str
):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    skill_md = candidate / "SKILL.md"
    skill_md.write_text(
        skill_md.read_text(encoding="utf-8") + "\n" + addition + "\n", encoding="utf-8"
    )

    with pytest.raises(module.PortableSkillError, match=match):
        module.validate_skill_source(candidate)


def test_portable_skill_rejects_a_yaml_escaped_local_path_in_skill_frontmatter(tmp_path: Path):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    skill_md = candidate / "SKILL.md"
    content = skill_md.read_text(encoding="utf-8")
    description_lines = [line for line in content.splitlines() if line.startswith("description: ")]
    assert len(description_lines) == 1
    original = description_lines[0]
    skill_md.write_text(
        content.replace(original, r'description: "\x2fUsers\x2falice\x2fprivate.pptx"', 1),
        encoding="utf-8",
    )

    with pytest.raises(module.PortableSkillError, match="macOS absolute user path"):
        module.validate_skill_source(candidate)


@pytest.mark.parametrize(
    "entry",
    [
        "token: &value x",
        "session: &value x",
        "password: !!str x",
        "api_key: |\n  x",
        "authorization: >\n  x",
        "'token': &value x",
        "value: &value x\ntoken: *value",
        "value: &value >\n  x\ntoken: *value",
        "value: &value |\n  x\nAuthorization: *value",
        "credential: x",
        "credentials: x",
        "private_key: x",
        "privateKey: x",
        "secret_key: x",
        "authToken: x",
        "bearer: x",
    ],
)
def test_portable_skill_rejects_decoded_sensitive_yaml_values_in_openai_manifest(
    tmp_path: Path, entry: str
):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    app_manifest = candidate / "agents" / "openai.yaml"
    app_manifest.write_text(
        app_manifest.read_text(encoding="utf-8") + "\n" + entry + "\n",
        encoding="utf-8",
    )

    with pytest.raises(module.PortableSkillError, match="credential value"):
        module.validate_skill_source(candidate)


@pytest.mark.parametrize(
    "entry",
    [
        "token: &value x",
        "session: &value x",
        "password: !!str x",
        "api_key: |\n  x",
        "authorization: >\n  x",
        "'token': &value x",
        "value: &value x\ntoken: *value",
        "value: &value >\n  x\ntoken: *value",
        "value: &value |\n  x\nAuthorization: *value",
        "credential: x",
        "credentials: x",
        "private_key: x",
        "privateKey: x",
        "secret_key: x",
        "authToken: x",
        "bearer: x",
    ],
)
def test_portable_skill_rejects_decoded_sensitive_yaml_values_in_skill_frontmatter(
    tmp_path: Path, entry: str
):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    skill_md = candidate / "SKILL.md"
    content = skill_md.read_text(encoding="utf-8")
    frontmatter_end = content.find("\n---\n")
    assert frontmatter_end > 0
    skill_md.write_text(
        content[:frontmatter_end] + "\n" + entry + content[frontmatter_end:],
        encoding="utf-8",
    )

    with pytest.raises(module.PortableSkillError, match="credential value"):
        module.validate_skill_source(candidate)


@pytest.mark.parametrize(
    "addition",
    [
        "session id: x",
        "https://example.com/?api+key=x",
        "aws_secret_access_key=x",
        "aws_session_token=x",
        "https://example.com/gho_abcdefghijklmnopqrstuvwxyz0123456789",
        "https://example.com/sk-abcdefghijklmnopqrstuvwxyz0123456789",
        "data:text/plain;base64,c2VjcmV0",
        "https://example.com/token=abcdefghijklmnopqrstuvwxyz0123456789",
        "https://example.com/secret=abcdefghijklmnopqrstuvwxyz0123456789",
        "https://example.com/api_key=abcdefghijklmnopqrstuvwxyz0123456789",
        "https://example.com/password=abcdefghijklmnopqrstuvwxyz0123456789",
        "https://example.com/authorization=Bearer-abcdefghijklmnopqrstuvwxyz0123456789",
        "https://docs.example.com/token=abcdefgh-ijklmnop-qrstuvwx",
        "https://docs.example.com/session/abcdefgh-ijklmnop-qrstuvwx",
        "https://example.com/access_token/abcdefghijklmnop-qrstuvwxyz012345",
        "https://example.com/secret/abc.defghijklmnopqrstuvwxyz0123456789",
        "https://example.com/session/abcdefghijklmno%2Dpqrstuvwxyz0123456789",
        "https://example.com/Basic/YWxpY2U6c2VjcmV0",
        "https://example.com/basic/YWxpY2U6c2VjcmV0",
        "https://example.com/Basic%2FYWxpY2U6c2VjcmV0",
        "https://docs.example.com/resource;token=secretvalue",
        "https://docs.example.com/resource;api_key=secretvalue",
        "https://docs.example.com/resource;token/secretvalue",
        "https://docs.example.com/resource%3Btoken%3Dsecretvalue",
        "https://docs.example.com/resource;basic/dXNlcjpwYXNz",
        "https://docs.example.com/;Basic/YWxpY2U6c2VjcmV0",
        "https://docs.example.com/?q=;Basic/YWxpY2U6c2VjcmV0",
        "https://docs.example.com/#q=;Basic/YWxpY2U6c2VjcmV0",
        "https://docs.example.com/?q=Basic/YWxpY2U6c2VjcmV0",
        "https://docs.example.com/#q=Basic/YWxpY2U6c2VjcmV0",
        "https://docs.example.com/?q=basic%2FYWxpY2U6c2VjcmV0",
        "Basic dXNlcjpwYXNz",
        "```yaml\nvalue: &value\n  x\ntoken: *value\n```",
        "```yaml\n{value: &value x, token: *value}\n```",
        "```yaml\nvalue: &value x\n? token\n: *value\n```",
        "```yaml\nvalue: &value x\n!!str token: *value\n```",
        "```yaml\ntoken: !<tag:yaml.org,2002:str> |\n  x\n```",
        "```yaml\n? token\n: |\n  x\n```",
        "```yaml\nvalue: &value x\ntoken: *value\ntoken: <token>\n```",
        "```yaml\n{value: &value x, token: *value, token: <token>}\n```",
        "```yaml\n---\nsafe: docs\n---\nvalue: &value x\ntoken: *value\n```",
        "```yaml\nvalue: &value !!binary eA==\ntoken: *value\n```",
        "```yaml\nvalue: &value !!timestamp 2026-01-01\ntoken: *value\n```",
        "```yaml\nvalue: &value !secret x\ntoken: *value\n```",
        "```yaml\n!!binary dG9rZW4=: x\n```",
        "```yaml\n? !!binary dG9rZW4=\n: x\n```",
        "```yaml\n!<tag:yaml.org,2002:binary> dG9rZW4=: x\n```",
        "```yaml\nkey: &k !!binary dG9rZW4=\n*k: x\n```",
        "> token: |\n>   secretvalue",
        "> value: &v secretvalue\n> token: *v",
        "&gt; token: |\n&gt;   secretvalue",
        "<p>token: |\n  secretvalue</p>",
        "authorization:\n  state: Basic YWxpY2U6c2VjcmV0\n  evidence: []",
        "authorization:\n  state: not_recorded\n  evidence: [Basic YWxpY2U6c2VjcmV0]",
        "config: |\n  value: &v secretvalue\n  token: *v",
        "config: >\n  value: &v secretvalue\n  token: *v",
        "config: |\n  ? !!str token\n  : secretvalue",
        '{"config":"value: &v secretvalue\\ntoken: *v"}',
        'config: "value: &v secretvalue\\ntoken: *v"',
        'outer: { config: "value: &v secretvalue\\ntoken: *v" }',
        "config: |\n  <span>value: &v secretvalue<br>token: *v</span>",
        "config: |\n  > value: &v secretvalue\n  > token: *v",
        "`{value: &v secretvalue, token: *v}`",
        "to**ken**: secretvalue",
        "to_ken_: secretvalue",
        "to`ken`: secretvalue",
        '<div data-config="{value: &v secretvalue, token: *v}">safe</div>',
        "? [token]\n: secretvalue",
        "# value: &v secretvalue\n# token: *v",
        "<!-- ? !!str token\n: secretvalue -->",
        "<!DOCTYPE x [ ? !!str token\n: secretvalue ]>",
        "# title\n" + ("ordinary prose line\n" * 129) + "\nvalue: &v secretvalue\ntoken: *v",
        "# title\nordinary prose without colon\n? &v secretvalue\n: x\n? token\n: *v",
        "````markdown\n# title\nordinary prose without colon\nvalue: &v secretvalue\ntoken: *v\n````",
        "~~~~~yaml\n# title\nordinary prose without colon\nvalue: &v secretvalue\ntoken: *v\n~~~~~",
        '<?config data="{value: &v secretvalue, token: *v}"?>',
        "!foo {value: &v secretvalue, token: *v}",
        "!foo\n- value: &v secretvalue\n- token: *v",
        "%TAG !e! tag:example.com,2026:\n---\n!e!foo {value: &v secretvalue, token: *v}",
        "# title\n!foo {value: &v secretvalue, token: *v}",
        "````yaml\n# title\n!foo {value: &v secretvalue, token: *v}\n````",
        "<span>notice</span>\n!foo {value: &v secretvalue, token: *v}",
        "> notice\n> !foo {value: &v secretvalue, token: *v}",
        "&root !foo {value: &v secretvalue, token: *v}",
        "- !foo {value: &v secretvalue, token: *v}",
        "- !foo\n  value: &v secretvalue\n  token: *v",
        "- &root !foo {value: &v secretvalue, token: *v}",
        "!!set {value: &v secretvalue, token: *v}",
        "!!omap [{value: &v secretvalue}, {token: *v}]",
        "!!pairs [{value: &v secretvalue}, {token: *v}]",
        "- !!set {value: &v secretvalue, token: *v}",
    ],
)
def test_portable_skill_rejects_semantic_yaml_and_remote_credential_bypasses(
    tmp_path: Path, addition: str
):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    skill_md = candidate / "SKILL.md"
    skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\n" + addition + "\n", encoding="utf-8")

    with pytest.raises(module.PortableSkillError, match="credential value|embedded data URI"):
        module.validate_skill_source(candidate)


@pytest.mark.parametrize(
    "addition",
    [
        "https://docs.example.com/token=overview",
        "https://docs.example.com/session=overview",
        "https://docs.example.com/token=release-notes-2026-with-long-title",
        "https://docs.example.com/api_key=public-api-docs-v2",
        "https://docs.example.com/token/overview",
        "https://docs.example.com/resource;token=overview",
        "https://docs.example.com/resource;token/overview",
        "https://docs.example.com/Us\u200bers/reference",
        "```yaml\ntoken:\n  type: string\n```",
        "```yaml\ntoken: <token>\n```",
        "```yaml\ntoken: {type: string, default: <token>}\n```",
        "```yaml\napi_key: {examples: [<token>, redacted]}\n```",
        "```yaml\nauthorization: {properties: {state: {enum: [user_provided_current_task, authorized_direct_bridge]}}}\n```",
        "```yaml\nauthorization:\n  state: not_recorded\n  evidence: []\n```",
        "Basic authentication may be unavailable.",
        "Basic education",
        "```yaml\nconfig: |\n  token:\n    type: string\n```",
        "```yaml\nconfig: |\n  token: <token>\n```",
        "`token: <token>`",
        "# token: <token>",
    ],
)
def test_portable_skill_allows_public_routes_and_declarative_sensitive_word_controls(
    tmp_path: Path, addition: str
):
    module = _load_module()
    candidate = _copy_skill(tmp_path)
    skill_md = candidate / "SKILL.md"
    skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\n" + addition + "\n", encoding="utf-8")

    assert module.validate_skill_source(candidate)
