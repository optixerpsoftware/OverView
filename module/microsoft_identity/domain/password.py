"""Génération de mot de passe temporaire conforme à la politique Entra ID par défaut."""
import secrets

_LOWER = "abcdefghijkmnpqrstuvwxyz"       # sans l, o (ambigus)
_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"        # sans I, O
_DIGITS = "23456789"                       # sans 0, 1
_SYMBOLS = "!@#$%&*?-_=+"


def generate_password(length: int = 20) -> str:
    if length < 12:
        raise ValueError("Longueur minimale : 12.")
    chars = [secrets.choice(s) for s in (_LOWER, _UPPER, _DIGITS, _SYMBOLS)]
    pool = _LOWER + _UPPER + _DIGITS + _SYMBOLS
    chars += [secrets.choice(pool) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)
