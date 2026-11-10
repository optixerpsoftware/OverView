"""Console de la plateforme : super-administrateurs (MFA obligatoire) et gestion des MSP."""
from __future__ import annotations

import re
import time
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from module.accounts.domain.access import verify_totp
from module.platform.models import Domain, Msp, PlatformAdmin, PlatformAudit, PlatformSetting
from shared_kernel.tenancy import PUBLIC, TENANT_CLAIM, clear_domain_cache, use_schema
from shared_kernel.totp import generate_secret, otpauth_uri

SALT = "overview.platform-mfa"
DOMAIN = re.compile(r"^(?=.{3,253}$)([a-z0-9-]{1,63}\.)*[a-z0-9-]{1,63}$")


class PlatformAuthFailed(Exception):
    pass


class PlatformError(Exception):
    pass


def is_platform_admin(user) -> bool:
    with use_schema(PUBLIC):
        return bool(user and user.is_authenticated and PlatformAdmin.objects.filter(user_id=user.pk, user__is_active=True).exists())


def audit(actor: str, action: str, target: str = "", detail: str = "") -> None:
    with use_schema(PUBLIC):
        PlatformAudit.objects.create(actor=actor[:150], action=action[:60], target=target[:200], detail=detail[:2000])


# ---------------------------------------------------------------- super-administrateurs
def create_platform_admin(username: str, password: str, email: str = "") -> dict:
    """Crée un super-administrateur et renvoie sa clé TOTP (à scanner immédiatement : affichée une seule fois)."""
    try:
        validate_password(password)
    except ValidationError as e:
        raise PlatformError(" ".join(e.messages)) from e
    with use_schema(PUBLIC):
        User = get_user_model()
        if User.objects.filter(username=username).exists():
            raise PlatformError("Nom d'utilisateur déjà utilisé.")
        with transaction.atomic():
            u = User.objects.create_user(username=username, password=password, email=email)
            secret = generate_secret()
            PlatformAdmin.objects.create(user=u, totp_secret=secret)
    return {"username": username, "secret": secret, "otpauth": otpauth_uri("OverView Plateforme", username, secret)}


def login(username: str, password: str) -> dict:
    with use_schema(PUBLIC):
        user = authenticate(username=username, password=password)
        pa = PlatformAdmin.objects.filter(user=user).first() if user else None
        if pa is None or not user.is_active:
            raise PlatformAuthFailed("Identifiants invalides.")
        if pa.locked_until and pa.locked_until > timezone.now():
            raise PlatformAuthFailed("Compte verrouillé quelques minutes après plusieurs codes erronés.")
        return {"mfa_required": True, "mfa_token": signing.dumps({"u": user.pk}, salt=SALT)}


def verify(mfa_token: str, code: str, now: Optional[float] = None) -> dict:
    try:
        uid = signing.loads(mfa_token, salt=SALT, max_age=300)["u"]
    except signing.BadSignature:
        raise PlatformAuthFailed("Session de connexion expirée : recommence.")
    with use_schema(PUBLIC):
        with transaction.atomic():
            pa = PlatformAdmin.objects.select_for_update().select_related("user").filter(user_id=uid).first()
            if pa is None or (pa.locked_until and pa.locked_until > timezone.now()):
                raise PlatformAuthFailed("Identifiants invalides ou compte verrouillé.")
            counter = verify_totp(pa.totp_secret, code, now or time.time(), pa.totp_last_counter)
            if counter is None:
                pa.failed += 1
                if pa.failed >= 5:
                    pa.locked_until, pa.failed = timezone.now() + timedelta(minutes=10), 0
                pa.save(update_fields=["failed", "locked_until"])
                ok = False
            else:
                pa.totp_last_counter, pa.failed, pa.locked_until = counter, 0, None
                pa.save(update_fields=["totp_last_counter", "failed", "locked_until"])
                ok = True
        if not ok:
            raise PlatformAuthFailed("Code invalide.")
        r = RefreshToken.for_user(pa.user)
        r[TENANT_CLAIM] = PUBLIC
        audit(pa.user.get_username(), "login")
        return {"access": str(r.access_token), "refresh": str(r)}


def admins() -> list[dict]:
    with use_schema(PUBLIC):
        return [{"id": p.user_id, "username": p.user.get_username(), "email": p.user.email, "active": p.user.is_active,
                 "created_at": p.created_at, "last_login": p.user.last_login}
                for p in PlatformAdmin.objects.select_related("user").order_by("user__username")]


