"""Pro entitlement resolution and feature gating (open-core).

The redcon core is free and stays dependency-free. Paid ("Pro") features - the
savings dashboard, cross-agent history, prompt-optimization insights - are
unlocked by a signed license key that redcon-cloud issues on purchase.

Design:

- Verification is OFFLINE. The core embeds only the Ed25519 *public* key; the
  private key never leaves redcon-cloud, so Pro works on a plane and never
  phones home.
- A license is UNFORGEABLE without the private key, so "holds a valid license"
  is proof of purchase - even though the local gate itself, being open source,
  can always be patched out by a determined user. The real paid value lives
  server-side in redcon-cloud; the local gate only stops casual bypass, which
  is the honest ceiling for any open-core license.
- Signature verification needs ``cryptography``, shipped in the optional
  ``pro`` extra. Free installs never pull it. If a license is present but the
  library is missing, we degrade to free and tell the user how to fix it
  rather than granting an unverifiable Pro.
- Until redcon-cloud mints the production keypair, the embedded public key is
  empty. With no key configured, every license is treated as unverified and
  the user stays free - the correct pre-launch state.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

TIER_FREE = "free"
TIER_PRO = "pro"

# Feature slugs gated behind Pro. Anything not listed here is free and always
# available. Keep the slugs stable - the dashboard, extension, and cloud all
# check them by name.
PRO_FEATURES: frozenset[str] = frozenset(
    {
        "dashboard.savings",  # the savings / cross-agent history dashboard
        "history.unlimited",  # run history beyond the free retention window
        "insights.prompt",  # prompt-optimization suggestions
        "compression.max",  # the max-compression profile (tighter tier thresholds)
    }
)

# Ed25519 public key (base64url of the raw 32 bytes) used to verify license
# signatures. The matching private key lives ONLY in redcon-cloud and signs
# licenses on purchase. Empty means "no key configured yet": every license is
# treated as unverified and the user stays free. Override for tests or a
# self-hosted signer via REDCON_LICENSE_PUBKEY.
_EMBEDDED_PUBLIC_KEY_B64 = ""

_ENV_LICENSE = "REDCON_LICENSE_KEY"
_ENV_PUBKEY = "REDCON_LICENSE_PUBKEY"
_LICENSE_FILENAMES = ("license", "license.key")

_PRO_HINT = "License found but verification needs cryptography - run: pip install 'redcon[pro]'"


@dataclass(frozen=True, slots=True)
class Entitlement:
    """The resolved plan for the current environment.

    ``status`` is the machine-readable outcome:

    - ``free``       - no license present (or a valid free-tier license)
    - ``active``     - a valid, unexpired Pro license
    - ``expired``    - a Pro license whose term has passed
    - ``invalid``    - a malformed license or a signature that did not verify
    - ``unverified`` - a license we could not check (no public key configured,
      or the ``pro`` extra is not installed)
    """

    tier: str = TIER_FREE
    email: str = ""
    expires_at: int = 0  # unix seconds; 0 = no expiry
    features: frozenset[str] = field(default_factory=frozenset)
    source: str = "none"  # none | env | file
    status: str = "free"
    hint: str = ""

    @property
    def is_pro(self) -> bool:
        """True only for a verified, unexpired Pro license."""
        return self.tier == TIER_PRO and self.status == "active"

    def has(self, feature: str) -> bool:
        """Whether ``feature`` is available under this entitlement.

        Free (ungated) features are available to everyone; Pro features only
        when this entitlement actually grants them.
        """
        return feature not in PRO_FEATURES or feature in self.features


def _b64d(value: str) -> bytes:
    """Decode unpadded/padded base64url text to bytes."""
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def _now() -> int:
    return int(time.time())


def _public_key_b64() -> str:
    return os.environ.get(_ENV_PUBKEY, "").strip() or _EMBEDDED_PUBLIC_KEY_B64


def _read_license_token(repo: Path | None) -> tuple[str, str]:
    """Return ``(token, source)`` from the env var, then a repo-local or
    home ``.redcon`` license file. Empty token means no license."""
    env = os.environ.get(_ENV_LICENSE, "").strip()
    if env:
        return env, "env"

    candidates: list[Path] = []
    if repo is not None:
        candidates += [repo / ".redcon" / name for name in _LICENSE_FILENAMES]
    home = Path.home() / ".redcon"
    candidates += [home / name for name in _LICENSE_FILENAMES]
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text, "file"
    return "", "none"


def _verify_token(token: str) -> Entitlement:
    """Verify a license token and return the entitlement it grants.

    Never raises: any failure resolves to a free/invalid/unverified
    entitlement so the CLI keeps working no matter what the user pastes.
    """
    parts = token.split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return Entitlement(status="invalid", hint="License key is malformed.")
    payload_b64, sig_b64 = parts

    pub_b64 = _public_key_b64()
    if not pub_b64:
        return Entitlement(status="unverified", hint="Pro licenses are not enabled in this build.")

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except Exception:  # noqa: BLE001 - any import/runtime failure means "cannot verify"
        return Entitlement(status="unverified", hint=_PRO_HINT)

    try:
        public_key = Ed25519PublicKey.from_public_bytes(_b64d(pub_b64))
        public_key.verify(_b64d(sig_b64), payload_b64.encode("ascii"))
    except InvalidSignature:
        return Entitlement(status="invalid", hint="License signature did not verify.")
    except (ValueError, TypeError):
        return Entitlement(status="invalid", hint="License key is malformed.")

    try:
        data = json.loads(_b64d(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return Entitlement(status="invalid", hint="License payload is unreadable.")

    tier = str(data.get("tier", TIER_FREE))
    email = str(data.get("email", ""))
    exp = int(data.get("exp", 0) or 0)

    if tier != TIER_PRO:
        return Entitlement(tier=TIER_FREE, email=email, status="free")
    if exp and exp < _now():
        return Entitlement(
            tier=TIER_PRO,
            email=email,
            expires_at=exp,
            status="expired",
            hint="License expired - renew to restore Pro.",
        )
    return Entitlement(
        tier=TIER_PRO,
        email=email,
        expires_at=exp,
        features=PRO_FEATURES,
        status="active",
    )


def load_entitlement(repo: str | Path | None = None) -> Entitlement:
    """Resolve the current entitlement from env or a ``.redcon`` license file.

    Returns a free entitlement when no license is present. A present-but-
    unusable license (invalid, expired, unverifiable) also resolves to free
    for feature purposes, with ``status``/``hint`` explaining why.
    """
    repo_path = Path(repo) if repo is not None else None
    token, source = _read_license_token(repo_path)
    if not token:
        return Entitlement()
    return replace(_verify_token(token), source=source)
