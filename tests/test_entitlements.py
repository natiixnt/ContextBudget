"""Pro entitlement resolution and offline license verification."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

crypto_ed = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402

from redcon import entitlements  # noqa: E402
from redcon.entitlements import _PRO_HINT, Entitlement, load_entitlement  # noqa: E402

# A fixed far-future expiry (2100-01-01) so tests never race the clock.
FUTURE = 4_102_444_800
PAST = 100


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _make_key() -> tuple[crypto_ed.Ed25519PrivateKey, str]:
    priv = crypto_ed.Ed25519PrivateKey.generate()
    pub_b64 = _b64e(priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    return priv, pub_b64


def _sign(priv: crypto_ed.Ed25519PrivateKey, **payload: object) -> str:
    payload_b64 = _b64e(json.dumps(payload).encode("utf-8"))
    sig_b64 = _b64e(priv.sign(payload_b64.encode("ascii")))
    return f"{payload_b64}.{sig_b64}"


@pytest.fixture
def iso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate license resolution: empty home, no ambient env license."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(entitlements.Path, "home", staticmethod(lambda: home))
    monkeypatch.delenv("REDCON_LICENSE_KEY", raising=False)
    monkeypatch.delenv("REDCON_LICENSE_PUBKEY", raising=False)
    return tmp_path


def test_no_license_is_free(iso: Path):
    ent = load_entitlement(iso)
    assert ent.status == "free"
    assert not ent.is_pro
    assert ent.has("pack")  # a free (ungated) feature
    assert not ent.has("dashboard.savings")  # a Pro feature


def test_valid_pro_license_unlocks_features(iso: Path, monkeypatch: pytest.MonkeyPatch):
    priv, pub = _make_key()
    monkeypatch.setenv("REDCON_LICENSE_PUBKEY", pub)
    monkeypatch.setenv("REDCON_LICENSE_KEY", _sign(priv, tier="pro", email="a@b.c", exp=FUTURE))

    ent = load_entitlement(iso)
    assert ent.is_pro
    assert ent.status == "active"
    assert ent.email == "a@b.c"
    assert ent.source == "env"
    assert ent.has("dashboard.savings")
    assert ent.has("history.unlimited")


def test_expired_license_does_not_grant_pro(iso: Path, monkeypatch: pytest.MonkeyPatch):
    priv, pub = _make_key()
    monkeypatch.setenv("REDCON_LICENSE_PUBKEY", pub)
    monkeypatch.setenv("REDCON_LICENSE_KEY", _sign(priv, tier="pro", email="a@b.c", exp=PAST))

    ent = load_entitlement(iso)
    assert ent.status == "expired"
    assert not ent.is_pro
    assert not ent.has("dashboard.savings")


def test_tampered_payload_is_invalid(iso: Path, monkeypatch: pytest.MonkeyPatch):
    priv, pub = _make_key()
    good = _sign(priv, tier="pro", email="a@b.c", exp=FUTURE)
    # Swap in a payload that claims pro but was never signed.
    forged_payload = _b64e(json.dumps({"tier": "pro", "email": "evil@x", "exp": FUTURE}).encode())
    tampered = f"{forged_payload}.{good.split('.')[1]}"
    monkeypatch.setenv("REDCON_LICENSE_PUBKEY", pub)
    monkeypatch.setenv("REDCON_LICENSE_KEY", tampered)

    ent = load_entitlement(iso)
    assert ent.status == "invalid"
    assert not ent.is_pro


def test_license_signed_by_other_key_is_invalid(iso: Path, monkeypatch: pytest.MonkeyPatch):
    priv, _ = _make_key()
    _, other_pub = _make_key()
    monkeypatch.setenv("REDCON_LICENSE_PUBKEY", other_pub)
    monkeypatch.setenv("REDCON_LICENSE_KEY", _sign(priv, tier="pro", email="a@b.c", exp=FUTURE))

    ent = load_entitlement(iso)
    assert ent.status == "invalid"
    assert not ent.is_pro


def test_free_tier_license_stays_free(iso: Path, monkeypatch: pytest.MonkeyPatch):
    priv, pub = _make_key()
    monkeypatch.setenv("REDCON_LICENSE_PUBKEY", pub)
    monkeypatch.setenv("REDCON_LICENSE_KEY", _sign(priv, tier="free", email="a@b.c"))

    ent = load_entitlement(iso)
    assert ent.status == "free"
    assert not ent.is_pro


@pytest.mark.parametrize("bad", ["garbage", "onlyonepart", "a.b.c", ".", "a.", ".b"])
def test_malformed_key_is_invalid(iso: Path, monkeypatch: pytest.MonkeyPatch, bad: str):
    _, pub = _make_key()
    monkeypatch.setenv("REDCON_LICENSE_PUBKEY", pub)
    monkeypatch.setenv("REDCON_LICENSE_KEY", bad)

    ent = load_entitlement(iso)
    assert ent.status == "invalid"
    assert not ent.is_pro


def test_no_pubkey_configured_treats_license_as_unverified(
    iso: Path, monkeypatch: pytest.MonkeyPatch
):
    # Pre-launch state: a key is pasted but the build ships no public key.
    priv, _ = _make_key()
    monkeypatch.setenv("REDCON_LICENSE_KEY", _sign(priv, tier="pro", email="a@b.c", exp=FUTURE))
    monkeypatch.setattr(entitlements, "_EMBEDDED_PUBLIC_KEY_B64", "")

    ent = load_entitlement(iso)
    assert ent.status == "unverified"
    assert not ent.is_pro


def test_degrades_to_unverified_without_cryptography(iso: Path, monkeypatch: pytest.MonkeyPatch):
    priv, pub = _make_key()
    monkeypatch.setenv("REDCON_LICENSE_PUBKEY", pub)
    monkeypatch.setenv("REDCON_LICENSE_KEY", _sign(priv, tier="pro", email="a@b.c", exp=FUTURE))
    # Simulate the `pro` extra not being installed: the submodules
    # _verify_token imports resolve to None in sys.modules, so `from ... import`
    # raises ImportError just as a missing package would.
    monkeypatch.setitem(sys.modules, "cryptography.exceptions", None)
    monkeypatch.setitem(sys.modules, "cryptography.hazmat.primitives.asymmetric.ed25519", None)

    ent = load_entitlement(iso)
    assert ent.status == "unverified"
    assert ent.hint == _PRO_HINT
    assert not ent.is_pro


def test_license_read_from_repo_file(iso: Path, monkeypatch: pytest.MonkeyPatch):
    priv, pub = _make_key()
    monkeypatch.setenv("REDCON_LICENSE_PUBKEY", pub)
    redcon_dir = iso / ".redcon"
    redcon_dir.mkdir()
    (redcon_dir / "license").write_text(
        _sign(priv, tier="pro", email="a@b.c", exp=FUTURE) + "\n", encoding="utf-8"
    )

    ent = load_entitlement(iso)
    assert ent.is_pro
    assert ent.source == "file"


def test_env_license_beats_file(iso: Path, monkeypatch: pytest.MonkeyPatch):
    priv, pub = _make_key()
    monkeypatch.setenv("REDCON_LICENSE_PUBKEY", pub)
    redcon_dir = iso / ".redcon"
    redcon_dir.mkdir()
    (redcon_dir / "license").write_text(_sign(priv, tier="free", email="file@x"), encoding="utf-8")
    monkeypatch.setenv("REDCON_LICENSE_KEY", _sign(priv, tier="pro", email="env@x", exp=FUTURE))

    ent = load_entitlement(iso)
    assert ent.is_pro
    assert ent.email == "env@x"
    assert ent.source == "env"


def test_entitlement_has_is_permissive_for_ungated_features():
    # A bare free entitlement grants everything that is not a Pro feature.
    ent = Entitlement()
    assert ent.has("pack")
    assert ent.has("plan")
    assert not ent.has("insights.prompt")