# ---------------------------------------------------------------- MSP
def _stats(schema: str) -> dict:
    """Quelques chiffres lus dans le schéma du MSP (sans rien modifier)."""
    from django.contrib.auth import get_user_model as gum
    from module.insights.infrastructure.models import Alert
    from module.microsoft_connector.infrastructure.models import MicrosoftConnector
    from module.microsoft_outlook.infrastructure.models import Mailbox
    from module.organization.infrastructure.models import Organization
    try:
        with use_schema(schema):
            return {"organizations": Organization.objects.count(), "connectors": MicrosoftConnector.objects.count(),
                    "mailboxes": Mailbox.objects.count(), "users": gum().objects.filter(is_active=True).count(),
                    "open_alerts": Alert.objects.filter(status="open").count(),
                    "critical_alerts": Alert.objects.filter(status="open", severity="critical").count()}
    except Exception as e:  # noqa: BLE001 : schéma incomplet (migration en cours, etc.)
        return {"error": f"{type(e).__name__}"}


def msps() -> list[dict]:
    with use_schema(PUBLIC):
        rows = list(Msp.objects.exclude(schema_name=PUBLIC).order_by("name").prefetch_related("domains"))
        out = [_row(m) for m in rows]
    for m in out:
        m["stats"] = _stats(m["schema"])
        m["usage"] = {"tenants": m["stats"].get("connectors", 0), "organizations": m["stats"].get("organizations", 0),
                      "users": m["stats"].get("users", 0)}
        m["alerts"] = _subscription_alerts(m)
    return out


def _row(m: Msp) -> dict:
    from module.platform.limits import PLANS, is_expired
    return {"id": m.pk, "name": m.name, "slug": m.slug, "schema": m.schema_name, "active": m.is_active,
            "created_at": m.created_at, "app_url": m.app_url,
            "domains": [{"id": d.pk, "domain": d.domain, "primary": d.is_primary} for d in m.domains.all()],
            "plan": m.plan, "plan_label": PLANS.get(m.plan, {}).get("label", m.plan),
            "limits": {"tenants": m.max_tenants, "organizations": m.max_organizations, "users": m.max_users},
            "expires_at": m.expires_at, "expired": is_expired(m.expires_at),
            "contact_name": m.contact_name, "contact_email": m.contact_email, "notes": m.notes}


def _subscription_alerts(m: dict) -> list[dict]:
    """Points d'attention : quota atteint ou presque, abonnement échu ou bientôt échu."""
    from datetime import date
    out = []
    labels = {"tenants": "tenants", "organizations": "organisations", "users": "comptes"}
    for k, limit in m["limits"].items():
        if limit is None:
            continue
        used = m["usage"].get(k, 0)
        if used >= limit:
            out.append({"level": "err", "text": f"Limite atteinte : {used}/{limit} {labels[k]}"})
        elif limit and used / limit >= 0.8:
            out.append({"level": "warn", "text": f"{used}/{limit} {labels[k]} (80 % ou plus)"})
    if m["expires_at"]:
        days = (m["expires_at"] - date.today()).days
        if days < 0:
            out.append({"level": "err", "text": f"Abonnement échu depuis {-days} j : espace inaccessible"})
        elif days <= 30:
            out.append({"level": "warn", "text": f"Abonnement échu dans {days} j"})
    return out


def _msp(msp_id) -> Msp:
    with use_schema(PUBLIC):
        m = Msp.objects.exclude(schema_name=PUBLIC).filter(pk=msp_id).first()
    if m is None:
        raise PlatformError("MSP introuvable.")
    return m


def update_msp(msp_id, *, actor: str, name: Optional[str] = None, active: Optional[bool] = None,
               subscription: Optional[dict] = None) -> dict:
    m = _msp(msp_id)
    if subscription:
        _apply_subscription(m, subscription, actor)
    with use_schema(PUBLIC):
        changes = []
        if name is not None and name.strip() and name.strip() != m.name:
            changes.append(f"nom : {m.name} → {name.strip()}")
            m.name = name.strip()
        if active is not None and bool(active) != m.is_active:
            m.is_active = bool(active)
            changes.append("réactivé" if active else "suspendu (plus aucun accès à son espace)")
        Msp.objects.filter(pk=m.pk).update(name=m.name, is_active=m.is_active)
    clear_domain_cache()
    if changes:
        audit(actor, "msp_update", m.slug, " · ".join(changes))
    return next(x for x in msps() if x["id"] == m.pk)


