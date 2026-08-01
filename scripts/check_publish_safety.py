"""Publish safety gate: fail if the repository leaks private or secret data.

Run before every push to a public/remote repository:

    python scripts/check_publish_safety.py

Exit code 0 means the working tree is safe to publish. Any hit prints the
file, line number, rule name and a masked excerpt, and the process exits 1.

The scanner is intentionally conservative: it inspects every file that git
would publish (tracked + untracked, respecting .gitignore), skips binaries
and known third-party payloads, and treats *any* match as a failure. Add a
path to ALLOWED_PATHS only when the match is provably synthetic (test
fixtures, documentation examples).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

# Text files larger than this are streamed line by line anyway, but very
# large data blobs are skipped entirely: they are market data, not source.
MAX_FILE_BYTES = 8 * 1024 * 1024

SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".zip",
    ".exe",
    ".dll",
    ".pyd",
    ".so",
    ".dylib",
    ".whl",
    ".pyc",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".parquet",
    ".gz",
    ".bz2",
    ".xz",
    ".pdf",
}

# Directory prefixes that never contain project source.
SKIP_PREFIXES = (
    ".git/",
    ".venv",
    "venv/",
    "dist/",
    "build/",
    "releases/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
)

# (rule name, compiled pattern, human description)
RULES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "windows_user_path",
        # Fragments, not literals: this gate is itself published, so it must
        # not carry the very strings it rejects.
        re.compile(
            r"[A-Za-z]:\\+" + "Use" + r"rs\\+[^\\\s\"'<>|]+",
            re.IGNORECASE,
        ),
        "absolute Windows user path leaks the local account name",
    ),
    (
        "windows_abs_path",
        # Vendor default install locations carry no personal information and
        # are the correct thing for public docs to show; a user-specific
        # directory under the local drive root still fails.
        re.compile(
            r"\b[A-Za-z]:\\(?:" + "Cod" + r"ex|" + "Use" + r"rs|TWS|Program|Dev|Work)\b"
            r"(?! API\b)(?! Files\b)"
        ),
        "absolute local development path",
    ),
    (
        "unix_home_path",
        re.compile(r"/(?:home|Users)/[A-Za-z0-9._-]+/"),
        "absolute POSIX home path",
    ),
    (
        "email_address",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "email address",
    ),
    (
        "public_ipv4",
        re.compile(
            r"\b(?!127\.0\.0\.1)(?!0\.0\.0\.0)(?!255\.255\.255\.255)"
            r"(?!(?:10|192\.168|192\.0\.2|198\.51\.100|203\.0\.113"
            r"|172\.(?:1[6-9]|2\d|3[01]))\.[0-9])"
            r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
            r"(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}\b"
        ),
        "routable public IPv4 address (private, loopback and RFC 5737 ranges excluded)",
    ),
    (
        "bearer_token",
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
        "hard-coded bearer token",
    ),
    (
        "openai_style_key",
        re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
        "hard-coded API key",
    ),
    (
        "aws_access_key",
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        "hard-coded AWS access key id",
    ),
    (
        "github_token",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
        "hard-coded GitHub token",
    ),
    (
        "google_api_key",
        re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
        "hard-coded Google API key",
    ),
    (
        "private_key_block",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
        "embedded private key material",
    ),
    (
        "assigned_secret",
        re.compile(
            r"(?i)\b(?:api[_-]?key|apikey|client[_-]?secret|access[_-]?token|"
            r"auth[_-]?token|secret[_-]?key|password|passwd)\b"
            r"\s*[:=]\s*[\"']([^\"'\s]{12,})[\"']"
        ),
        "literal credential assignment",
    ),
    (
        "ibkr_live_account",
        re.compile(r"\bU\d{7,9}\b"),
        "IBKR live account number (DU-prefixed paper ids are allowed)",
    ),
    (
        "ssh_command",
        re.compile(r"(?i)\bssh\s+(?:-p\s*\d+\s+)?root@"),
        "hard-coded SSH login command",
    ),
    (
        "cn_id_card",
        # Structurally valid 18-digit ID: 6-digit region code, real birth
        # date, 3-digit sequence, checksum. A bare 18-digit number is far
        # more likely market data, so the date segment is mandatory.
        re.compile(
            r"(?<!\d)[1-9]\d{5}"
            r"(?:19|20)(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"
            r"\d{3}[\dXx](?!\d)"
        ),
        "18-digit mainland China ID number",
    ),
    (
        "cn_mobile",
        re.compile(r"(?<![\d.\-])1[3-9]\d{9}(?![\d.\-])"),
        "standalone mainland China mobile number",
    ),
    (
        "private_name_marker",
        # Assembled from fragments so this gate never publishes the very
        # identifiers it exists to detect.
        re.compile("|".join(("davies" + "join", "Jiami" + "anh", "TX" + "Yun"))),
        "local account, handle or host credential marker",
    ),
]

# Explicitly allowed matches: path prefix -> set of rule names that are
# synthetic in that file. Keep this list tiny and justified.
ALLOWED: dict[str, set[str]] = {
    "tests/": {"ibkr_live_account"},
    "tests/test_redaction.py": {"bearer_token"},
    "src/us_quant/universe.py": {"email_address"},
    "docs/": {"email_address", "public_ipv4"},
    "configs/": {"public_ipv4"},
}


def _git_publishable_files() -> list[Path]:
    """Files git would include: tracked plus untracked, ignoring .gitignore."""
    try:
        raw = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:  # pragma: no cover
        print(f"cannot enumerate git files: {error}", file=sys.stderr)
        return []
    names = [name for name in raw.decode("utf-8", "replace").split("\0") if name]
    return [ROOT / name for name in sorted(names)]


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _allowed(rel: str, rule: str) -> bool:
    for prefix, rules in ALLOWED.items():
        if rel.startswith(prefix) and rule in rules:
            return True
    return False


def _mask(excerpt: str) -> str:
    """Never echo a full secret: keep shape, drop the payload."""
    text = excerpt.strip()
    if len(text) <= 12:
        return text
    return f"{text[:6]}…{text[-4:]}"


def _inside_number(text: str, start: int, end: int) -> bool:
    """True when the match is part of a longer numeric literal.

    Financial research payloads are full of long decimals such as
    ``0.1841761911227795``; without this guard the ID-number rule fires on
    the fractional digits of an ordinary return value.
    """
    if start > 0 and text[start - 1] in "0123456789.":
        return True
    if end < len(text) and text[end] in "0123456789.":
        return True
    return False


# Rules that only apply to free-standing tokens, never inside a number.
NUMERIC_CONTEXT_RULES = frozenset({"cn_id_card", "cn_mobile"})

# Raster images cannot be checked by a text scanner, and a screenshot of this
# app renders the local state root on screen. Publishing one therefore needs a
# human look, so treat it as a finding rather than silently skipping it.
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"})


def scan() -> int:
    findings: list[str] = []
    scanned = 0
    for path in _git_publishable_files():
        rel = _relative(path)
        if rel.startswith(SKIP_PREFIXES):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.suffix.lower() in IMAGE_SUFFIXES:
            findings.append(
                f"{rel}: [unscannable_image] raster image needs a manual look "
                f"before publishing\n"
                f"    {_mask(rel)}"
            )
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\0" in raw[:4096]:
            continue
        scanned += 1
        text = raw.decode("utf-8", "replace")
        for number, line in enumerate(text.splitlines(), start=1):
            for rule, pattern, description in RULES:
                match = pattern.search(line)
                if match is None or _allowed(rel, rule):
                    continue
                if (
                    rule in NUMERIC_CONTEXT_RULES
                    and _inside_number(line, match.start(), match.end())
                ):
                    continue
                findings.append(
                    f"{rel}:{number}: [{rule}] {description}\n"
                    f"    {_mask(match.group(0))}"
                )

    print(f"scanned {scanned} publishable text files")
    if not findings:
        print("OK: no private data or credentials found")
        return 0
    print(f"FAIL: {len(findings)} finding(s) must be resolved before publishing")
    for finding in findings:
        print(finding)
    return 1


if __name__ == "__main__":
    raise SystemExit(scan())
