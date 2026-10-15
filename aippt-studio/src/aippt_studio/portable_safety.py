"""Shared portable-boundary detectors.

POSIX detection intentionally recognizes well-known filesystem roots so an
ordinary slash-prefixed word does not become a false positive. Windows has
additional unambiguous forms (a drive prefix, a UNC path, or a current-drive
rooted backslash path), which are recognized independently. The caller still
applies context-specific checks for file URIs, task folders, credentials, and
contacts.
"""
from __future__ import annotations

import base64
import html
import ipaddress
import re
from urllib.parse import unquote, urlsplit

import yaml

# Valid remote URL paths receive their exception in ``portable_path_scan_variants``
# before these patterns run. A bare colon is not proof of a valid remote URL:
# treating it as one would let ``vscode:/Users/...`` and malformed
# ``https:/Users/...`` spellings hide a local path. Keep this source string
# reusable by the upload packager so its release scan and receiving validator
# cannot silently drift apart.
PORTABLE_POSIX_LOCAL_PATH_PATTERN = (
    r"(?<![/A-Za-z0-9+._-])/{1,2}"
    r"(?:Users|Volumes|home|private|tmp|var|etc|opt|Applications|Library|System|"
    r"bin|sbin|root|mnt|srv|data|workspace|usr|run|proc|dev|boot|media|snap)"
    r"(?:[\\/]|$)"
)

# ``file:/...`` is a local-file URI even with a single slash. Named homes,
# including zsh/Bash current- and previous-directory forms (``~+/...`` and
# ``~-/...``), and Windows/UNC forms are kept separate because they are useful
# outside POSIX prose too.
# Every ``file:`` spelling crosses the local-file boundary. In particular,
# ``file:C:deck.pptx`` is a legal relative/Windows form even though it lacks
# the familiar ``file:///`` slashes.
PORTABLE_FILE_URI_PATTERN = re.compile(r"\bfile:", re.IGNORECASE)
PORTABLE_NETWORK_FILE_URI_PATTERN = re.compile(r"\b(?:smb|nfs|afp)://", re.IGNORECASE)
PORTABLE_HOME_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9._-])~(?:[+-]|[A-Za-z0-9._-]+)?[\\/]", re.MULTILINE
)
# A backslash UNC path often follows a JSON/Markdown label such as
# ``source:\\\\server\\share``.  Its colon is ordinary punctuation and must not
# suppress detection.  A forward-slash form needs the stricter boundary so the
# ``//host/`` portion of a normal ``https://host/`` URL remains portable.
PORTABLE_UNC_PATH_PATTERN = re.compile(
    r"(?:"
    r"(?<![:/A-Za-z0-9._-])//[^\\/\s]+[\\/]"
    r"|"
    r"(?<![A-Za-z0-9._-])\\\\[^\\/\s]+[\\/]"
    r")",
    re.MULTILINE,
)
PORTABLE_WINDOWS_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")
_PORTABLE_LOCAL_ARTIFACT_EXTENSION = (
    r"(?:pptx?|potx?|pdf|docx?|xlsx?|csv|png|jpe?g|gif|svg|mp4|mov|txt|md|py|js|ts|ps1|sh)"
)
_PORTABLE_WINDOWS_DRIVE_EVIDENCE = (
    r"(?:Users|Documents(?:[ _-]+and[ _-]+Settings)?|Desktop|Downloads|"
    r"OneDrive(?:[ _-]+[^/\\\r\n]+)?|Program[ _-]+Files(?:[ _-]+\(x86\))?|"
    r"AppData|Windows|ProgramData|work(?=[\\/][0-9]+(?:[\\/]|$))|workspace|"
    r"jobs?|outputs?|node_modules|\.venv(?:[-\w.]*)?|\.tools|"
    r"(?:client|private|secret)(?:[-_.][^/\\\s]+)?)"
)
# ``C:folder`` is drive-relative, but a one-letter label such as ``A:2024``
# is normal slide prose. Require a Windows/Studio root, traversal, separator,
# or local artifact shape rather than treating every ``<letter>:<text>`` as a
# path. Full ``C:\\...`` paths are covered by the absolute-path pattern above.
PORTABLE_WINDOWS_DRIVE_RELATIVE_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]:"
    r"(?=(?:[\\/]|\.{1,2}[\\/]|"
    rf"{_PORTABLE_WINDOWS_DRIVE_EVIDENCE}(?:[\\/]|$)|"
    rf"(?:[^/\\\s]+[\\/])*[^/\\\s]+\.{_PORTABLE_LOCAL_ARTIFACT_EXTENSION}(?:[\\/]|$)))",
    re.IGNORECASE,
)
PORTABLE_WINDOWS_NATIVE_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9._-])\\(?:Device|GLOBAL\?\?|SystemRoot|\?\?)[\\/]",
    re.IGNORECASE,
)
# A single leading backslash is rooted at the current Windows drive. Unlike a
# generic POSIX slash it is an unambiguous path form, so inspect every first
# component rather than only a hand-maintained list of familiar directories.
# Keep arbitrary lone escapes (for example TeX ``\alpha``) usable, but a bare
# recognizable system/user/Studio root such as ``\Users`` or ``\private`` is
# still a local location disclosure even when it has no trailing separator.
# The negative lookbehind leaves an initial UNC ``\\server`` form to the
# dedicated UNC detector below.
_PORTABLE_WINDOWS_ROOTED_DIRECTORY_TOKEN = (
    r"(?:Users|Documents(?:[ _-]+and[ _-]+Settings)?|Desktop|Downloads|"
    r"OneDrive(?:[ _-]+[^/\\\r\n]+)?|Program[ _-]+Files(?:[ _-]+\(x86\))?|"
    r"AppData|Windows|ProgramData|work|workspace|jobs?|outputs?|node_modules|"
    r"\.venv(?:[-\w.]*)?|\.tools|client|private|secret)"
)
PORTABLE_WINDOWS_ROOTED_PATH_PATTERN = re.compile(
    rf"(?<!\\)\\(?:{_PORTABLE_WINDOWS_ROOTED_DIRECTORY_TOKEN}(?=$|[\s,.;:)\]}}])|"
    r"[^\\/\s][^\\/\r\n]*[\\/]|"
    rf"[^\\/\s]+\.{_PORTABLE_LOCAL_ARTIFACT_EXTENSION}(?=$|[\s,.;:)\]}}]))",
    re.MULTILINE | re.IGNORECASE,
)
# Git Bash/MSYS can expose any Windows volume as ``/<letter>/...``. A bare
# two-segment token such as ``/a/b`` is also a normal API route, so the MSYS
# rule needs evidence from a Windows/user/Studio root or a local artifact name
# instead of rejecting every single-letter segment. Cygwin's unambiguous
# ``/cygdrive/<letter>/...`` form is handled separately. Keep the leading
# boundary URL-safe; a valid remote web URL path is masked before this detector
# runs, while query and fragment disclosures remain visible.
_PORTABLE_WINDOWS_POSIX_DRIVE_EVIDENCE = (
    rf"(?:{_PORTABLE_WINDOWS_DRIVE_EVIDENCE}|"
    rf"[^/\\\s]+\.{_PORTABLE_LOCAL_ARTIFACT_EXTENSION})"
)
PORTABLE_WINDOWS_POSIX_DRIVE_PATH_PATTERN = re.compile(
    rf"(?<![/A-Za-z0-9+._-])/[A-Za-z]/(?:[^/\\\s]+[\\/])*"
    rf"{_PORTABLE_WINDOWS_POSIX_DRIVE_EVIDENCE}(?:[\\/]|$)",
    re.IGNORECASE,
)
PORTABLE_CYGDRIVE_PATH_PATTERN = re.compile(
    r"(?<![/A-Za-z0-9+._-])/cygdrive/[A-Za-z](?:[\\/]|$)", re.IGNORECASE
)
PORTABLE_WINDOWS_ENV_PATH_PATTERN = re.compile(
    r"(?:%(?:USERPROFILE|APPDATA|LOCALAPPDATA|PROGRAMDATA|TEMP|TMP|WINDIR|SYSTEMROOT|"
    r"HOMEDRIVE|HOMEPATH|PROGRAMFILES(?:\(X86\))?|CD|PUBLIC|ALLUSERSPROFILE|HOMESHARE|__APPDIR__)%(?=[\\/])|"
    r"%HOMEDRIVE%%HOMEPATH%(?=[\\/]))",
    re.IGNORECASE,
)
PORTABLE_POSIX_ENV_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:\$(?:HOME|OLDPWD|TMPDIR|PWD|XDG_(?:CONFIG|CACHE|DATA)_HOME|XDG_RUNTIME_DIR)|"
    r"\$\{(?:HOME|OLDPWD|TMPDIR|PWD|XDG_(?:CONFIG|CACHE|DATA)_HOME|XDG_RUNTIME_DIR)\})(?=[\\/])",
    re.IGNORECASE,
)
_PORTABLE_POWERSHELL_ENV_NAME = (
    r"(?:USERPROFILE|APPDATA|LOCALAPPDATA|PROGRAMDATA|TEMP|TMP|WINDIR|SYSTEMROOT|"
    r"HOMEDRIVE|HOMEPATH|PROGRAMFILES(?:\(X86\))?|CD|HOME|PUBLIC|ALLUSERSPROFILE|HOMESHARE|__APPDIR__)"
)
PORTABLE_POWERSHELL_ENV_PATH_PATTERN = re.compile(
    rf"(?i:(?:\$env:{_PORTABLE_POWERSHELL_ENV_NAME}(?=[\\/])|"
    rf"\$\{{env:{_PORTABLE_POWERSHELL_ENV_NAME}\}}(?=[\\/])))"
)
# The local-reference scan treats a web URL's *path* as remote naming data,
# while leaving its query and fragment visible.  That prevents legitimate URLs
# such as ``https://example.test/assets//Users/logo.png`` from being mistaken
# for a local path, without allowing ``?source=/Users/alice/deck.pptx`` to
# hide a local reference inside a remote URL.
PORTABLE_REMOTE_WEB_URL_PATTERN = re.compile(
    r"\b(?:https?|wss?)://(?=[^/?#\s<>'\"`])[^\s<>'\"`]+", re.IGNORECASE
)
PORTABLE_MALFORMED_WEB_LOCAL_PATH_PATTERN = re.compile(
    r"\b(?:https?|wss?):/{3,}"
    r"(?:Users|Volumes|home|private|tmp|var|etc|opt|Applications|Library|System|"
    r"bin|sbin|root|mnt|srv|data|workspace|usr|run|proc|dev|boot|media|snap)"
    r"(?:[\\/]|$)",
    re.IGNORECASE,
)
PORTABLE_MALFORMED_WEB_UNC_PATH_PATTERN = re.compile(
    r"\b(?:https?|wss?):/{3,}[^/\s]+/", re.IGNORECASE
)
_PORTABLE_BACKSLASH_ESCAPE_PATTERN = re.compile(
    r"(?<!\\)\\(?:u(?P<u>[0-9A-Fa-f]{4})|U(?P<U>[0-9A-Fa-f]{8})|x(?P<x>[0-9A-Fa-f]{2}))"
)
PORTABLE_POSIX_LOCAL_PATH_RE = re.compile(PORTABLE_POSIX_LOCAL_PATH_PATTERN, re.IGNORECASE)
# Keep macOS-specific diagnostics inside the shared detector list rather than
# letting the upload packager add a broader ``/Users/`` shortcut.  The same
# URL-safe token boundary is essential here: a remote URL may legally use
# ``//Users/`` in its path.
PORTABLE_MACOS_USER_PATH_PATTERN = re.compile(
    r"(?<![/A-Za-z0-9+._-])/{1,2}Users(?:[\\/]|$)", re.IGNORECASE
)
PORTABLE_MACOS_VOLUME_PATH_PATTERN = re.compile(
    r"(?<![/A-Za-z0-9+._-])/{1,2}Volumes(?:[\\/]|$)", re.IGNORECASE
)
# Legacy HFS/AppleScript paths use colon components instead of slashes. Keep
# this deliberately narrow to Apple's default startup-volume spelling, rather
# than treating ordinary prose containing arbitrary colons as a local path.
PORTABLE_MACOS_HFS_LOCAL_PATH_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9._-])Macintosh HD:"
    r"(?:Users|Volumes|private|tmp|var|etc|opt|Applications|Library|System):"
)
PORTABLE_MACOS_HFS_STUDIO_PATH_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9._-])Macintosh HD:"
    r"(?:work:[0-9]+|workspace|jobs?|outputs?|node_modules|\.venv(?:[-\w.]*)?|\.tools)(?::|$)"
)
# Relative traversal is still a location leak in a portable source bundle.
# Treat every token boundary as valid: packets and uploadable Markdown routinely
# place a path after a backtick, bracket, brace, colon, or JSON assignment.  A
# negative lookbehind still leaves ordinary dotted prose, versions, and an
# identifier such as ``word../suffix`` alone.
PORTABLE_RELATIVE_LOCAL_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9._-])\.{1,2}[\\/]", re.MULTILINE
)