def add_domain(msp_id, domain: str, actor: str) -> dict:
    m = _msp(msp_id)
    domain = (domain or "").strip().lower()
    if not DOMAIN.match(domain):
        raise PlatformError("Adresse invalide (ex. client.overview.ca).")
    with use_schema(PUBLIC):
        if Domain.objects.filter(domain=domain).exists():
            raise PlatformError("Cette adresse est déjà utilisée.")
        Domain.objects.create(domain=domain, tenant=m, is_primary=not m.domains.exists())
    clear_domain_cache()
    audit(actor, "domain_add", m.slug, domain)
    return next(x for x in msps() if x["id"] == m.pk)


def remove_domain(msp_id, domain_id, actor: str) -> dict:
    m = _msp(msp_id)
    with use_schema(PUBLIC):
        d = Domain.objects.filter(pk=domain_id, tenant=m).first()
        if d is None:
            raise PlatformError("Adresse introuvable.")
        if d.is_primary:
            raise PlatformError("L'adresse principale ne peut pas être retirée.")
        d.delete()
    clear_domain_cache()
    audit(actor, "domain_remove", m.slug, d.domain)
    return next(x for x in msps() if x["id"] == m.pk)


def rescue_admin(msp_id, *, username: str, password: str, actor: str) -> dict:
    """Crée un administrateur dans l'espace d'un MSP (support : le MSP a perdu l'accès)."""
    m = _msp(msp_id)
    try:
        validate_password(password)
    except ValidationError as e:
        raise PlatformError(" ".join(e.messages)) from e
    with use_schema(m.schema_name):
        from module.accounts.infrastructure.models import OperatorProfile
        User = get_user_model()
        if User.objects.filter(username=username).exists():
            raise PlatformError("Ce nom d'utilisateur existe déjà dans cet espace.")
        u = User.objects.create_superuser(username=username, password=password)
        OperatorProfile.objects.get_or_create(user=u, defaults={"role": "admin"})
    audit(actor, "rescue_admin", m.slug, f"administrateur créé : {username}")
    return {"username": username, "msp": m.slug}


def journal(limit: int = 100) -> list[dict]:
    with use_schema(PUBLIC):
        return list(PlatformAudit.objects.values("at", "actor", "action", "target", "detail")[:limit])



# ---------------------------------------------------------------- abonnement (forfait, limites, échéance, contact)
def _limit(value) -> Optional[int]:
    if value in (None, "", "illimite"):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise PlatformError("Limite invalide : nombre entier positif, ou vide pour illimité.")
    if n < 0 or n > 100000:
        raise PlatformError("Limite invalide : entre 0 et 100000.")
    return n


def _apply_subscription(m: Msp, data: dict, actor: str) -> None:
    from datetime import date

    from module.platform.limits import PLANS
    before = {"plan": m.plan, "tenants": m.max_tenants, "organizations": m.max_organizations, "users": m.max_users,
              "expires_at": m.expires_at}
    plan = str(data.get("plan", m.plan))
    if plan not in PLANS:
        raise PlatformError("Forfait inconnu.")
    m.plan = plan
    preset = PLANS[plan]
    for kind, field in (("tenants", "max_tenants"), ("organizations", "max_organizations"), ("users", "max_users")):
        if plan != "personnalise" and field in preset:
            setattr(m, field, preset[field])
        elif kind in (data.get("limits") or {}):
            setattr(m, field, _limit(data["limits"][kind]))
    if "expires_at" in data:
        raw = data["expires_at"]
        try:
            m.expires_at = date.fromisoformat(raw) if raw else None
        except ValueError:
            raise PlatformError("Date d'échéance invalide (AAAA-MM-JJ).")
    for f in ("contact_name", "contact_email", "notes"):
        if f in data:
            setattr(m, f, str(data[f] or "")[:2000 if f == "notes" else 254])
    with use_schema(PUBLIC):
        m.save(update_fields=["plan", "max_tenants", "max_organizations", "max_users", "expires_at",
                              "contact_name", "contact_email", "notes"])
    clear_domain_cache()
    after = {"plan": m.plan, "tenants": m.max_tenants, "organizations": m.max_organizations, "users": m.max_users,
             "expires_at": m.expires_at}
    diff = [f"{k} : {before[k] if before[k] is not None else 'illimité'} → {after[k] if after[k] is not None else 'illimité'}"
            for k in after if before[k] != after[k]]
    if diff:
        audit(actor, "subscription", m.slug, " · ".join(diff))


