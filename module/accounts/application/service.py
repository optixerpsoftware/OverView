"""Comptes OverView : connexion en deux étapes (mot de passe puis code TOTP), MFA, rôles, gestion des comptes."""
from __future__ import annotations

import os
import time
from datetime import timedelta
from typing import Optional

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from module.accounts.domain.access import ROLES, SELF_SERVICE, allowed, required_role, verify_totp
from module.accounts.infrastructure.models import OperatorProfile
from shared_kernel.totp import generate_secret, otpauth_uri

SALT = "overview.mfa-challenge"
CHALLENGE_AGE = 300          # secondes pour saisir le code après le mot de passe
MAX_FAILURES = 5
LOCK = timedelta(minutes=10)
ISSUER = "OverView"


class AuthFailed(Exception):
    pass


class AccountError(Exception):
    pass


def mfa_required_globally() -> bool:
    return os.environ.get("OVERVIEW_REQUIRE_MFA", "false").lower() in ("1", "true", "yes", "oui")


def profile_of(user) -> OperatorProfile:
    p, created = OperatorProfile.objects.get_or_create(
        user=user, defaults={"role": "admin" if user.is_superuser else "viewer"})
    return p


def role_of(user) -> str:
    if user.is_superuser:
        return "admin"
    p = OperatorProfile.objects.filter(user=user).values_list("role", flat=True).first()
    return p if p in ROLES else "viewer"


def _tokens(user) -> dict:
    r = RefreshToken.for_user(user)
    return {"access": str(r.access_token), "refresh": str(r)}


def check_access(user, method: str, path: str) -> Optional[tuple[int, str, str]]:
    """Contrôle d'accès appliqué à chaque appel d'API (vues async et DRF). None = autorisé."""
    if not path.startswith("/api/") or path.startswith("/api/auth/"):
        return None
    if mfa_required_globally() and not SELF_SERVICE.match(path):
        if not OperatorProfile.objects.filter(user=user, totp_enabled=True).exists():
            return 403, "Active d'abord le MFA de ton compte OverView (Mon compte).", "mfa_enrollment_required"
    role = role_of(user)
    if not allowed(role, method, path):
        need = required_role(method, path)
        return 403, f"Droits insuffisants : action réservée au rôle « {need} » (ton rôle : « {role} »).", "forbidden_role"
    return None