# Use this one ordered collection wherever portable input crosses a boundary.
# The package builder preserves the labels for actionable diagnostics; runtime
# packet validation only needs the same yes/no decision.  Keeping both users
# on this tuple prevents their filesystem-path rules from drifting apart.
PORTABLE_LOCAL_REFERENCE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (PORTABLE_FILE_URI_PATTERN, "file URI"),
    (PORTABLE_NETWORK_FILE_URI_PATTERN, "network file URI"),
    (PORTABLE_WINDOWS_ENV_PATH_PATTERN, "Windows environment path"),
    (PORTABLE_POWERSHELL_ENV_PATH_PATTERN, "Windows environment path"),
    (PORTABLE_POSIX_ENV_PATH_PATTERN, "POSIX environment path"),
    (PORTABLE_MALFORMED_WEB_LOCAL_PATH_PATTERN, "malformed web URI carrying local path"),
    (PORTABLE_MALFORMED_WEB_UNC_PATH_PATTERN, "malformed web URI carrying UNC path"),
    (PORTABLE_MACOS_HFS_LOCAL_PATH_PATTERN, "macOS HFS local path"),
    (PORTABLE_MACOS_USER_PATH_PATTERN, "macOS absolute user path"),
    (PORTABLE_MACOS_VOLUME_PATH_PATTERN, "macOS volume path"),
    (PORTABLE_POSIX_LOCAL_PATH_RE, "POSIX local path"),
    (PORTABLE_RELATIVE_LOCAL_PATH_PATTERN, "relative local path"),
    (PORTABLE_HOME_PATH_PATTERN, "tilde local path"),
    (PORTABLE_WINDOWS_NATIVE_PATH_PATTERN, "Windows native device path"),
    (PORTABLE_WINDOWS_PATH_PATTERN, "Windows absolute path"),
    (PORTABLE_WINDOWS_DRIVE_RELATIVE_PATH_PATTERN, "Windows drive-relative path"),
    (PORTABLE_UNC_PATH_PATTERN, "UNC local path"),
    (PORTABLE_WINDOWS_ROOTED_PATH_PATTERN, "Windows rooted path"),
    (PORTABLE_WINDOWS_POSIX_DRIVE_PATH_PATTERN, "Windows POSIX drive path"),
    (PORTABLE_CYGDRIVE_PATH_PATTERN, "Cygwin drive path"),
)

# These are Studio-private identifiers and directory shapes, not portable
# presentation evidence. Like filesystem paths, they must be recognized after
# semantic decoding and at ordinary prose/Markdown token boundaries instead of
# only at a line start or after a slash. The second alternative in each tree
# rule still catches an absolute local tree while excluding URL path segments.
PORTABLE_STUDIO_REFERENCE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i:(?<![A-Za-z0-9._/-])(?:[A-Za-z0-9._-]+[\\/])*work[\\/][0-9]+"
            r"(?![A-Za-z0-9_:-])|"
            r"(?<![/A-Za-z0-9+._-])/(?:[A-Za-z0-9._-]+/)*work/[0-9]+"
            r"(?![A-Za-z0-9_:-]))"
        ),
        "task Work path",
    ),
    (
        re.compile(r"(?i:\b(?:work|task|job)\s*(?:#|no\.?\s*)?\d+\b)"),
        "task history identifier",
    ),
    (
        re.compile(
            r"(?i:(?<![A-Za-z0-9._/-])jobs(?:[\\/]|$)|"
            r"(?<![/A-Za-z0-9+._-])/jobs(?:/|$))"
        ),
        "legacy job path",
    ),
    (
        re.compile(
            r"(?i:(?<![A-Za-z0-9._/-])outputs?(?:[\\/]|$)|"
            r"(?<![/A-Za-z0-9+._-])/outputs?(?:/|$))"
        ),
        "local output path",
    ),
    (
        re.compile(
            r"(?i:(?<![A-Za-z0-9._/-])node_modules(?:[\\/]|$)|"
            r"(?<![/A-Za-z0-9+._-])/node_modules(?:/|$))"
        ),
        "node dependency path",
    ),
    (
        re.compile(
            r"(?i:(?<![A-Za-z0-9._/-])\.venv(?:[-\w.]*)?(?:[\\/]|$)|"
            r"(?<![/A-Za-z0-9+._-])/\.venv(?:[-\w.]*)?(?:/|$))"
        ),
        "Python environment path",
    ),
    (
        re.compile(
            r"(?i:(?<![A-Za-z0-9._/-])\.tools(?:[\\/]|$)|"
            r"(?<![/A-Za-z0-9+._-])/\.tools(?:/|$))"
        ),
        "local tool path",
    ),
    (PORTABLE_MACOS_HFS_STUDIO_PATH_PATTERN, "macOS HFS Studio path"),
    (re.compile(r"CODEX_THREAD_ID"), "local thread environment variable"),
    (re.compile(r"AIPPT_[A-Z0-9_]+"), "local Studio environment variable"),
)


class PortableTextDecodingError(ValueError):
    """Portable text remained encoded beyond the bounded semantic scan."""


def contains_portable_forbidden_control_character(text: str) -> bool:
    """Return whether portable text carries a non-text control character.

    Tabs and ordinary line endings are legitimate Markdown/YAML layout. Other
    C0 controls and DEL have no portable-skill or handoff meaning, can be
    represented differently by an upload service and a local parser, and must
    not cross either boundary. Callers also apply this to decoded text forms.
    """

    return any(
        (ord(character) < 0x20 and character not in {"\t", "\n", "\r"})
        or ord(character) == 0x7F
        for character in text
    )


def contains_portable_unpaired_surrogate(text: str) -> bool:
    """Return whether decoded portable text contains a non-UTF-8 surrogate."""

    return any(0xD800 <= ord(character) <= 0xDFFF for character in text)


def _decode_portable_backslash_escapes(text: str) -> str:
    """Decode only unambiguous single-backslash Unicode/hex character escapes."""

    def replace(match: re.Match[str]) -> str:
        hexadecimal = match.group("u") or match.group("U") or match.group("x")
        assert hexadecimal is not None
        codepoint = int(hexadecimal, 16)
        if codepoint == 0:
            raise PortableTextDecodingError("portable text decodes to a NUL character")
        # Do not manufacture unpaired surrogates from prose. They cannot carry
        # an ASCII path delimiter by themselves and should remain literal.
        if 0xD800 <= codepoint <= 0xDFFF or codepoint > 0x10FFFF:
            return match.group(0)
        return chr(codepoint)

    return _PORTABLE_BACKSLASH_ESCAPE_PATTERN.sub(replace, text)


def portable_text_variants(text: str) -> tuple[str, ...]:
    """Return raw and bounded HTML/percent-decoded forms of portable text.

    An uploaded or received source can represent the same visible path as JSON
    escapes, YAML escapes, raw Markdown-style ``\\u``/``\\x`` escapes, HTML
    character references, or percent-encoded URI data. JSON/YAML are decoded
    by their respective parsers before this helper; this function gives both
    the package gate and packet receiver an identical final text-normalization
    step. More than sixteen changing layers fail closed rather than leaving a
    deeper encoding as a bypass.
    """

    variants = [text]
    decoded = text
    for _ in range(16):
        decoded_next = html.unescape(unquote(_decode_portable_backslash_escapes(decoded)))
        if decoded_next == decoded:
            return tuple(variants)
        variants.append(decoded_next)
        decoded = decoded_next
    raise PortableTextDecodingError("portable text has excessively nested encodings")


def _mask_remote_web_url_paths(text: str, *, mask_authority: bool = False) -> str:
    """Replace selected non-query parts of absolute remote web URLs with spaces.

    This is deliberately a narrow lexical pass instead of a blanket URL
    exemption. A local reference in a query or fragment remains detectable.
    Local-path checks may also mask a valid remote authority so a one-letter
    hostname and port (``https://a:80``) is not mistaken for ``A:`` Windows
    drive syntax. Sensitive-value checks leave the authority visible so HTTP
    userinfo stays rejectable. Keeping positions and every query/fragment
    character intact makes the boundary patterns behave consistently on
    surrounding prose.
    """

    def url_end(start: int, candidate_end: int) -> int:
        """Trim unmatched Markdown-style closing delimiters from one URL."""

        matching_open = {")": "(", "]": "[", "}": "{"}
        stack: list[str] = []
        for index in range(start, candidate_end):
            character = text[index]
            if character in "([{":
                stack.append(character)
            elif character in matching_open:
                if not stack or stack[-1] != matching_open[character]:
                    # An outer Markdown/link/prose delimiter closes the URL.
                    # A matching delimiter within the URL itself remains valid.
                    return index
                stack.pop()
        return candidate_end

    def has_remote_host(token: str) -> bool:
        """Return whether a web token has a valid DNS, IPv4, or IPv6 host."""

        try:
            parsed = urlsplit(token)
            host = parsed.hostname
            # Accessing ``port`` also rejects malformed port strings.
            _ = parsed.port
        except ValueError:
            return False
        if not host or "%" in host:
            return False
        if ":" in host:
            try:
                ipaddress.IPv6Address(host)
            except ValueError:
                return False
            return True
        normalized = host[:-1] if host.endswith(".") else host
        if not normalized:
            return False
        try:
            normalized = normalized.encode("idna").decode("ascii")
        except UnicodeError:
            return False
        if len(normalized) > 253:
            return False
        label_pattern = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
        return all(label_pattern.fullmatch(label) is not None for label in normalized.split("."))

    characters = list(text)
    for match in PORTABLE_REMOTE_WEB_URL_PATTERN.finditer(text):
        token_end = url_end(match.start(), match.end())
        token = text[match.start():token_end]
        authority_start = token.find("://") + 3
        first_delimiter = len(token)
        for index in range(authority_start, len(token)):
            if token[index] in "/?#\\":
                first_delimiter = index
                break
        if first_delimiter == authority_start:
            # The regex already excludes this form, but retain a local defense
            # if its future grammar changes: an empty authority is not a
            # remote origin and must never hide a following local path.
            continue
        if not has_remote_host(token):
            # Do not give an invalid pseudo-URL an exemption. Separating only
            # its prefix lets the ordinary local-reference patterns inspect the
            # untouched path, query, and fragment that follow it.
            for index in range(match.start(), match.start() + first_delimiter):
                characters[index] = " "
            continue
        if first_delimiter == len(token) or token[first_delimiter] != "/":
            if mask_authority:
                for index in range(match.start(), match.start() + first_delimiter):
                    characters[index] = " "
            continue
        path_end = len(token)
        for index in range(first_delimiter, len(token)):
            if token[index] in "?#":
                path_end = index
                break
        mask_start = match.start() if mask_authority else match.start() + first_delimiter
        for index in range(mask_start, match.start() + path_end):
            characters[index] = " "
    return "".join(characters)


def portable_path_scan_variants(text: str) -> tuple[str, ...]:
    """Return decoded variants with valid remote web URL paths masked.

    A remote URL path is naming data supplied by a public origin. Query and
    fragment text deliberately remains visible because either can carry an
    accidental local reference or credential value.
    """

    # Prefer the deepest decoded representation for diagnostics. A raw
    # ``\u002fUsers`` spelling otherwise resembles a Windows rooted path
    # before its semantic macOS/POSIX path form gets a chance to be recognized.
    # The joined visual variant makes a literal local path or Work identifier
    # visible even if its characters were split by fullwidth glyphs or
    # default-ignorable Unicode formatting. Run URL masking after that fold so
    # a verified public URL path retains the same portable exception.
    presentation_variants = portable_presentation_scan_variants(text)
    path_variants = tuple(
        dict.fromkeys(
            normalized
            for candidate in presentation_variants
            for normalized in _portable_path_presentation_variants(candidate)
        )
    )
    return tuple(
        _mask_remote_web_url_paths(candidate, mask_authority=True)
        for candidate in reversed(path_variants)
    )


def portable_sensitive_scan_variants(text: str) -> tuple[str, ...]:
    """Return the privacy scan form without treating remote URL paths as secrets.

    URL authority, query, and fragment text remains visible. In particular,
    userinfo and signed query values still cross the privacy gate; only a
    verified remote path such as ``https://docs.example/path/token=overview``
    is excluded from credential-shape matching.
    """

    presentation_variants = portable_presentation_scan_variants(text)
    privacy_variants = tuple(
        dict.fromkeys(
            normalized
            for candidate in presentation_variants
            for normalized in _portable_privacy_presentation_variants(candidate)
        )
    )
    return tuple(
        _mask_remote_web_url_paths(candidate, mask_authority=False)
        for candidate in reversed(privacy_variants)
    )


