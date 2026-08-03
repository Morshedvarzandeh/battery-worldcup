"""Sign a record so anyone can check it was not altered.

This is the cheapest possible fix for the most expensive problem in the
second-hand battery trade. A buyer looking at a seller's report has no way to
tell whether it describes the pack in front of them, whether the health figure
was edited on the way, or whether the whole document was invented. So they
assume the worst and discount accordingly, which is precisely why good packs
cannot fetch what they are worth.

A signature does not make the seller honest. It makes the record **tamper
evident and attributable**: this document came from this issuer, unchanged. That
is a smaller claim than it sounds and it is the one that matters, because it
moves the buyer's question from "is this person lying" to "do I trust the
issuer" -- and there are far fewer issuers than sellers.

Ed25519 over canonical JSON. Small keys, small signatures, no parameter choices
to get wrong, and verification needs nothing but the public key -- so a buyer
can check a certificate offline, from a phone, without asking us anything.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ENV_PRIVATE_KEY = "BV_SIGNING_KEY"
ENV_KEY_PATH = "BV_SIGNING_KEY_PATH"
ENV_ISSUER = "BV_ISSUER"

DEFAULT_ISSUER = "battery-value (unconfigured issuer)"

ALGORITHM = "Ed25519"


class SigningUnavailable(RuntimeError):
    """No signing key, or no crypto library to use one with."""


def canonical_json(payload: dict[str, Any]) -> bytes:
    """The exact bytes that get signed.

    Sorted keys, no insignificant whitespace, UTF-8. Two processes that agree on
    the content must produce identical bytes, or a valid certificate will fail
    to verify somewhere else and the whole thing is worthless.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _ed25519():
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:  # pragma: no cover - exercised by the extra
        raise SigningUnavailable(
            "signing needs the cryptography package; "
            "pip install 'battery-value[trust]'"
        ) from exc
    return ed25519


