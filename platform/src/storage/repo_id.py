"""Stable, collision-resistant repository ID derivation.

The old scheme used the folder name (e.g. ``my-project``), which collides
when two different repos share a name, and causes the second ingestion to
overwrite the first.

The new scheme hashes the *canonical source string* — the normalised URL or
absolute path.  Two users submitting the same GitHub URL converge on the same
``repo_id`` and share the same indexed data.  Two repos that happen to share a
folder name but come from different URLs get distinct IDs.

``canonical_source()`` is the single source of truth for normalization.
``derive_repo_id()`` is what every code path must call; it must never be
reimplemented inline.
"""

from __future__ import annotations

import hashlib
import re


# Length of the hex prefix used as the repo_id.
# 16 hex chars = 64 bits — collision probability negligible for any realistic
# number of repos (birthday bound: 50% collision at ~4 billion repos).
_ID_PREFIX_LEN = 16


def canonical_source(source: str) -> str:
    """Return the normalised form of a repository source string.

    Rules applied (in order):
    1. Strip leading/trailing whitespace.
    2. Lowercase.
    3. Remove trailing slashes.
    4. For GitHub/GitLab HTTPS URLs: strip ``.git`` suffix if present.
    5. Collapse repeated slashes in the path component (not the ``://``).
    6. Normalise ``http://`` → ``https://`` for GitHub/GitLab domains.

    These rules ensure that the following all produce the same canonical form
    and therefore the same ``repo_id``:
        https://github.com/acme/my-project
        https://github.com/acme/my-project/
        https://github.com/acme/my-project.git
        http://github.com/acme/my-project.git
        HTTP://GitHub.com/Acme/My-Project.git
    """
    s = source.strip().lower().rstrip("/")

    # Strip .git suffix (GitHub, GitLab, Bitbucket)
    if s.endswith(".git"):
        s = s[:-4]

    # Normalise http → https for known hosting domains
    s = re.sub(
        r"^http://(github\.com|gitlab\.com|bitbucket\.org)/",
        r"https://\1/",
        s,
    )

    # Collapse duplicate slashes after the scheme (e.g. https:///foo → https://foo)
    scheme_end = s.find("://")
    if scheme_end != -1:
        prefix = s[: scheme_end + 3]
        rest = s[scheme_end + 3 :]
        s = prefix + re.sub(r"/+", "/", rest)

    return s


def derive_repo_id(source: str) -> str:
    """Return a stable 16-char hex ID for a repository source string.

    The ID is the first ``_ID_PREFIX_LEN`` hex characters of the SHA-256
    hash of ``canonical_source(source)``.  It is:
    - Deterministic: same source always gives the same ID.
    - Collision-resistant: different sources (overwhelmingly) give different IDs.
    - Opaque: does not expose path/URL information in logs or API responses.
    - URL-safe: hex digits only, no special characters.
    """
    canon = canonical_source(source)
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    return digest[:_ID_PREFIX_LEN]


def repo_name_from_source(source: str) -> str:
    """Extract a human-readable repository name from a source string.

    For URLs: uses the last path component (the repo slug).
    For local paths: uses the directory name.

    Examples:
        https://github.com/acme/my-project  →  my-project
        /home/user/projects/my-project      →  my-project
        /home/user/projects/my-project/     →  my-project
    """
    canon = canonical_source(source)
    # Strip query/fragment if somehow present
    canon = canon.split("?")[0].split("#")[0]
    # Last non-empty path component
    parts = [p for p in canon.replace("\\", "/").split("/") if p]
    return parts[-1] if parts else canon