def contains_portable_local_reference(text: str) -> bool:
    """Return whether decoded portable text discloses a local file reference."""

    try:
        variants = portable_path_scan_variants(text)
    except PortableTextDecodingError:
        return True
    return any(
        pattern.search(candidate)
        for candidate in variants
        for pattern, _label in PORTABLE_LOCAL_REFERENCE_PATTERNS
    )


def contains_portable_studio_reference(text: str) -> bool:
    """Return whether decoded text leaks a Studio-private task/runtime reference."""

    try:
        variants = portable_path_scan_variants(text)
    except PortableTextDecodingError:
        return True
    return any(
        pattern.search(candidate)
        for candidate in variants
        for pattern, _label in PORTABLE_STUDIO_REFERENCE_PATTERNS
    )


# The portable contract excludes a credential carrier even when its apparent
# value is short. An external packet or upload skill has no need to preserve
# ``session=x`` or HTTP userinfo, and a length threshold would make exactly
# those compact forms a bypass. The expressions require a concrete assignment,
# header, userinfo, query/fragment key, or Bearer value, so plain prose such as
# “session is available” remains valid. Remote URL *paths* are masked by the
# scan helper below; authorities, queries, and fragments are not.
#
# A YAML tag or anchor does not make a scalar safe.  This lexical form is used
# for free-form evidence text (which is intentionally not parsed as YAML), and
# the mapping helpers below apply the same rule after JSON/YAML decoding.
_PORTABLE_SENSITIVE_NAME_SEPARATOR = r"[-_ +]?"
_PORTABLE_SENSITIVE_SETTING_NAME = (
    rf"(?:authorization|api{_PORTABLE_SENSITIVE_NAME_SEPARATOR}key|"
    rf"x{_PORTABLE_SENSITIVE_NAME_SEPARATOR}api{_PORTABLE_SENSITIVE_NAME_SEPARATOR}key|"
    rf"client{_PORTABLE_SENSITIVE_NAME_SEPARATOR}secret|"
    rf"access{_PORTABLE_SENSITIVE_NAME_SEPARATOR}token|"
    rf"refresh{_PORTABLE_SENSITIVE_NAME_SEPARATOR}token|password|passwd|secret|token|"
    rf"auth(?:orization)?|session(?:{_PORTABLE_SENSITIVE_NAME_SEPARATOR}(?:id|token))?|"
    rf"credential(?:s)?|private{_PORTABLE_SENSITIVE_NAME_SEPARATOR}key|"
    rf"secret{_PORTABLE_SENSITIVE_NAME_SEPARATOR}key|"
    rf"auth{_PORTABLE_SENSITIVE_NAME_SEPARATOR}token|"
    rf"bearer(?:{_PORTABLE_SENSITIVE_NAME_SEPARATOR}token)?|passphrase|"
    rf"signing{_PORTABLE_SENSITIVE_NAME_SEPARATOR}key|"
    rf"aws{_PORTABLE_SENSITIVE_NAME_SEPARATOR}(?:"
    rf"secret{_PORTABLE_SENSITIVE_NAME_SEPARATOR}access{_PORTABLE_SENSITIVE_NAME_SEPARATOR}key|"
    rf"session{_PORTABLE_SENSITIVE_NAME_SEPARATOR}token|"
    rf"access{_PORTABLE_SENSITIVE_NAME_SEPARATOR}key"
    rf"(?:{_PORTABLE_SENSITIVE_NAME_SEPARATOR}id)?))"
)
PORTABLE_SENSITIVE_SETTING_KEY_PATTERN = re.compile(
    rf"{_PORTABLE_SENSITIVE_SETTING_NAME}", re.IGNORECASE
)
_PORTABLE_YAML_SCALAR_DECORATORS = (
    r"(?:(?:!!?[A-Za-z][A-Za-z0-9_:/.-]*|!<[^>\r\n]+>|&[A-Za-z0-9_-]+)[ \t]*){0,8}"
)
_PORTABLE_SAFE_CREDENTIAL_PLACEHOLDER_PATTERN = re.compile(
    r"(?:<[^<>\r\n]{1,160}>|\$\{[A-Za-z_][A-Za-z0-9_]*\}|"
    r"\{\{[A-Za-z_][A-Za-z0-9_. -]*\}\}|(?:redacted|placeholder|example))",
    re.IGNORECASE,
)
_PORTABLE_COOKIE_HEADER_PATTERN = re.compile(
    r"\b(?P<kind>set-)?cookie[ \t]*:[ \t]*(?P<body>[^\r\n]{0,4096})",
    re.IGNORECASE,
)
_PORTABLE_COOKIE_NAME_PATTERN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")

# Privacy carriers copied from Office, a browser, or formatted Markdown may
# use fullwidth ASCII, visible Unicode spaces/dashes, or zero-width joiners.
# This translation is deliberately used only by the privacy scanner: it gives
# token/header/contact grammars their visible text without globally rewriting
# source prose, YAML values, or local-path semantics.
_PORTABLE_PRIVACY_FORMATTING_CHARACTERS = (
    "\u00ad\u034f\u061c\u180e\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e"
    "\u2060\u2061\u2062\u2063\u2064\u2066\u2067\u2068\u2069\u206a\u206b\u206c\u206d\u206e\u206f\ufeff"
    + "".join(chr(codepoint) for codepoint in range(0xFE00, 0xFE10))
    + "".join(chr(codepoint) for codepoint in range(0xE0100, 0xE01F0))
    + "".join(chr(codepoint) for codepoint in range(0xE0001, 0xE0080))
)
_PORTABLE_PRIVACY_PRESENTATION_TRANSLATION = str.maketrans(
    {
        **{chr(codepoint): chr(codepoint - 0xFEE0) for codepoint in range(0xFF01, 0xFF5F)},
        **{character: " " for character in "\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000"},
        **{character: "" for character in _PORTABLE_PRIVACY_FORMATTING_CHARACTERS},
        "\ufe6b": "@",  # SMALL COMMERCIAL AT
        "\ufe52": ".",  # SMALL FULL STOP
        "\u2024": ".",  # ONE DOT LEADER
        **{character: "-" for character in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"},
    }
)
_PORTABLE_PRIVACY_FORMAT_SPACE_TRANSLATION = str.maketrans(
    {
        **{chr(codepoint): chr(codepoint - 0xFEE0) for codepoint in range(0xFF01, 0xFF5F)},
        **{character: " " for character in "\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000"},
        **{character: " " for character in _PORTABLE_PRIVACY_FORMATTING_CHARACTERS},
        **{character: "-" for character in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"},
    }
)

# Normalize only visually confusable directory separators in the path scanner.
# They are not generic typography rewrites: their only effect is to ensure a
# local/Studio location cannot evade the portable boundary by using a glyph a
# reader will perceive as a slash or backslash. Remote URL masking happens
# after this normalization, so verified public routes keep the usual exception.
_PORTABLE_PATH_PRESENTATION_TRANSLATION = str.maketrans(
    {
        **{chr(codepoint): chr(codepoint - 0xFEE0) for codepoint in range(0xFF01, 0xFF5F)},
        **{character: "" for character in _PORTABLE_PRIVACY_FORMATTING_CHARACTERS},
        "\ufe6b": "@",
        "\ufe52": ".",
        "\u2024": ".",
        "\u2215": "/",  # DIVISION SLASH
        "\u2044": "/",  # FRACTION SLASH
        "\u29f8": "/",  # BIG SOLIDUS
        "\u2216": "\\",  # SET MINUS
        "\u29f5": "\\",  # REVERSE SOLIDUS OPERATOR
        "\u27cb": "/",  # MATHEMATICAL RISING DIAGONAL
        "\u27cd": "\\",  # MATHEMATICAL FALLING DIAGONAL
        "\u2571": "/",  # BOX DRAWINGS LIGHT DIAGONAL UPPER RIGHT TO LOWER LEFT
        "\u2572": "\\",  # BOX DRAWINGS LIGHT DIAGONAL UPPER LEFT TO LOWER RIGHT
        **{character: " " for character in "\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000"},
        **{character: "-" for character in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"},
    }
)


def _portable_path_presentation_variants(text: str) -> tuple[str, ...]:
    """Return raw and joined visual forms for local-path/Studio scanning.

    Unlike privacy grammars, local-path syntax must not treat an invisible
    formatter as a visible space: ``work<formatter>/37`` and
    ``fi<formatter>le:`` are one path/URI token to a reader. The shared fold
    covers fullwidth ASCII and removes default-ignorable characters before
    the caller applies its normal remote-URL masking rules.
    """

    return tuple(dict.fromkeys((text, text.translate(_PORTABLE_PATH_PRESENTATION_TRANSLATION))))


def _portable_privacy_presentation_variants(text: str) -> tuple[str, ...]:
    """Expose text where invisible formatting is either joined or spaced.

    Joining detects contacts split inside a lexical token (for example an
    email address); spacing detects grammars whose visible separator is
    significant (for example ``Bearer <value>``). Both forms remain scanner
    inputs only and never rewrite stored source.
    """

    return tuple(
        dict.fromkeys(
            (
                text,
                text.translate(_PORTABLE_PRIVACY_PRESENTATION_TRANSLATION),
                text.translate(_PORTABLE_PRIVACY_FORMAT_SPACE_TRANSLATION),
            )
        )
    )
PORTABLE_PHONE_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<![A-Za-z0-9])(?:\+?86[ .-]?)?1[3-9]\d(?:[ .-]?\d){8}(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])(?:tel:)?\+[1-9]\d{7,14}(?![A-Za-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])\+1[ .-]?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])(?:\+?1[ .-]?)?\(?[2-9]\d{2}\)?[ .-]?[2-9]\d{2}[ .-]?\d{4}(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])\+[1-9](?:[ .-]?\d){7,14}(?![A-Za-z0-9])"),
)
PORTABLE_EMAIL_VALUE_PATTERN = re.compile(
    # ``\w`` remains Unicode-aware: a genuine EAI/IDN address must not become
    # portable merely because its local part, domain label, or TLD is non-ASCII.
    # The two-character final label keeps ordinary prose such as ``user@guide``
    # usable as a public route name.
    r"(?<![\w.!#$%&'*+/=?^`{|}~-])"
    r"[\w.!#$%&'*+/=?^`{|}~-]+@"
    r"(?:[\w](?:[\w-]{0,61}[\w])?[.\u3002\uff0e\uff61])+"
    r"[\w](?:[\w-]{0,61}[\w])"
)
PORTABLE_BEARER_VALUE_PATTERN = re.compile(
    rf"\bBearer[ \t]+{_PORTABLE_YAML_SCALAR_DECORATORS}[\"']?"
    r"(?!(?:token|value|secret|credential(?:s)?|authentication|scheme)\b)[A-Za-z0-9._~+/-]+",
    re.IGNORECASE,
)
PORTABLE_DATA_URI_PATTERN = re.compile(r"\bdata:[^\s]*", re.IGNORECASE)
PORTABLE_SENSITIVE_VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            rf"(?:[?&]{_PORTABLE_SENSITIVE_SETTING_NAME}=[^&#\s]+|"
            rf"\b{_PORTABLE_SENSITIVE_SETTING_NAME}[ \t]*(?::|=)[ \t]*{_PORTABLE_YAML_SCALAR_DECORATORS}[\"']?[A-Za-z0-9._~+/%=-]+|"
            rf"[\"']{_PORTABLE_SENSITIVE_SETTING_NAME}[\"'][ \t]*:[ \t]*{_PORTABLE_YAML_SCALAR_DECORATORS}[\"']?[A-Za-z0-9._~+/%=-]+|"
            r"\b(?:https?|wss?)://[^/?#\s@]*@|"
            rf"(?im:^[ \t]*[\"']?{_PORTABLE_SENSITIVE_SETTING_NAME}[\"']?[ \t]*:[ \t]*[>|][+-]?[0-9]*[ \t]*(?:\#.*)?\r?\n[ \t]+(?!(?:\#|<|\$\{{|\{{\{{))[\S]+)|"
            r"\bsk-[A-Za-z0-9_-]{16,}\b|"
            r"\bgh[oprsu]_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b|"
            r"\bxox[baprs]-[A-Za-z0-9-]{8,}\b|\bAIza[0-9A-Za-z_-]{20,}\b|"
            r"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----)",
            re.IGNORECASE,
        ),
        "credential value",
    ),
    (PORTABLE_BEARER_VALUE_PATTERN, "credential value"),
    (
        PORTABLE_DATA_URI_PATTERN,
        "embedded data URI",
    ),
    (
        PORTABLE_EMAIL_VALUE_PATTERN,
        "personal contact",
    ),
    *((pattern, "personal contact") for pattern in PORTABLE_PHONE_VALUE_PATTERNS),
)