# ---------------------------------------------------------------- fiche détaillée d'un MSP
def msp_detail(msp_id) -> dict:
    m = _msp(msp_id)
    row = next(x for x in msps() if x["id"] == m.pk)
    from django.contrib.auth import get_user_model as gum
    from module.microsoft_connector.infrastructure.models import MicrosoftConnector
    from module.organization.infrastructure.models import Organization
    try:
        with use_schema(m.schema_name):
            from module.accounts.application.service import role_of
            from module.accounts.infrastructure.models import OperatorProfile
            users = []
            for u in gum().objects.order_by("username"):
                p = OperatorProfile.objects.filter(user=u).first()
                users.append({"id": u.pk, "username": u.get_username(), "email": u.email, "active": u.is_active,
                              "role": role_of(u), "mfa": bool(p and p.totp_enabled), "last_login": u.last_login})
            conns = {}
            for c in MicrosoftConnector.objects.values("org_id", "name", "is_active", "last_error"):
                conns.setdefault(c["org_id"], []).append(c)
            orgs = [{"id": o.id, "name": o.name, "tenants": [{"name": c["name"], "active": c["is_active"], "error": bool(c["last_error"])}
                                                             for c in conns.get(o.id, [])]}
                    for o in Organization.objects.order_by("name")]
    except Exception as e:  # noqa: BLE001
        users, orgs = [], []
        row["detail_error"] = f"{type(e).__name__}"
    with use_schema(PUBLIC):
        history = list(PlatformAudit.objects.filter(target=m.slug).values("at", "actor", "action", "detail")[:30])
    return {**row, "users": users, "organizations": orgs, "history": history}


def reset_msp_user_mfa(msp_id, user_id: int, actor: str) -> dict:
    """Support : réinitialise le MFA d'un compte d'un MSP (téléphone perdu). Le compte devra le réactiver."""
    m = _msp(msp_id)
    with use_schema(m.schema_name):
        from module.accounts.infrastructure.models import OperatorProfile
        u = get_user_model().objects.filter(pk=user_id).first()
        if u is None:
            raise PlatformError("Compte introuvable.")
        OperatorProfile.objects.filter(user=u).update(totp_enabled=False, totp_secret=None, totp_last_counter=None)
    audit(actor, "msp_user_mfa_reset", m.slug, u.get_username())
    return {"username": u.get_username(), "mfa": False}


# ---------------------------------------------------------------- super-administrateurs : désactivation, MFA
def set_admin_active(user_id: int, active: bool, actor_id: int, actor: str) -> dict:
    with use_schema(PUBLIC):
        pa = PlatformAdmin.objects.select_related("user").filter(user_id=user_id).first()
        if pa is None:
            raise PlatformError("Super-administrateur introuvable.")
        if not active:
            if user_id == actor_id:
                raise PlatformError("Tu ne peux pas désactiver ton propre compte.")
            if PlatformAdmin.objects.filter(user__is_active=True).exclude(user_id=user_id).count() == 0:
                raise PlatformError("Il doit rester au moins un super-administrateur actif.")
        pa.user.is_active = bool(active)
        pa.user.save(update_fields=["is_active"])
    audit(actor, "admin_active" if active else "admin_disable", pa.user.get_username())
    return {"id": user_id, "active": bool(active)}


def reset_admin_mfa(user_id: int, actor: str) -> dict:
    """Nouvelle clé TOTP pour un super-administrateur (appareil perdu) : renvoyée une seule fois."""
    with use_schema(PUBLIC):
        pa = PlatformAdmin.objects.select_related("user").filter(user_id=user_id).first()
        if pa is None:
            raise PlatformError("Super-administrateur introuvable.")
        pa.totp_secret, pa.totp_last_counter, pa.failed, pa.locked_until = generate_secret(), None, 0, None
        pa.save(update_fields=["totp_secret", "totp_last_counter", "failed", "locked_until"])
    audit(actor, "admin_mfa_reset", pa.user.get_username())
    name = pa.user.get_username()
    return {"username": name, "secret": pa.totp_secret, "otpauth": otpauth_uri("OverView Plateforme", name, pa.totp_secret)}


def plans() -> dict:
    from module.platform.limits import PLANS
    return PLANS
