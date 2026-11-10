"""TOTP (RFC 6238) : génération de clé secrète, calcul de code et URI de configuration.

Sert à enregistrer une application TOTP (Microsoft Authenticator, Google Authenticator, Authy…) pour un
compte : on génère la clé, on la déclare chez Microsoft (jeton OATH) avec un premier code valide, et on
remet la clé à l'utilisateur (QR code). La clé est aussi sensible qu'un mot de passe : jamais journalisée."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Optional
from urllib.parse import quote


def generate_secret(nbytes: int = 20) -> str:
    """Clé secrète base32 (20 octets = 160 bits, la taille recommandée pour HMAC-SHA1), sans remplissage."""
    return base64.b32encode(secrets.token_bytes(nbytes)).decode().rstrip("=")


def totp_code(secret_b32: str, at: Optional[float] = None, interval: int = 30, digits: int = 6) -> str:
    """Code TOTP à l'instant `at` (secondes epoch ; maintenant par défaut)."""
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8), casefold=True)
    counter = int((time.time() if at is None else at) // interval)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** digits).zfill(digits)


def otpauth_uri(issuer: str, account: str, secret_b32: str, interval: int = 30, digits: int = 6) -> str:
    """URI `otpauth://` lue par les applications d'authentification (QR code)."""
    label = quote(f"{issuer}:{account}", safe="")
    return (f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer, safe='')}"
            f"&algorithm=SHA1&digits={digits}&period={interval}")