# A verified remote URL path may contain ordinary public route names such as
# ``/token=overview``.  It can never make a provider-shaped secret portable,
# though: a real GitHub/OpenAI/Slack/Google credential embedded in the path is
# still a credential disclosure. Contact values and explicit authentication
# carriers are likewise never portable public route names. Run those narrow
# checks against unmasked decoded text in ``contains_portable_sensitive_value``.
PORTABLE_HIGH_CONFIDENCE_SECRET_PATTERN = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{16,}\b|"
    r"\bgh[oprsu]_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b|"
    r"\bxox[baprs]-[A-Za-z0-9-]{8,}\b|\bAIza[0-9A-Za-z_-]{20,}\b|"
    r"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----)",
    re.IGNORECASE,
)

# A remote URL path is normally naming data, but a path component named
# ``token``/``password``/``session`` has no portable reason to carry an
# arbitrary value. Keep only a finite, readable documentation-route vocabulary
# such as ``/token=overview`` or ``/token=release-notes-2026``. This avoids a
# length heuristic that would permit ``/token=x`` or an opaque base64url/JWT
# split across innocent-looking hyphen/underscore pieces. Provider-specific
# patterns above continue to catch their documented formats independently.
PORTABLE_REMOTE_PATH_CREDENTIAL_PATTERN = re.compile(
    rf"/(?:{_PORTABLE_SENSITIVE_SETTING_NAME})="
    r"(?P<value>[^/?#\s]+)",
    re.IGNORECASE,
)
PORTABLE_REMOTE_PATH_CREDENTIAL_SEGMENT_PATTERN = re.compile(
    rf"/(?:{_PORTABLE_SENSITIVE_SETTING_NAME})/"
    r"(?P<value>[^/?#\s]+)",
    re.IGNORECASE,
)
PORTABLE_REMOTE_PATH_MATRIX_CREDENTIAL_PATTERN = re.compile(
    rf";(?:{_PORTABLE_SENSITIVE_SETTING_NAME})="
    r"(?P<value>[^/?#;\s]+)",
    re.IGNORECASE,
)
PORTABLE_REMOTE_PATH_MATRIX_CREDENTIAL_SEGMENT_PATTERN = re.compile(
    rf";(?:{_PORTABLE_SENSITIVE_SETTING_NAME})/"
    r"(?P<value>[^/?#;\s]+)",
    re.IGNORECASE,
)
_PORTABLE_BASIC_AUTH_CANDIDATE_PATTERN = re.compile(
    r"\bBasic[ \t]+(?P<payload>[A-Za-z0-9+/]{4,}={0,2})(?=$|[\s\]\},)'\"])", re.IGNORECASE
)
_PORTABLE_BASIC_AUTH_PATH_CANDIDATE_PATTERN = re.compile(
    r"/(?:basic)/(?P<payload>[A-Za-z0-9+/]{4,}={0,2})(?=$|[/?#\s\]\},)'\"])", re.IGNORECASE
)
_PORTABLE_BASIC_AUTH_MATRIX_PATH_CANDIDATE_PATTERN = re.compile(
    r";(?:basic)/(?P<payload>[A-Za-z0-9+/]{4,}={0,2})(?=$|[/?#;\s\]\},)'\"])", re.IGNORECASE
)
_PORTABLE_BASIC_AUTH_URL_COMPONENT_PATTERN = re.compile(
    r"(?:^|[;=/])(?:basic)/(?P<payload>[A-Za-z0-9+/]{4,}={0,2})(?=$|[/?#;&\s\]\},)'\"])",
    re.IGNORECASE,
)
_PORTABLE_REMOTE_PATH_TOKEN_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9._~%+\-]+={0,2}$")
_PORTABLE_DOCUMENTATION_ROUTE_WORDS = frozenset(
    {
        "api", "article", "articles", "authentication", "auth", "catalog", "changelog",
        "compatibility", "concept", "concepts", "configuration", "config", "demo", "demos",
        "details", "developer", "developers", "docs", "documentation", "example", "examples",
        "explanation", "faq", "guide", "guides", "handling", "index", "information", "intro",
        "introduction", "learn", "learning", "long", "manual", "migration", "migrations", "note",
        "notes", "overview", "policy", "policies", "public", "reference", "references", "release",
        "releases", "report", "reports", "settings", "spec", "specification", "standard", "standards",
        "status", "summary", "title", "tutorial", "tutorials", "usage", "use", "version", "with",
    }
)
_PORTABLE_DOCUMENTATION_ROUTE_VERSION_PATTERN = re.compile(r"(?:v\d{1,4}|(?:19|20)\d{2})$", re.IGNORECASE)


_PORTABLE_MARKDOWN_FENCE_OPEN_PATTERN = re.compile(
    r"^[ \t]*(?P<fence>`{3,}|~{3,})[^\r\n]*$"
)
_PORTABLE_MARKDOWN_FENCE_CLOSE_PATTERN = re.compile(
    r"^[ \t]*(?P<fence>`{3,}|~{3,})[ \t]*$"
)
_PORTABLE_MARKDOWN_INLINE_CODE_PATTERN = re.compile(
    r"(?<!`)`(?P<body>[^`\r\n]{1,32768})`(?!`)"
)
_PORTABLE_YAML_FRONTMATTER_PATTERN = re.compile(
    r"\A---[ \t]*\r?\n(?P<body>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL
)
_PORTABLE_MARKDOWN_STRUCTURE_PATTERN = re.compile(r"(?m)^(?:#{1,6}[ \t]|`{3,}|~{3,})")
_PORTABLE_MARKDOWN_BLOCKQUOTE_PATTERN = re.compile(r"(?m)^[ \t]*>[ \t]?(?P<body>.*)$")
_PORTABLE_MARKDOWN_COMMENT_LINE_PATTERN = re.compile(r"(?m)^[ \t]*#[ \t]?(?P<body>.*)$")
_PORTABLE_HTML_YAML_WRAPPER_PATTERN = re.compile(
    r"(?is)<(?P<tag>[A-Za-z][A-Za-z0-9:_-]*)\b[^>]*>(?P<body>.*?)</(?P=tag)\s*>"
)
_PORTABLE_HTML_TAG_PATTERN = re.compile(r"(?is)</?[A-Za-z][^>]*>")
_PORTABLE_XML_PROCESSING_INSTRUCTION_PATTERN = re.compile(r"(?is)<\?(?P<body>.*?)\?>")
_PORTABLE_HTML_ATTRIBUTE_VALUE_PATTERN = re.compile(
    r'''(?is)\b[A-Za-z_:][A-Za-z0-9_:.\-]*[ \t]*=[ \t]*(?:"(?P<double>[^"]*)"|'(?P<single>[^']*)'|(?P<bare>[^\s"'=<>`]+))'''
)
_PORTABLE_HTML_EMBEDDING_TAG_PATTERN = re.compile(
    r"(?is)<(?:"
    r"/?(?:address|article|aside|blockquote|br|code|details|div|dl|dt|dd|figcaption|figure|"
    r"footer|form|h[1-6]|header|li|main|nav|ol|p|pre|script|section|span|summary|table|tbody|"
    r"td|textarea|tfoot|th|thead|tr|ul)\b|"
    r"/?[A-Za-z][A-Za-z0-9:_-]*-[A-Za-z0-9:_-]+\b"
    r")[^>]*>"
)
_PORTABLE_HTML_COMMENT_PATTERN = re.compile(r"(?is)<!--.*?-->")
_PORTABLE_HTML_COMMENT_BODY_PATTERN = re.compile(r"(?is)<!--(?P<body>.*?)-->")
_PORTABLE_HTML_UNCLOSED_COMMENT_LINE_PATTERN = re.compile(r"(?m)^[ \t]*<!--[^\r\n]*(?:\r?\n|$)")
_PORTABLE_HTML_LINE_BREAK_PATTERN = re.compile(r"(?is)<\s*br\b[^>]*>")
_PORTABLE_HTML_BLOCK_CLOSE_PATTERN = re.compile(
    r"(?is)</\s*(?:p|div|li|tr|td|th|h[1-6])\s*>"
)
_PORTABLE_CDATA_PATTERN = re.compile(r"(?is)<!\[CDATA\[(?P<body>.*?)\]\]>")
_PORTABLE_CDATA_OPEN_PATTERN = re.compile(r"(?i)<!\[CDATA\[")
_PORTABLE_HTML_DECLARATION_PATTERN = re.compile(r"(?is)<![^>]*>")
_PORTABLE_HTML_DECLARATION_BODY_PATTERN = re.compile(r"(?is)<!(?P<body>[^>]+)>")
_PORTABLE_YAML_TAG_OR_ANCHOR_DOCUMENT_START = (
    r"(?:%TAG\b|![A-Za-z]|!<|&[A-Za-z0-9_-]+(?:[ \t]|$))"
)
_PORTABLE_YAML_TOP_LEVEL_MAPPING_LINE_PATTERN = re.compile(
    rf"^(?:(?:[A-Za-z_][A-Za-z0-9_.-]*|[\"']?{_PORTABLE_SENSITIVE_SETTING_NAME}[\"']?)[ \t]*:|\?[ \t]+|{_PORTABLE_YAML_TAG_OR_ANCHOR_DOCUMENT_START})",
    re.IGNORECASE | re.MULTILINE,
)
_PORTABLE_YAML_TOP_LEVEL_STRUCTURE_LINE_PATTERN = re.compile(
    rf"^(?:(?:[A-Za-z_][A-Za-z0-9_.-]*|[\"']?{_PORTABLE_SENSITIVE_SETTING_NAME}[\"']?)[ \t]*:|\?[ \t]+|{_PORTABLE_YAML_TAG_OR_ANCHOR_DOCUMENT_START}|---|[{{\[]|-\s)",
    re.IGNORECASE,
)
_PORTABLE_YAML_TOP_LEVEL_BINARY_SCALAR_LINE_PATTERN = re.compile(
    r"^[ \t]*(?:!!binary|!<tag:yaml\.org,2002:binary>)(?=[ \t]|$)",
    re.IGNORECASE,
)
_PORTABLE_YAML_TOP_LEVEL_TAGGED_SCALAR_LINE_PATTERN = re.compile(
    r"^[ \t]*(?:!![A-Za-z][A-Za-z0-9_:/.-]*|![A-Za-z][A-Za-z0-9_:/.-]*|!<[^>\r\n]+>)(?=[ \t]|$)",
    re.IGNORECASE,
)
_PORTABLE_YAML_EMBEDDED_TAGGED_SCALAR_PATTERN = re.compile(
    r"(?:^|[:\[\{,]|-[ \t])[ \t]*(?:!![A-Za-z][A-Za-z0-9_:/.-]*|![A-Za-z][A-Za-z0-9_:/.-]*|!<[^>\r\n]+>)(?=[ \t]|$)",
    re.IGNORECASE,
)
_PORTABLE_YAML_TAGGED_BASE64_VALUE_PATTERN = re.compile(r"[A-Za-z0-9+/]+={0,2}$")
_PORTABLE_YAML_BLOCK_SCALAR_HEADER_PATTERN = re.compile(
    r"^(?P<indent>[ \t]*)(?:[^#\r\n][^:\r\n]*):[ \t]*[>|][+-]?[1-9]?[ \t]*(?:\#.*)?$",
    re.MULTILINE,
)
_PORTABLE_YAML_MAX_CANDIDATE_BYTES = 32 * 1024
_PORTABLE_YAML_MAX_QUOTE_DEPTH = 64

# This is intentionally only a fail-closed fallback for YAML that cannot be
# safely constructed (for example, a custom tag or a duplicate key).  The
# semantic loader below is the primary detector.  Keeping the candidate shape
# narrow avoids treating ordinary prose as a broken configuration document.
_PORTABLE_YAML_SENSITIVE_KEY_CANDIDATE_PATTERN = re.compile(
    rf"""(?imx)
    (?:^|[{{\[,\n]|^[ \t]*-[ \t]+)[ \t]*
    (?:
        \?[ \t]*
        (?:
            (?:{_PORTABLE_YAML_SCALAR_DECORATORS})?[\"']?{_PORTABLE_SENSITIVE_SETTING_NAME}[\"']?
            |
            \[[ \t]*(?:{_PORTABLE_YAML_SCALAR_DECORATORS})?[\"']?{_PORTABLE_SENSITIVE_SETTING_NAME}[\"']?[ \t]*\]
        )
        [ \t]*\r?\n[ \t]*:
        |
        (?:{_PORTABLE_YAML_SCALAR_DECORATORS})?[\"']?{_PORTABLE_SENSITIVE_SETTING_NAME}[\"']?[ \t]*:
    )
    """
)
_PORTABLE_YAML_ASSOCIATION_LOSS_TAG_PATTERN = re.compile(
    r"(?i)!!(?:set|omap|pairs)\b"
)


class _PortableYamlDuplicateKeyError(yaml.YAMLError):
    """A YAML mapping repeats a key and therefore cannot be trusted."""


class _PortableYamlCandidateLimitError(yaml.YAMLError):
    """A credential-shaped YAML candidate exceeded a safe extraction bound."""