class AccountService:
    # ------------------------------------------------------------------ connexion
    @staticmethod
    def login(username: str, password: str) -> dict:
        user = authenticate(username=username, password=password)
        if user is None or not user.is_active:
            raise AuthFailed("Identifiants invalides.")
        p = profile_of(user)
        if p.locked_until and p.locked_until > timezone.now():
            raise AuthFailed("Compte temporairement verrouillé après plusieurs codes erronés. Réessaie dans quelques minutes.")
        if p.totp_enabled:
            token = signing.dumps({"u": user.pk, "t": int(time.time())}, salt=SALT)
            return {"mfa_required": True, "mfa_token": token}
        return {**_tokens(user), "mfa_setup_required": mfa_required_globally()}

    @staticmethod
    def verify(mfa_token: str, code: str, now: Optional[float] = None) -> dict:
        try:
            data = signing.loads(mfa_token, salt=SALT, max_age=CHALLENGE_AGE)
        except signing.BadSignature:
            raise AuthFailed("Session de connexion expirée : saisis de nouveau ton mot de passe.")
        with transaction.atomic():
            p = OperatorProfile.objects.select_for_update().select_related("user").filter(user_id=data["u"]).first()
            if p is None or not p.totp_enabled or not p.user.is_active:
                raise AuthFailed("Identifiants invalides.")
            if p.locked_until and p.locked_until > timezone.now():
                raise AuthFailed("Compte temporairement verrouillé. Réessaie dans quelques minutes.")
            counter = verify_totp(p.totp_secret, code, now or time.time(), p.totp_last_counter)
            if counter is None:
                # L'échec est enregistré (transaction validée) AVANT de lever l'erreur : sinon le verrouillage
                # après 5 essais ne s'enclencherait jamais.
                p.failed_mfa += 1
                if p.failed_mfa >= MAX_FAILURES:
                    p.locked_until, p.failed_mfa = timezone.now() + LOCK, 0
                p.save(update_fields=["failed_mfa", "locked_until", "modified_at"])
                ok = False
            else:
                p.totp_last_counter, p.failed_mfa, p.locked_until = counter, 0, None
                p.save(update_fields=["totp_last_counter", "failed_mfa", "locked_until", "modified_at"])
                ok = True
        if not ok:
            raise AuthFailed("Code invalide.")
        return _tokens(p.user)

    # ------------------------------------------------------------------ mon compte
    @staticmethod
    def me(user) -> dict:
        p = profile_of(user)
        return {"username": user.get_username(), "email": user.email, "role": role_of(user),
                "mfa_enabled": p.totp_enabled, "mfa_required": mfa_required_globally()}

    @staticmethod
    def mfa_setup(user) -> dict:
        p = profile_of(user)
        if p.totp_enabled:
            raise AccountError("Le MFA est déjà activé. Désactive-le d'abord pour changer d'appareil.")
        p.totp_secret = generate_secret()
        p.save(update_fields=["totp_secret", "modified_at"])
        return {"secret": p.totp_secret, "otpauth": otpauth_uri(ISSUER, user.get_username(), p.totp_secret)}

    @staticmethod
    def mfa_enable(user, code: str, now: Optional[float] = None) -> dict:
        p = profile_of(user)
        counter = verify_totp(p.totp_secret or "", code, now or time.time())
        if counter is None:
            raise AccountError("Code invalide : vérifie l'heure du téléphone et réessaie.")
        p.totp_enabled, p.totp_last_counter = True, counter
        p.save(update_fields=["totp_enabled", "totp_last_counter", "modified_at"])
        return AccountService.me(user)

    @staticmethod
    def mfa_disable(user, code: str, now: Optional[float] = None) -> dict:
        if mfa_required_globally():
            raise AccountError("Le MFA est obligatoire sur ce serveur : il ne peut pas être désactivé.")
        p = profile_of(user)
        if not p.totp_enabled or verify_totp(p.totp_secret, code, now or time.time(), p.totp_last_counter) is None:
            raise AccountError("Code invalide.")
        p.totp_enabled, p.totp_secret, p.totp_last_counter = False, None, None
        p.save(update_fields=["totp_enabled", "totp_secret", "totp_last_counter", "modified_at"])
        return AccountService.me(user)

    # ------------------------------------------------------------------ gestion des comptes (administrateurs)
    @staticmethod
    def users() -> list[dict]:
        out = []
        for u in get_user_model().objects.order_by("username"):
            p = OperatorProfile.objects.filter(user=u).first()
            out.append({"id": u.pk, "username": u.get_username(), "email": u.email, "active": u.is_active,
                        "role": role_of(u), "mfa_enabled": bool(p and p.totp_enabled), "superuser": u.is_superuser,
                        "last_login": u.last_login})
        return out

    @staticmethod
    def _admins_left(excluding_id) -> int:
        n = 0
        for u in get_user_model().objects.filter(is_active=True).exclude(pk=excluding_id):
            if role_of(u) == "admin":
                n += 1
        return n

    @staticmethod
    def create_user(username: str, password: str, role: str, email: str = "") -> dict:
        if role not in ROLES:
            raise AccountError("Rôle invalide.")
        username = (username or "").strip()
        if not username or get_user_model().objects.filter(username=username).exists():
            raise AccountError("Nom d'utilisateur vide ou déjà utilisé.")
        try:
            validate_password(password)
        except Exception as e:  # noqa: BLE001 : messages de validation Django
            raise AccountError(" ".join(getattr(e, "messages", [str(e)])))
        u = get_user_model().objects.create_user(username=username, password=password, email=email or "")
        OperatorProfile.objects.create(user=u, role=role)
        return next(x for x in AccountService.users() if x["id"] == u.pk)

    @staticmethod
    def update_user(actor, user_id: int, *, role: Optional[str] = None, active: Optional[bool] = None,
                    reset_mfa: bool = False) -> dict:
        u = get_user_model().objects.filter(pk=user_id).first()
        if u is None:
            raise AccountError("Compte introuvable.")
        if u.pk == actor.pk and (role not in (None, "admin") or active is False):
            raise AccountError("Tu ne peux pas retirer tes propres droits d'administrateur ni désactiver ton compte.")
        demoting = (role is not None and role != "admin") or active is False
        if demoting and role_of(u) == "admin" and AccountService._admins_left(u.pk) == 0:
            raise AccountError("Il doit rester au moins un administrateur actif.")
        p = profile_of(u)
        if role is not None:
            if role not in ROLES:
                raise AccountError("Rôle invalide.")
            if u.is_superuser and role != "admin":
                u.is_superuser = False
            p.role = role
        if active is not None:
            u.is_active = bool(active)
        if reset_mfa:
            p.totp_enabled, p.totp_secret, p.totp_last_counter = False, None, None
        u.save()
        p.save()
        return next(x for x in AccountService.users() if x["id"] == u.pk)