@dataclass(frozen=True, slots=True)
class Signature:
    """A signature, the key that made it, and the exact bytes it covers.

    ``payload`` carries those bytes rather than leaving them to be reproduced,
    and that is not belt-and-braces -- it is the only thing that makes this work
    across languages. Canonical JSON is not actually canonical once a value has
    been through another runtime: Python writes ``76.0`` where JavaScript writes
    ``76`` for the same number, so a certificate re-serialised by a browser
    stops matching a signature computed over Python's rendering of it. Carrying
    the signed bytes sidesteps the whole problem, exactly as JWS does.
    """

    algorithm: str
    public_key: str
    value: str
    issuer: str
    payload: str = ""
    """base64url of the exact bytes that were signed."""

    def to_dict(self) -> dict[str, Any]:
        """Serialise for embedding in a certificate."""
        return {
            "algorithm": self.algorithm,
            "public_key": self.public_key,
            "value": self.value,
            "issuer": self.issuer,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Signature:
        """Read a signature back off a certificate."""
        return cls(
            algorithm=str(raw.get("algorithm", ALGORITHM)),
            public_key=str(raw.get("public_key", "")),
            value=str(raw.get("value", "")),
            issuer=str(raw.get("issuer", "")),
            payload=str(raw.get("payload", "")),
        )

    def signed_payload(self) -> dict[str, Any] | None:
        """What was actually signed, parsed. ``None`` if it cannot be read."""
        if not self.payload:
            return None
        try:
            return json.loads(_unb64(self.payload).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None


class Signer:
    """Holds the issuing key and signs payloads with it.

    The key is found in this order, and the order matters: an operator who set
    an environment variable meant it, and a generated key must never silently
    replace a configured one.

    1. ``BV_SIGNING_KEY`` -- the private key seed, base64url.
    2. ``BV_SIGNING_KEY_PATH`` -- a file holding the same.
    3. A key generated on first use and written under the data directory.

    A generated key is fine for a single deployment and useless for anything
    federated, which is why it says so in the issuer name until one is
    configured.
    """

    def __init__(
        self,
        private_key_seed: bytes | None = None,
        *,
        issuer: str | None = None,
        key_path: str | Path | None = None,
    ) -> None:
        self._ed25519 = _ed25519()
        self.issuer = issuer or os.environ.get(ENV_ISSUER) or DEFAULT_ISSUER
        self._key_path = Path(key_path) if key_path else self._default_key_path()

        seed = private_key_seed or self._load_seed()
        self._key = self._ed25519.Ed25519PrivateKey.from_private_bytes(seed)

    # -- key handling ------------------------------------------------------

    @staticmethod
    def _default_key_path() -> Path:
        configured = os.environ.get(ENV_KEY_PATH)
        if configured:
            return Path(configured).expanduser()
        xdg = os.environ.get("XDG_DATA_HOME")
        root = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
        return root / "battery-value" / "signing.key"

    def _load_seed(self) -> bytes:
        configured = os.environ.get(ENV_PRIVATE_KEY)
        if configured:
            return _unb64(configured.strip())

        if self._key_path.exists():
            return _unb64(self._key_path.read_text(encoding="ascii").strip())

        key = self._ed25519.Ed25519PrivateKey.generate()
        seed = key.private_bytes_raw()
        try:
            self._key_path.parent.mkdir(parents=True, exist_ok=True)
            self._key_path.write_text(_b64(seed), encoding="ascii")
            self._key_path.chmod(0o600)
            logger.warning(
                "generated a new signing key at %s. Certificates signed with it "
                "verify only against this deployment; set %s to issue under a "
                "key you control.",
                self._key_path,
                ENV_PRIVATE_KEY,
            )
        except OSError as exc:
            logger.warning("could not persist the signing key (%s); "
                           "certificates issued now will not verify later", exc)
        return seed

    @property
    def public_key(self) -> str:
        """The public key, base64url. Publish this; verification needs nothing else."""
        return _b64(self._key.public_key().public_bytes_raw())

    # -- signing -----------------------------------------------------------

    def sign(self, payload: dict[str, Any]) -> Signature:
        """Sign a payload, carrying the signed bytes along with the signature."""
        body = canonical_json(payload)
        return Signature(
            algorithm=ALGORITHM,
            public_key=self.public_key,
            value=_b64(self._key.sign(body)),
            issuer=self.issuer,
            payload=_b64(body),
        )


def verify(payload: dict[str, Any], signature: Signature | dict[str, Any]) -> bool:
    """Check a payload against its signature.

    Two things have to hold, and the second is the one that is easy to forget:

    1. The signature is valid over the bytes the signature carries.
    2. Those bytes say the same thing as the document being displayed.

    Without (2) an attacker could sign one payload and show another beside it,
    and the certificate would verify while the reader looked at fiction. The
    comparison is between *parsed* values, so a runtime that writes ``76`` where
    Python wrote ``76.0`` is not treated as tampering -- which it is not.

    Needs no configuration and no network: the public key travels with the
    certificate, so a buyer holding the file can check it anywhere. Whether that
    key belongs to an issuer they trust is a separate question, and one this
    function deliberately does not answer.
    """
    if isinstance(signature, dict):
        signature = Signature.from_dict(signature)
    if signature.algorithm != ALGORITHM or not signature.value:
        return False

    body = _unb64(signature.payload) if signature.payload else canonical_json(payload)

    ed25519 = _ed25519()
    try:
        public = ed25519.Ed25519PublicKey.from_public_bytes(
            _unb64(signature.public_key)
        )
        public.verify(_unb64(signature.value), body)
    except Exception:  # noqa: BLE001 - any failure is a failed verification
        return False

    signed = signature.signed_payload()
    if signed is not None and signed != payload:
        logger.warning(
            "signature is valid but covers different content from the document "
            "it is attached to"
        )
        return False
    return True


def signing_available() -> bool:
    """Whether this install can sign at all."""
    try:
        _ed25519()
    except SigningUnavailable:
        return False
    return True


_default: Signer | None = None


def default_signer() -> Signer:
    """The process-wide signer."""
    global _default
    if _default is None:
        _default = Signer()
    return _default


def reset_default_signer() -> None:
    """Drop the cached signer. Used by tests."""
    global _default
    _default = None


__all__ = [
    "ALGORITHM",
    "Signature",
    "Signer",
    "SigningUnavailable",
    "canonical_json",
    "default_signer",
    "reset_default_signer",
    "signing_available",
    "verify",
]