class _PortableYamlLoader(yaml.SafeLoader):
    """Safe loader that rejects duplicate mapping keys before construction.

    ``yaml.safe_load`` silently keeps a last duplicate key.  That would let a
    later ``token: <token>`` hide an earlier alias carrying a real value.  A
    portable boundary needs the opposite behavior: duplicate mappings are an
    invalid representation whenever they look credential-related.
    """

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[object, object]:
        if not isinstance(node, yaml.MappingNode):
            raise yaml.constructor.ConstructorError(
                None, None, f"expected a mapping node, got {node.id}", node.start_mark
            )
        mapping: dict[object, object] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in mapping:
                    raise _PortableYamlDuplicateKeyError("duplicate YAML mapping key")
                value = self.construct_object(value_node, deep=deep)
                mapping[key] = value
            except TypeError as error:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping", node.start_mark, "found an unhashable key", key_node.start_mark
                ) from error
        return mapping


def _normalize_portable_yaml_wrappers(text: str) -> str:
    """Expose YAML hidden behind presentational HTML without dropping content.

    A packet field is free-form text, so HTML can be legitimate prose. It must
    not, however, turn a YAML credential example into an unscanned value. Strip
    markup and comment delimiters while preserving the body. For an unclosed
    comment opener, remove only that opener's line: removing the rest of the
    input would create the very blind spot this normalization is meant to
    close.
    """

    normalized = _PORTABLE_CDATA_PATTERN.sub(lambda match: match.group("body"), text)
    normalized = _PORTABLE_CDATA_OPEN_PATTERN.sub("", normalized)
    normalized = normalized.replace("]]>", "")
    normalized = _PORTABLE_HTML_COMMENT_PATTERN.sub("", normalized)
    normalized = _PORTABLE_HTML_UNCLOSED_COMMENT_LINE_PATTERN.sub("", normalized)
    normalized = _PORTABLE_HTML_DECLARATION_PATTERN.sub("", normalized)
    normalized = _PORTABLE_XML_PROCESSING_INSTRUCTION_PATTERN.sub(
        lambda match: match.group("body"), normalized
    )
    # A line break is semantic structure in a YAML mapping. Removing ``<br>``
    # outright would concatenate an anchored value and its sensitive alias.
    normalized = _PORTABLE_HTML_LINE_BREAK_PATTERN.sub("\n", normalized)
    normalized = _PORTABLE_HTML_BLOCK_CLOSE_PATTERN.sub("\n", normalized)
    return _PORTABLE_HTML_TAG_PATTERN.sub(_strip_portable_html_presentation_tag, normalized)


def _normalize_portable_markdown_wrappers(text: str) -> str:
    """Expose a YAML key split by harmless-looking Markdown delimiters.

    This is intentionally limited to a prospective mapping *key*. Values are
    left byte-for-byte intact: ``not_recorded`` is valid authorization
    provenance, and ``<token>`` is a valid explicit placeholder. Thus
    ``to**ken**: *value`` and ``to`ken`: *value`` are examined as the same
    mapping key without rewriting ordinary YAML scalar values or fences.
    """

    key_pattern = re.compile(
        r"(?m)(?P<prefix>(?:^|(?<=[{\[,\n#]))[ \t]*)(?P<key>[A-Za-z][A-Za-z0-9_*~`-]*)"
        r"(?P<colon>[ \t]*:)"
    )

    def replace(match: re.Match[str]) -> str:
        key = match.group("key")
        compact = re.sub(r"[*_~`]", "", key)
        if compact != key and is_portable_sensitive_setting_key(compact):
            return match.group("prefix") + compact + match.group("colon")
        return match.group(0)

    return key_pattern.sub(replace, text)


_PORTABLE_INLINE_MARKDOWN_PRESENTATION_WRAPPER_PATTERN = re.compile(
    r"(?:"
    r"(?<![A-Za-z0-9_])(?P<underscore>_{1,2})(?!_)(?P<underscore_body>[^\r\n]{1,4096}?)(?P=underscore)(?![A-Za-z0-9_])"
    r"|"
    r"(?<!`)(?P<tick>`{1,2})(?!`)(?P<tick_body>[^\r\n]{1,4096}?)(?P=tick)(?!`)"
    r"|"
    r"(?<!\*)(?P<star>\*{1,2})(?!\*)(?P<star_body>[^\r\n]{1,4096}?)(?P=star)(?!\*)"
    r"|"
    r"(?<!~)(?P<tilde>~~)(?!~)(?P<tilde_body>[^\r\n]{1,4096}?)(?P=tilde)(?!~)"
    r")"
)
_PORTABLE_EXPLICIT_PRESENTATION_PLACEHOLDER_TAG_PATTERN = re.compile(
    r"^<(?:token|value|secret|credential(?:s)?|password|authorization|"
    r"api[-_ ]?key|access[-_ ]?token|session(?:[-_ ]?(?:id|token))?|"
    r"redacted|placeholder|example)>$",
    re.IGNORECASE,
)


def _strip_portable_html_presentation_tag(match: re.Match[str]) -> str:
    """Strip display markup while retaining explicit portable placeholders."""

    # ``<token>`` and related explicit example placeholders are source data,
    # not presentation markup. Keeping them intact avoids turning a safe
    # Cookie/header/schema example into an empty concrete assignment.
    if _PORTABLE_EXPLICIT_PRESENTATION_PLACEHOLDER_TAG_PATTERN.fullmatch(match.group(0)):
        return match.group(0)
    return ""


def _normalize_portable_presentation_wrappers(text: str) -> str:
    """Expose literal text split by HTML or inline Markdown presentation.

    The portable boundary receives raw source bytes, so a visible local path,
    Work identifier, contact, or credential must not become safe merely by
    putting a few characters in ``<span>`` or ``**bold**`` wrappers. This is
    an additional scan form only: it does not rewrite source, YAML values, or
    ordinary output. The normal path/URL masker still runs after this step,
    preserving the public-remote-URL exception for a normalized URL path.
    """

    without_html = _PORTABLE_HTML_TAG_PATTERN.sub(_strip_portable_html_presentation_tag, text)

    def strip_markdown_wrapper(match: re.Match[str]) -> str:
        for name in ("underscore_body", "tick_body", "star_body", "tilde_body"):
            body = match.group(name)
            if body is not None:
                return body
        raise AssertionError("presentation wrapper match did not expose a body")

    return _PORTABLE_INLINE_MARKDOWN_PRESENTATION_WRAPPER_PATTERN.sub(
        strip_markdown_wrapper, without_html
    )


def portable_presentation_scan_variants(text: str) -> tuple[str, ...]:
    """Return decoded text plus bounded presentation-normalized variants.

    Wrapper removal can expose nested HTML and inline Markdown forms. Keep a
    short finite loop and fail closed if a future normalization rule were ever
    able to keep changing the same value indefinitely.
    """

    variants: list[str] = []
    seen: set[str] = set()
    for decoded in portable_text_variants(text):
        current = decoded
        for _ in range(8):
            if current not in seen:
                variants.append(current)
                seen.add(current)
            normalized = _normalize_portable_presentation_wrappers(current)
            if normalized == current:
                break
            current = normalized
        else:
            raise PortableTextDecodingError("portable text has excessively nested presentation wrappers")
    return tuple(variants)


def _portable_markdown_inline_code_bodies(text: str) -> tuple[str, ...]:
    """Return bounded inline-code bodies without converting a full document."""

    return tuple(match.group("body") for match in _PORTABLE_MARKDOWN_INLINE_CODE_PATTERN.finditer(text))


def _portable_html_attribute_values(text: str) -> tuple[str, ...]:
    """Return HTML/XML-markup attribute values that may carry YAML objects."""

    values: list[str] = []
    for pattern in (
        _PORTABLE_HTML_TAG_PATTERN,
        _PORTABLE_XML_PROCESSING_INSTRUCTION_PATTERN,
    ):
        for tag in pattern.finditer(text):
            for attribute in _PORTABLE_HTML_ATTRIBUTE_VALUE_PATTERN.finditer(tag.group(0)):
                value = attribute.group("double") or attribute.group("single") or attribute.group("bare")
                if value is not None:
                    values.append(value)
    return tuple(values)


def _portable_processing_instruction_bodies(text: str) -> tuple[str, ...]:
    """Expose XML processing instructions at the raw portable-byte boundary."""

    return tuple(
        match.group("body")
        for match in _PORTABLE_XML_PROCESSING_INSTRUCTION_PATTERN.finditer(text)
    )


def _portable_markdown_comment_bodies(text: str) -> tuple[str, ...]:
    """Extract contiguous Markdown comment bodies without treating prose as YAML."""

    bodies: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        match = _PORTABLE_MARKDOWN_COMMENT_LINE_PATTERN.match(line)
        if match is not None:
            current.append(match.group("body"))
        elif current:
            bodies.append("\n".join(current))
            current.clear()
    if current:
        bodies.append("\n".join(current))
    return tuple(bodies)


def _portable_html_comment_and_declaration_bodies(text: str) -> tuple[str, ...]:
    """Expose raw comment/declaration bytes to the credential scanner.

    Package and handoff privacy apply to the uploaded/raw bytes, so a comment
    cannot be a blind spot merely because it is not visible rendered prose.
    For declarations, also expose a suffix starting at an explicit complex
    YAML key: ``<!DOCTYPE x [ ? [token] ...]>`` otherwise begins with HTML
    metadata rather than the embedded YAML object.
    """

    bodies = [match.group("body") for match in _PORTABLE_HTML_COMMENT_BODY_PATTERN.finditer(text)]
    for match in _PORTABLE_HTML_DECLARATION_BODY_PATTERN.finditer(text):
        body = match.group("body")
        bodies.append(body)
        key_start = body.find("?")
        if key_start >= 0:
            bodies.append(body[key_start:])
    return tuple(bodies)


def _portable_yaml_block_scalar_bodies(text: str) -> tuple[str, ...]:
    """Extract raw YAML block-scalar bodies before ``>`` folds their lines.

    ``SafeLoader`` intentionally returns a folded scalar as one string.  That
    is correct YAML behavior, but it erases the newline that may separate a
    nested ``value: &alias`` from ``token: *alias``.  Preserve the source
    body, with its common indentation removed, as a bounded candidate for the
    same semantic scanner.  This is extraction only; normal YAML loading still
    decides whether a candidate is an actual mapping.
    """

    lines = text.splitlines()
    bodies: list[str] = []
    for index, line in enumerate(lines):
        header = _PORTABLE_YAML_BLOCK_SCALAR_HEADER_PATTERN.match(line)
        if header is None:
            continue
        header_indent = len(header.group("indent"))
        body_lines: list[str] = []
        for following in lines[index + 1:]:
            if not following.strip():
                if body_lines:
                    body_lines.append("")
                continue
            leading = len(following) - len(following.lstrip(" \t"))
            if leading <= header_indent:
                break
            body_lines.append(following)
        while body_lines and not body_lines[-1]:
            body_lines.pop()
        nonempty_indents = [
            len(item) - len(item.lstrip(" \t")) for item in body_lines if item.strip()
        ]
        if not nonempty_indents:
            continue
        common_indent = min(nonempty_indents)
        bodies.append(
            "\n".join(item[common_indent:] if item.strip() else "" for item in body_lines)
        )
    return tuple(bodies)


def _portable_fenced_code_bodies(text: str) -> tuple[str, ...]:
    """Extract complete CommonMark-style fenced bodies without shortening runs.

    A four-backtick opener must never be interpreted as a three-backtick
    opener with a stray backtick in its info string. Closing runs use the same
    marker character and may be longer than the opener, matching the ordinary
    Markdown fence rule. Unclosed fences remain handled by the later
    line-oriented fallback so their payload cannot become invisible.
    """

    bodies: list[str] = []
    open_fence: str | None = None
    body_lines: list[str] = []
    for line in text.splitlines():
        if open_fence is None:
            opened = _PORTABLE_MARKDOWN_FENCE_OPEN_PATTERN.match(line)
            if opened is not None:
                open_fence = opened.group("fence")
                body_lines = []
            continue
        closed = _PORTABLE_MARKDOWN_FENCE_CLOSE_PATTERN.match(line)
        if (
            closed is not None
            and closed.group("fence")[0] == open_fence[0]
            and len(closed.group("fence")) >= len(open_fence)
        ):
            bodies.append("\n".join(body_lines))
            open_fence = None
            body_lines = []
            continue
        body_lines.append(line)
    return tuple(bodies)


def _portable_yaml_sensitive_suffixes(text: str) -> tuple[str, ...]:
    """Extract literal top-level YAML suffixes after Markdown-like prose.

    A code fence can itself contain a heading or a prose preface before a
    valid YAML mapping. Parse that mapping separately rather than treating the
    whole fence as an opaque scalar. The caller supplies a fence body or an
    already-unfenced region, so this helper deliberately has no fence state of
    its own.
    """

    suffixes: list[str] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not _PORTABLE_YAML_TOP_LEVEL_STRUCTURE_LINE_PATTERN.match(line):
            continue
        suffix_lines: list[str] = []
        for following in lines[index:]:
            if not following.strip():
                break
            suffix_lines.append(following)
        suffix = "\n".join(suffix_lines)
        if _looks_like_sensitive_yaml_mapping(suffix):
            suffixes.append(suffix)
    return tuple(suffixes)


def _portable_yaml_top_level_binary_scalar_documents(text: str) -> tuple[str, ...]:
    """Extract standalone YAML binary scalars from Markdown-rich source.

    A complete Markdown skill document cannot be loaded as one YAML document,
    yet a top-level ``!!binary`` line still has independent YAML semantics.
    Its decoded bytes may be a local path, contact, credential, or control
    character. Preserve a bounded scalar plus its indented base64 continuations
    for the normal semantic YAML walk rather than letting mixed Markdown make
    that one scalar invisible.
    """

    lines = text.splitlines()
    documents: list[str] = []
    for index, line in enumerate(lines):
        if _PORTABLE_YAML_TOP_LEVEL_BINARY_SCALAR_LINE_PATTERN.match(line) is None:
            continue
        scalar_lines = [line]
        for following in lines[index + 1:]:
            if not following.strip():
                break
            if not re.match(r"^[ \t]+(?:[A-Za-z0-9+/=]+[ \t]*)?(?:\#.*)?$", following):
                break
            scalar_lines.append(following)
        documents.append("\n".join(scalar_lines))
    return tuple(documents)


def _portable_yaml_top_level_tagged_scalar_documents(text: str) -> tuple[str, ...]:
    """Extract standalone tagged scalars from mixed Markdown/YAML source.

    Unknown YAML tags cannot be constructed by ``SafeLoader``. Preserve a
    bounded top-level scalar so an encoded value under such a tag receives the
    same raw/decoded privacy inspection as ``!!binary`` instead of disappearing
    inside a Markdown document.
    """

    lines = text.splitlines()
    documents: list[str] = []
    for index, line in enumerate(lines):
        if (
            _PORTABLE_YAML_TOP_LEVEL_TAGGED_SCALAR_LINE_PATTERN.match(line) is None
            and _PORTABLE_YAML_EMBEDDED_TAGGED_SCALAR_PATTERN.search(line) is None
        ):
            continue
        scalar_lines = [line]
        for following in lines[index + 1:]:
            if not following.strip():
                break
            if not re.match(r"^[ \t]+(?:[A-Za-z0-9+/=]+[ \t]*)?(?:\#.*)?$", following):
                break
            scalar_lines.append(following)
        documents.append("\n".join(scalar_lines))
    return tuple(documents)


def _portable_yaml_documents(text: str) -> tuple[str, ...]:
    """Yield bounded YAML candidates found in an otherwise free-form field.

    A packet field can be ordinary prose, so it must never be *treated* as a
    configuration document.  It can nonetheless contain a YAML snippet that
    hides a secret through aliases, flow mappings, explicit keys, tags, or
    block scalars.  Parse only the whole field, fenced YAML blocks, and
    bounded blank-line paragraphs as a privacy detector; invalid candidates
    simply remain ordinary text for the direct lexical rules.
    """

    # A complete Markdown file is not a malformed YAML document merely
    # because its frontmatter starts with ``---``. Parse raw whole-field YAML
    # only when it has no Markdown structure; extract frontmatter and fenced
    # YAML independently for mixed Markdown/YAML text.
    candidates: list[str] = []
    if _PORTABLE_MARKDOWN_STRUCTURE_PATTERN.search(text) is None:
        candidates.append(text)
    candidates.extend(_portable_yaml_top_level_binary_scalar_documents(text))
    candidates.extend(_portable_yaml_top_level_tagged_scalar_documents(text))
    # Inline code and HTML attributes are data carriers, not merely visual
    # decoration. Feed their literal bodies to the same YAML relationship
    # checker so an alias-backed flow mapping cannot hide behind a span or a
    # Markdown monospace wrapper.
    for embedded in (
        *_portable_markdown_inline_code_bodies(text),
        *_portable_html_attribute_values(text),
        *_portable_processing_instruction_bodies(text),
        *_portable_markdown_comment_bodies(text),
        *_portable_html_comment_and_declaration_bodies(text),
    ):
        candidates.append(embedded)
        candidates.extend(_portable_yaml_sensitive_suffixes(embedded))
        normalized_embedded = _normalize_portable_yaml_wrappers(
            _normalize_portable_markdown_wrappers(embedded)
        )
        if normalized_embedded != embedded:
            candidates.append(normalized_embedded)
            candidates.extend(_portable_yaml_sensitive_suffixes(normalized_embedded))
    normalized_markdown = _normalize_portable_markdown_wrappers(text)
    normalized_html = _normalize_portable_yaml_wrappers(text)
    if normalized_html != text and _PORTABLE_MARKDOWN_STRUCTURE_PATTERN.search(normalized_html) is None:
        # Parse the normalized form even for a Markdown document. A closed or
        # unclosed ``<span>``/comment before an alias mapping otherwise makes
        # the whole field look like prose rather than a self-contained YAML
        # candidate.
        candidates.append(normalized_html)
    frontmatter = _PORTABLE_YAML_FRONTMATTER_PATTERN.match(text)
    if frontmatter is not None:
        candidates.append(frontmatter.group("body"))
    for body in _portable_fenced_code_bodies(text):
        candidates.append(body)
        candidates.extend(_portable_yaml_sensitive_suffixes(body))
        normalized_body = _normalize_portable_yaml_wrappers(
            _normalize_portable_markdown_wrappers(body)
        )
        if normalized_body != body:
            candidates.append(normalized_body)
            candidates.extend(_portable_yaml_sensitive_suffixes(normalized_body))
    if normalized_html != text:
        # Do not feed a whole Markdown document through the YAML fallback: a
        # harmless ``token: <token>`` code sample then looks malformed after
        # stripping its placeholder markup. A bounded paragraph that becomes
        # an actual sensitive YAML object after wrapper normalization is
        # different: inspect that object directly.
        for part in re.split(r"(?:\r?\n){2,}", normalized_html):
            stripped = part.strip()
            if (
                stripped
                and not stripped.startswith(("```", "~~~", "#"))
                and _looks_like_sensitive_yaml_mapping(stripped)
                and _looks_like_self_contained_yaml_document(stripped)
            ):
                candidates.append(part)
    if normalized_markdown != text:
        # Do not parse an entire Markdown document after decoration removal.
        # Instead, take bounded top-level YAML-looking suffixes. This catches
        # keys split as ``to**ken**`` / ``to_ken_`` / ``to`ken``, while a
        # sentence that merely discusses a token remains ordinary prose.
        normalized_lines = normalized_markdown.splitlines()
        open_fence: str | None = None
        for index, line in enumerate(normalized_lines):
            if open_fence is None:
                fence_match = _PORTABLE_MARKDOWN_FENCE_OPEN_PATTERN.match(line)
                if fence_match is not None:
                    open_fence = fence_match.group("fence")
                    continue
            else:
                fence_match = _PORTABLE_MARKDOWN_FENCE_CLOSE_PATTERN.match(line)
                if (
                    fence_match is not None
                    and fence_match.group("fence")[0] == open_fence[0]
                    and len(fence_match.group("fence")) >= len(open_fence)
                ):
                    open_fence = None
                continue
            if not _PORTABLE_YAML_TOP_LEVEL_STRUCTURE_LINE_PATTERN.match(line):
                continue
            suffix_lines: list[str] = []
            for following in normalized_lines[index:]:
                if not following.strip():
                    break
                suffix_lines.append(following)
            suffix = "\n".join(suffix_lines)
            if _looks_like_sensitive_yaml_mapping(suffix) and _looks_like_self_contained_yaml_document(suffix):
                candidates.append(suffix)
    # Keep the source layout of both literal and folded YAML scalars. The
    # decoded scalar value is also re-scanned below, but a folded ``>`` value
    # has already lost the newlines that make an embedded mapping parseable.
    for body in _portable_yaml_block_scalar_bodies(text):
        candidates.append(body)
        normalized_body = _normalize_portable_yaml_wrappers(body)
        if normalized_body != body:
            candidates.append(normalized_body)
    # Quote blocks and simple HTML wrappers are common ways Markdown-rich
    # evidence presents a literal configuration example. Extract them before
    # semantic YAML parsing; otherwise ``> token: |`` and ``<p>token: |``
    # would remain only prose-shaped text outside the block-scalar detector.
    quoted_lines: list[str] = []
    for line in text.splitlines():
        unwrapped = line
        quote_depth = 0
        while quote_depth < _PORTABLE_YAML_MAX_QUOTE_DEPTH:
            match = _PORTABLE_MARKDOWN_BLOCKQUOTE_PATTERN.match(unwrapped)
            if match is None:
                break
            unwrapped = match.group("body")
            quote_depth += 1
        if quote_depth >= _PORTABLE_YAML_MAX_QUOTE_DEPTH and _PORTABLE_MARKDOWN_BLOCKQUOTE_PATTERN.match(unwrapped):
            # We intentionally do not silently stop unwrapping an arbitrarily
            # deep structured block. The importing boundary is small and a
            # hostile value must fail closed instead of hiding after the quote
            # extraction budget.
            raise _PortableYamlCandidateLimitError("Markdown quote nesting exceeds the portable YAML extraction limit")
        if quote_depth:
            quoted_lines.append(unwrapped)
        elif quoted_lines:
            quoted_body = "\n".join(quoted_lines)
            candidates.append(quoted_body)
            candidates.extend(_portable_yaml_sensitive_suffixes(quoted_body))
            quoted_lines.clear()
    if quoted_lines:
        quoted_body = "\n".join(quoted_lines)
        candidates.append(quoted_body)
        candidates.extend(_portable_yaml_sensitive_suffixes(quoted_body))
    for match in _PORTABLE_HTML_YAML_WRAPPER_PATTERN.finditer(text):
        wrapper_body = _normalize_portable_yaml_wrappers(match.group("body"))
        candidates.append(wrapper_body)
        candidates.extend(_portable_yaml_sensitive_suffixes(wrapper_body))
    # Markdown may contain a literal HTML fragment without a closing tag. Do
    # not normalize the entire document—``<token>`` is a valid explicit
    # placeholder and must remain a placeholder—but do inspect the bounded
    # body immediately after a real HTML/custom-element tag. The candidate
    # must itself begin with a mapping line, which keeps ordinary prose and
    # Markdown annotations out of the YAML fallback path.
    for match in _PORTABLE_HTML_EMBEDDING_TAG_PATTERN.finditer(text):
        body = text[match.end():].split("\n\n", 1)[0]
        normalized_body = _normalize_portable_yaml_wrappers(body)
        first_nonempty = next((line for line in normalized_body.splitlines() if line.strip()), "")
        if _PORTABLE_YAML_TOP_LEVEL_MAPPING_LINE_PATTERN.match(first_nonempty.lstrip()):
            candidates.append(normalized_body)
    # A prose introduction before a literal mapping must not turn the mapping
    # into an opaque scalar. Extract every top-level YAML-looking suffix that
    # contains a sensitive key; indentation deliberately stays at column zero
    # so text inside a YAML block scalar is not reinterpreted as a mapping.
    lines = text.splitlines()
    open_fence: str | None = None
    open_fence_body_start: int | None = None
    for index, line in enumerate(lines):
        if open_fence is None:
            fence_match = _PORTABLE_MARKDOWN_FENCE_OPEN_PATTERN.match(line)
            if fence_match is not None:
                open_fence = fence_match.group("fence")
                open_fence_body_start = index + 1
                continue
        else:
            fence_match = _PORTABLE_MARKDOWN_FENCE_CLOSE_PATTERN.match(line)
            if (
                fence_match is not None
                and fence_match.group("fence")[0] == open_fence[0]
                and len(fence_match.group("fence")) >= len(open_fence)
            ):
                open_fence = None
                open_fence_body_start = None
                continue
        if open_fence is not None:
            # Complete fenced blocks were collected above and parsed in their
            # own right. Do not splice their closing fence into a synthetic
            # suffix, which would turn harmless schema descriptors into
            # malformed credential-shaped YAML.
            continue
        if not _PORTABLE_YAML_TOP_LEVEL_STRUCTURE_LINE_PATTERN.match(line):
            continue
        suffix_lines: list[str] = []
        for following in lines[index:]:
            if not following.strip():
                break
            suffix_lines.append(following)
        suffix = "\n".join(suffix_lines)
        if _looks_like_sensitive_yaml_mapping(suffix):
            candidates.append(suffix)
    if open_fence is not None and open_fence_body_start is not None:
        # A missing closing fence is not a reason to skip its content. Treat
        # the remainder as the candidate so an unfinished code block cannot
        # hide an alias-backed credential.
        unfinished_body = "\n".join(lines[open_fence_body_start:])
        candidates.append(unfinished_body)
        candidates.extend(_portable_yaml_sensitive_suffixes(unfinished_body))
    candidates.extend(part for part in re.split(r"(?:\r?\n){2,}", text) if ":" in part or "&" in part or "*" in part)
    unique: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        if len(candidate.encode("utf-8")) > _PORTABLE_YAML_MAX_CANDIDATE_BYTES:
            # Runtime packet strings are already smaller than this cap. The
            # upload package scans source files as well, so do not silently
            # drop a larger candidate that visibly contains a sensitive YAML
            # key. It is safer to reject that source than to parse only the
            # harmless prefix and miss an alias or block scalar at the end.
            if _looks_like_sensitive_yaml_mapping(candidate):
                raise _PortableYamlCandidateLimitError("Credential-shaped YAML exceeds the portable extraction limit")
            continue
        if candidate in unique:
            continue
        unique.append(candidate)
    return tuple(unique)


def _looks_like_sensitive_yaml_mapping(text: str) -> bool:
    return _PORTABLE_YAML_SENSITIVE_KEY_CANDIDATE_PATTERN.search(text) is not None


def _looks_like_self_contained_yaml_document(text: str) -> bool:
    """Distinguish a malformed YAML candidate from ordinary Markdown prose."""

    stripped = text.strip()
    if not stripped or stripped.startswith(("```", "~~~", "#", "* ")):
        return False
    return re.match(
        rf"(?is)^(?:---|\.\.\.|%YAML\b|%TAG\b|![A-Za-z]|!<|&[A-Za-z0-9_-]+(?:[ \t]|$)|-[ \t]+|[{{\[]|\?[ \t]+|"
        rf"(?:{_PORTABLE_YAML_SCALAR_DECORATORS})?[\"']?[A-Za-z_][A-Za-z0-9_. -]*[\"']?[ \t]*:)",
        stripped,
    ) is not None


def _contains_portable_unknown_yaml_tag_payload(document: str) -> bool:
    """Fail closed for an encoded scalar under an unknown YAML tag.

    ``SafeLoader`` intentionally declines to construct custom tags. That must
    not let a tagged base64 scalar bypass the same boundary that protects the
    standard ``!!binary`` form. A custom tag is still tolerated when it carries
    ordinary, non-encoded prose; the narrow fail-closed rule applies only to a
    real decoded byte payload, which has no portable-contract role.
    """

    try:
        nodes = list(yaml.compose_all(document, Loader=yaml.SafeLoader))
    except (yaml.YAMLError, RecursionError, MemoryError, OverflowError, ValueError):
        return False
    stack: list[object] = [node for node in nodes if node is not None]
    visited: set[int] = set()
    scanned = 0
    while stack:
        node = stack.pop()
        node_id = id(node)
        if node_id in visited:
            continue
        visited.add(node_id)
        scanned += 1
        if scanned > 4096:
            return True
        tag = getattr(node, "tag", "")
        if isinstance(tag, str) and tag and not tag.startswith("tag:yaml.org,2002:"):
            if isinstance(node, yaml.ScalarNode):
                encoded = re.sub(r"[ \t\r\n]+", "", node.value)
                if len(encoded) > 8192:
                    return True
                if len(encoded) >= 8 and _PORTABLE_YAML_TAGGED_BASE64_VALUE_PATTERN.fullmatch(encoded):
                    padded = encoded + "=" * ((-len(encoded)) % 4)
                    try:
                        decoded = base64.b64decode(padded, validate=True)
                    except (ValueError, UnicodeError):
                        decoded = b""
                    if decoded:
                        # An unknown tag with decoded bytes is a non-portable
                        # opaque carrier even if the bytes happen not to spell
                        # one of today's lexical credential formats.
                        return True
            elif isinstance(node, yaml.SequenceNode):
                stack.extend(node.value)
            elif isinstance(node, yaml.MappingNode):
                for key_node, value_node in node.value:
                    stack.append(key_node)
                    stack.append(value_node)
            continue
        if isinstance(node, yaml.SequenceNode):
            stack.extend(node.value)
        elif isinstance(node, yaml.MappingNode):
            for key_node, value_node in node.value:
                stack.append(key_node)
                stack.append(value_node)
    return False


def _contains_portable_yaml_credential_mapping(
    text: str,
    *,
    _embedded_depth: int = 0,
    _embedded_seen: set[str] | None = None,
) -> bool:
    """Find credential carriers after safe YAML semantic resolution.

    This closes representation gaps that regexes cannot safely enumerate:
    anchors and aliases, explicit/tagged keys, flow mappings, and literal or
    folded scalars all resolve to ordinary mapping key/value pairs. The walk is
    bounded and cycle-safe because YAML aliases may construct recursive graphs.
    """

    # YAML block/folded scalars are text containers. A literal payload such as
    # ``config: |`` followed by an alias-backed ``token`` mapping cannot be
    # judged from the outer decoded object alone: SafeLoader correctly returns
    # that payload as a string. Re-scan only strings that visibly contain a
    # sensitive YAML key, sharing a small depth/identity budget so nested
    # configuration examples cannot turn the privacy boundary into unbounded
    # recursive parsing. Reaching the depth cap on a credential-shaped scalar
    # fails closed.
    if _embedded_depth > 8:
        return _looks_like_sensitive_yaml_mapping(text)
    embedded_seen = _embedded_seen if _embedded_seen is not None else set()
    try:
        documents = _portable_yaml_documents(text)
    except _PortableYamlCandidateLimitError:
        return True
    for document in documents:
        if _contains_portable_unknown_yaml_tag_payload(document):
            return True
        # SafeLoader represents ``!!set`` as a dictionary whose original
        # mapping values are discarded, and ``!!omap``/``!!pairs`` as tuples.
        # Those collection types erase the credential key-to-value association
        # before the normal object walk can inspect it. A portable source has
        # no reason to carry a real value through such a tagged collection, so
        # retain the raw semantic relation and fail closed when it is visibly
        # credential-shaped.
        if (
            _PORTABLE_YAML_ASSOCIATION_LOSS_TAG_PATTERN.search(document)
            and _looks_like_sensitive_yaml_mapping(document)
        ):
            return True
        try:
            decoded_documents = list(yaml.load_all(document, Loader=_PortableYamlLoader))
        except (yaml.YAMLError, RecursionError, MemoryError, OverflowError, ValueError):
            # Unknown tags, malformed aliases and duplicate keys must not turn
            # a credential-looking YAML document into a pass.  The narrow
            # candidate check keeps unrelated prose with a colon usable.
            if _looks_like_sensitive_yaml_mapping(document) and _looks_like_self_contained_yaml_document(document):
                return True
            continue
        if len(decoded_documents) > 32:
            return True
        for decoded in decoded_documents:
            stack: list[object] = [decoded]
            visited: set[int] = set()
            scanned = 0
            while stack:
                current = stack.pop()
                scanned += 1
                if scanned > 4096:
                    # An adversarial document does not become a pass merely by
                    # exceeding the semantic privacy scanner's bounded budget.
                    return True
                if isinstance(current, dict):
                    container_id = id(current)
                    if container_id in visited:
                        continue
                    visited.add(container_id)
                    for key, value in current.items():
                        if isinstance(key, bytes):
                            try:
                                decoded_key = key.decode("utf-8")
                            except UnicodeDecodeError:
                                # Opaque non-UTF-8 keys cannot spell one of
                                # the portable credential setting names.
                                # Keep scanning their values normally.
                                stack.append(value)
                                continue
                            if has_portable_sensitive_setting_value(decoded_key, value):
                                return True
                            stack.append(value)
                            continue
                        if not isinstance(key, str):
                            stack.append(value)
                            continue
                        if has_portable_sensitive_setting_value(key, value):
                            return True
                        stack.append(key)
                        stack.append(value)
                elif isinstance(current, (list, tuple, set)):
                    container_id = id(current)
                    if container_id in visited:
                        continue
                    visited.add(container_id)
                    stack.extend(current)
                elif isinstance(current, bytes):
                    # ``!!binary`` may decode to a source path, contact, or a
                    # complete alias-backed YAML example. SafeLoader gives us
                    # bytes here, so scan a UTF-8 payload as the text it
                    # represents; opaque bytes cannot establish a portable
                    # safe value and therefore fail closed.
                    try:
                        decoded_bytes = current.decode("utf-8")
                    except UnicodeDecodeError:
                        return True
                    if (
                        contains_portable_forbidden_control_character(decoded_bytes)
                        or contains_portable_unpaired_surrogate(decoded_bytes)
                        or contains_portable_local_reference(decoded_bytes)
                        or contains_portable_studio_reference(decoded_bytes)
                        or contains_portable_sensitive_value(decoded_bytes)
                    ):
                        return True
                elif isinstance(current, str) and _looks_like_sensitive_yaml_mapping(current):
                    if _embedded_depth >= 8:
                        return True
                    # A YAML alias can repeat the same scalar at several
                    # locations. Exact text identity is sufficient here: the
                    # scalar itself is immutable, and a single semantic scan
                    # decides it for every alias without redoing parser work.
                    if current in embedded_seen:
                        continue
                    embedded_seen.add(current)
                    if _contains_portable_yaml_credential_mapping(
                        current,
                        _embedded_depth=_embedded_depth + 1,
                        _embedded_seen=embedded_seen,
                    ):
                        return True
    return False


def is_portable_sensitive_setting_key(text: str) -> bool:
    """Return whether a decoded mapping key names a credential carrier.

    The direct text scanner cannot see the relationship between a decoded key
    and a separate scalar value.  Keep that relationship in this shared
    module so runtime packet validation and upload-package validation make the
    same decision, including when the key itself was percent/HTML escaped.
    """

    try:
        variants = portable_text_variants(text)
    except PortableTextDecodingError:
        return True
    return any(
        PORTABLE_SENSITIVE_SETTING_KEY_PATTERN.fullmatch(candidate.strip()) is not None
        for candidate in variants
    )


_PORTABLE_SCHEMA_DESCRIPTOR_KEYS = frozenset(
    {
        "$ref",
        "$schema",
        "$id",
        "$defs",
        "title",
        "description",
        "default",
        "examples",
        "type",
        "format",
        "pattern",
        "enum",
        "const",
        "items",
        "properties",
        "required",
        "additionalProperties",
        "oneOf",
        "anyOf",
        "allOf",
        "not",
        "if",
        "then",
        "else",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "uniqueItems",
        "readOnly",
        "writeOnly",
        "deprecated",
        "nullable",
    }
)
_PORTABLE_SCHEMA_LITERAL_VALUE_KEYS = frozenset({"default", "examples", "enum", "const"})
_PORTABLE_SCHEMA_DEFINITION_MAP_KEYS = frozenset({"$defs", "properties"})
_PORTABLE_SCHEMA_SUBSCHEMA_KEYS = frozenset({"items", "not", "if", "then", "else"})
_PORTABLE_SCHEMA_SUBSCHEMA_LIST_KEYS = frozenset({"oneOf", "anyOf", "allOf"})
_PORTABLE_SCHEMA_TEXT_KEYS = frozenset({"$ref", "$schema", "$id", "title", "description", "format", "pattern"})
_PORTABLE_SCHEMA_BOOLEAN_KEYS = frozenset(
    {"additionalProperties", "uniqueItems", "readOnly", "writeOnly", "deprecated", "nullable"}
)
_PORTABLE_SCHEMA_NUMBER_KEYS = frozenset({"minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"})
_PORTABLE_SCHEMA_TYPE_NAMES = frozenset({"array", "boolean", "integer", "null", "number", "object", "string"})
_PORTABLE_AUTHORIZATION_METADATA_STATES = frozenset(
    {"user_provided_current_task", "authorized_direct_bridge", "not_recorded"}
)


def _is_portable_schema_example_placeholder(value: object) -> bool:
    """Return whether a schema's literal example cannot carry a real secret."""

    if value is None:
        return True
    if isinstance(value, str):
        try:
            variants = portable_text_variants(value)
        except PortableTextDecodingError:
            return False
        candidate = variants[-1].strip()
        return (
            _PORTABLE_SAFE_CREDENTIAL_PLACEHOLDER_PATTERN.fullmatch(candidate) is not None
            or candidate in _PORTABLE_AUTHORIZATION_METADATA_STATES
        )
    if isinstance(value, (list, tuple)):
        return all(_is_portable_schema_example_placeholder(item) for item in value)
    return False


def _is_portable_schema_descriptor(value: object, *, _depth: int = 0) -> bool:
    """Allow a declarative schema under a sensitive key without its data.

    JSON Schema has both structural keywords and literal-value keywords. A
    key-only allowlist would wrongly treat ``token: {default: real-value}`` as
    metadata even though it carries exactly the value the portable boundary
    must exclude. Validate structure recursively and accept literal fields
    only when their values are explicit placeholders (or ``null``).
    """

    if not isinstance(value, dict) or _depth > 16:
        return False
    for name, item in value.items():
        if not isinstance(name, str):
            return False
        if name in _PORTABLE_SCHEMA_LITERAL_VALUE_KEYS:
            if not _is_portable_schema_example_placeholder(item):
                return False
            continue
        if name in _PORTABLE_SCHEMA_DEFINITION_MAP_KEYS:
            if not isinstance(item, dict) or not all(
                isinstance(property_name, str)
                and _is_portable_schema_descriptor(property_value, _depth=_depth + 1)
                for property_name, property_value in item.items()
            ):
                return False
            continue
        if name in _PORTABLE_SCHEMA_SUBSCHEMA_KEYS:
            if name == "items" and isinstance(item, (list, tuple)):
                is_valid = all(_is_portable_schema_descriptor(option, _depth=_depth + 1) for option in item)
            else:
                is_valid = _is_portable_schema_descriptor(item, _depth=_depth + 1)
            if not is_valid:
                return False
            continue
        if name in _PORTABLE_SCHEMA_SUBSCHEMA_LIST_KEYS:
            if not isinstance(item, (list, tuple)) or not all(
                _is_portable_schema_descriptor(option, _depth=_depth + 1) for option in item
            ):
                return False
            continue
        if name == "additionalProperties":
            if not isinstance(item, bool) and not _is_portable_schema_descriptor(item, _depth=_depth + 1):
                return False
            continue
        if name.startswith("x-"):
            # An extension's semantics are not portable-contract metadata.
            # Permit only an explicit placeholder/null, never an opaque value
            # or an arbitrary nested structure that can hide one.
            if not _is_portable_schema_example_placeholder(item):
                return False
            continue
        if name in _PORTABLE_SCHEMA_TEXT_KEYS:
            if not isinstance(item, str):
                return False
            continue
        if name == "type":
            if isinstance(item, str):
                if item not in _PORTABLE_SCHEMA_TYPE_NAMES:
                    return False
            elif not isinstance(item, (list, tuple)) or not item or not all(
                isinstance(type_name, str) and type_name in _PORTABLE_SCHEMA_TYPE_NAMES
                for type_name in item
            ):
                return False
            continue
        if name == "required":
            if not isinstance(item, (list, tuple)) or not all(isinstance(property_name, str) for property_name in item):
                return False
            continue
        if name in _PORTABLE_SCHEMA_BOOLEAN_KEYS:
            # ``additionalProperties`` handled its schema form above.
            if not isinstance(item, bool):
                return False
            continue
        if name in _PORTABLE_SCHEMA_NUMBER_KEYS:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                return False
            continue
        if name not in _PORTABLE_SCHEMA_DESCRIPTOR_KEYS:
            return False
    return True


def _looks_like_portable_documentation_slug(value: str) -> bool:
    """Return whether a long sensitive-route suffix is plainly prose-like.

    Public documentation can legitimately say ``/token=release-notes-2026``.
    Keep that escape hatch finite and reviewable rather than allowing arbitrary
    short alpha chunks that can also be an opaque credential split by hyphens.
    Opaque base64url/JWT values, long mixed components, percent escapes, and
    dotted values do not satisfy it.
    """

    if any(character in value for character in ".~%+"):
        return False
    parts = re.split(r"[-_]+", value.casefold())
    return bool(parts) and all(
        part in _PORTABLE_DOCUMENTATION_ROUTE_WORDS
        or _PORTABLE_DOCUMENTATION_ROUTE_VERSION_PATTERN.fullmatch(part) is not None
        for part in parts
    )


def _looks_like_portable_remote_path_credential(value: str) -> bool:
    """Recognize an opaque secret placed after a sensitive remote path name."""

    candidate = unquote(value).strip().rstrip(".,;:)]}")
    if candidate.casefold().startswith("bearer-"):
        candidate = candidate[7:]
    if not candidate or _PORTABLE_REMOTE_PATH_TOKEN_VALUE_PATTERN.fullmatch(candidate) is None:
        return False
    return not _looks_like_portable_documentation_slug(candidate.rstrip("="))


def _contains_portable_remote_path_credential(text: str) -> bool:
    """Check only a verified web URL path for an opaque sensitive carrier."""

    for match in PORTABLE_REMOTE_WEB_URL_PATTERN.finditer(text):
        try:
            parsed = urlsplit(match.group(0))
        except ValueError:
            continue
        if parsed.scheme.casefold() not in {"http", "https", "ws", "wss"} or not parsed.hostname:
            continue
        path = unquote(parsed.path)
        for pattern in (
            PORTABLE_REMOTE_PATH_CREDENTIAL_PATTERN,
            PORTABLE_REMOTE_PATH_CREDENTIAL_SEGMENT_PATTERN,
            PORTABLE_REMOTE_PATH_MATRIX_CREDENTIAL_PATTERN,
            PORTABLE_REMOTE_PATH_MATRIX_CREDENTIAL_SEGMENT_PATTERN,
        ):
            for credential_match in pattern.finditer(path):
                if _looks_like_portable_remote_path_credential(credential_match.group("value")):
                    return True
    return False


def _contains_portable_email_value(text: str) -> bool:
    """Find an ASCII, EAI, or IDN email after bounded visual normalization."""

    return any(
        PORTABLE_EMAIL_VALUE_PATTERN.search(normalized)
        for normalized in _portable_privacy_presentation_variants(text)
    )


def _contains_portable_remote_path_private_value(text: str) -> bool:
    """Reject explicit private values hidden in a verified remote URL path.

    Normal public route names remain exempt from the ordinary privacy scanner.
    An actual contact, cookie/header value, Bearer token, or data URI is never
    portable route metadata. Query and fragment values remain visible to the
    normal scanner and therefore do not need this exception-path check.
    """

    for match in PORTABLE_REMOTE_WEB_URL_PATTERN.finditer(text):
        try:
            parsed = urlsplit(match.group(0))
        except ValueError:
            continue
        if parsed.scheme.casefold() not in {"http", "https", "ws", "wss"} or not parsed.hostname:
            continue
        path = unquote(parsed.path)
        if (
            _contains_portable_email_value(path)
            or _contains_portable_phone_value(path)
            or _contains_portable_cookie_header(path)
            or PORTABLE_BEARER_VALUE_PATTERN.search(path) is not None
            or PORTABLE_DATA_URI_PATTERN.search(path) is not None
        ):
            return True
    return False


def _is_portable_basic_credential_payload(payload: str) -> bool:
    """Return whether a bounded base64 token decodes to ``user:password``."""

    if len(payload) > 8192:
        return True
    padded = payload + "=" * ((-len(payload)) % 4)
    try:
        decoded = base64.b64decode(padded, validate=True)
    except (ValueError, UnicodeError):
        return False
    return b":" in decoded


def _contains_portable_basic_authorization(text: str) -> bool:
    """Recognize an actual Basic user:password payload without flagging prose.

    A lexical ``Basic <letters>`` pattern would misclassify ordinary phrases
    such as “Basic education”. Decode only bounded base64-looking payloads and
    require the wire-level colon that separates Basic credentials.
    """

    for pattern in (_PORTABLE_BASIC_AUTH_CANDIDATE_PATTERN, _PORTABLE_BASIC_AUTH_PATH_CANDIDATE_PATTERN):
        for match in pattern.finditer(text):
            if _is_portable_basic_credential_payload(match.group("payload")):
                return True
    for url_match in PORTABLE_REMOTE_WEB_URL_PATTERN.finditer(text):
        try:
            parsed = urlsplit(url_match.group(0))
        except ValueError:
            continue
        if parsed.scheme.casefold() not in {"http", "https", "ws", "wss"} or not parsed.hostname:
            continue
        for component in (parsed.path, parsed.query, parsed.fragment):
            decoded_component = unquote(component)
            for pattern in (
                _PORTABLE_BASIC_AUTH_MATRIX_PATH_CANDIDATE_PATTERN,
                _PORTABLE_BASIC_AUTH_URL_COMPONENT_PATTERN,
            ):
                for match in pattern.finditer(decoded_component):
                    if _is_portable_basic_credential_payload(match.group("payload")):
                        return True
    return False


def _contains_portable_cookie_header(text: str) -> bool:
    """Return whether an HTTP Cookie/Set-Cookie header carries a real value.

    Cookie names are application-defined: limiting detection to a small list
    such as ``session`` or ``token`` leaves routine carriers like
    ``JSESSIONID`` and ``sid`` outside the portable boundary. A header is not
    needed in a reusable skill or handoff. Angle/braced placeholder forms
    remain usable in documentation examples, while every concrete first-party
    cookie pair is rejected. ``Set-Cookie`` attributes such as ``Path=/`` are
    not themselves treated as additional cookie pairs.
    """

    for match in _PORTABLE_COOKIE_HEADER_PATTERN.finditer(text):
        body = match.group("body")
        entries = [body.split(";", 1)[0]] if match.group("kind") else body.split(";")
        for entry in entries:
            if "=" not in entry:
                continue
            name, value = entry.split("=", 1)
            if _PORTABLE_COOKIE_NAME_PATTERN.fullmatch(name.strip()) is None:
                continue
            if _PORTABLE_SAFE_CREDENTIAL_PLACEHOLDER_PATTERN.fullmatch(value.strip()) is None:
                return True
    return False


def _contains_portable_phone_value(text: str) -> bool:
    """Run phone patterns after limited visual-token normalization."""

    return any(
        pattern.search(normalized)
        for normalized in _portable_privacy_presentation_variants(text)
        for pattern in PORTABLE_PHONE_VALUE_PATTERNS
    )


def has_portable_sensitive_setting_value(key: str, value: object) -> bool:
    """Return whether a direct decoded credential setting has a real value.

    Structured schemas can legitimately use a *nested object* named ``token``
    to describe a field.  A direct scalar, however, carries a setting value and
    must not enter either portable boundary.  Explicit placeholder spellings
    remain usable in examples; malformed over-encoded text fails closed.
    """

    if not is_portable_sensitive_setting_key(key) or value is None:
        return False
    if isinstance(value, str):
        try:
            variants = portable_text_variants(value)
        except PortableTextDecodingError:
            return True
        candidate = variants[-1].strip()
        return bool(candidate) and _PORTABLE_SAFE_CREDENTIAL_PLACEHOLDER_PATTERN.fullmatch(candidate) is None
    if isinstance(value, dict):
        # The portable guidance contract has an authorization *provenance*
        # object (state plus evidence). It is deliberately not an HTTP
        # Authorization header. Its nested values still pass through the
        # recursive scanner, so a Bearer value or other concrete carrier is
        # not exempted by this narrowly shaped metadata allowance.
        if (
            key.strip().casefold() == "authorization"
            and set(value) <= {"state", "evidence"}
            and isinstance(value.get("state"), str)
            and value["state"] in _PORTABLE_AUTHORIZATION_METADATA_STATES
            and isinstance(value.get("evidence"), list)
        ):
            return False
        # A declarative schema such as ``token: {type: string}`` is a field
        # description, not an assigned credential. Any other mapping is a
        # concrete carrier and fails closed rather than hiding a value in a
        # ``value``/``data`` subfield.
        return not _is_portable_schema_descriptor(value)
    if isinstance(value, (list, tuple, set)):
        # Empty list-shaped schema syntax is harmless; a populated sequence
        # under a credential key carries concrete values.
        return bool(value)
    # YAML safe tags can resolve to bytes, date/time or other scalar Python
    # values. They are just as concrete as str/bool/int and must not become a
    # bypass through an anchor alias.
    return True


def contains_portable_sensitive_value(text: str) -> bool:
    """Return whether portable text discloses a credential or contact value."""

    try:
        presentation_variants = portable_presentation_scan_variants(text)
        raw_variants = tuple(
            dict.fromkeys(
                normalized
                for candidate in presentation_variants
                for normalized in _portable_privacy_presentation_variants(candidate)
            )
        )
        variants = tuple(
            _mask_remote_web_url_paths(candidate, mask_authority=False)
            for candidate in reversed(raw_variants)
        )
    except PortableTextDecodingError:
        return True
    return (
        any(PORTABLE_HIGH_CONFIDENCE_SECRET_PATTERN.search(candidate) for candidate in raw_variants)
        or any(_contains_portable_remote_path_credential(candidate) for candidate in raw_variants)
        or any(_contains_portable_remote_path_private_value(candidate) for candidate in raw_variants)
        or any(_contains_portable_basic_authorization(candidate) for candidate in raw_variants)
        or any(
            any(pattern.search(candidate) for pattern, _label in PORTABLE_SENSITIVE_VALUE_PATTERNS)
            or _contains_portable_cookie_header(candidate)
            or _contains_portable_phone_value(candidate)
            or _contains_portable_yaml_credential_mapping(candidate)
            for candidate in variants
        )
    )
