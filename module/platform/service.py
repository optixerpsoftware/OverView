"""Plateforme : création d'un MSP (schéma + domaine + premier administrateur) et reprise des données existantes."""
from __future__ import annotations

import re
from typing import Optional

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import connection, transaction

from module.platform.models import Domain, Msp
from shared_kernel.tenancy import PUBLIC, clear_domain_cache, use_schema

SLUG = re.compile(r"^[a-z][a-z0-9-]{1,30}[a-z0-9]$")
RESERVED = {"public", "www", "api", "admin", "platform", "app", "mail", "static", "test"}


class SignupError(Exception):
    pass


def ensure_public_tenant(domains: tuple[str, ...] = ()) -> Msp:
    """Le « tenant » public (la plateforme) doit exister pour django-tenants."""
    with use_schema(PUBLIC):
        public, _ = Msp.objects.get_or_create(schema_name=PUBLIC, defaults={"name": "Plateforme OverView", "slug": "platform"})
        for d in domains:
            Domain.objects.get_or_create(domain=d.lower(), defaults={"tenant": public, "is_primary": not public.domains.exists()})
        clear_domain_cache()
        return public


def create_msp(*, name: str, slug: str, admin_username: str, admin_password: str, admin_email: str = "",
               extra_domains: tuple[str, ...] = (), app_url: str = "") -> dict:
    """Crée le schéma du MSP (migrations comprises : quelques secondes), son domaine et son premier administrateur."""
    slug = (slug or "").strip().lower()
    name = (name or "").strip()
    if not name:
        raise SignupError("Nom du MSP requis.")
    if not SLUG.match(slug) or slug in RESERVED:
        raise SignupError("Identifiant invalide : 3 à 32 caractères, lettres minuscules, chiffres et tirets.")
    try:
        validate_password(admin_password)
    except ValidationError as e:
        raise SignupError(" ".join(e.messages)) from e
    if not (admin_username or "").strip():
        raise SignupError("Nom d'utilisateur administrateur requis.")
    schema = slug.replace("-", "_")
    host = f"{slug}.{settings.TENANT_BASE_DOMAIN}"
    with use_schema(PUBLIC):
        if Msp.objects.filter(slug=slug).exists() or Msp.objects.filter(schema_name=schema).exists():
            raise SignupError("Cet identifiant est déjà utilisé.")
        if Domain.objects.filter(domain__in=[host, *extra_domains]).exists():
            raise SignupError("Cette adresse est déjà utilisée.")
        msp = Msp(schema_name=schema, name=name, slug=slug, app_url=app_url)
        msp.save()                                   # crée et migre le schéma
        Domain.objects.create(domain=host, tenant=msp, is_primary=True)
        for d in extra_domains:
            Domain.objects.create(domain=d.lower(), tenant=msp, is_primary=False)
    clear_domain_cache()
    with use_schema(schema):
        from django.contrib.auth import get_user_model
        from module.accounts.infrastructure.models import OperatorProfile
        with transaction.atomic():
            u = get_user_model().objects.create_superuser(username=admin_username.strip(), email=admin_email or "",
                                                          password=admin_password)
            OperatorProfile.objects.get_or_create(user=u, defaults={"role": "admin"})
    return {"schema": schema, "slug": slug, "domain": host, "name": name}


def adopt_public_data(schema: str, *, dry_run: bool = False) -> dict:
    """Reprise : copie les données des applications « tenant » restées dans le schéma public (installation antérieure
    à django-tenants) vers le schéma du MSP, puis réaligne les séquences. Les tables publiques ne sont PAS supprimées.
    Refuse si le schéma cible contient déjà des données métier (pas de mélange)."""
    from django.apps import apps
    # Applications métier + comptes (utilisateurs, groupes). Pas les permissions / types de contenu / journal d'admin :
    # la migration du schéma les crée déjà, avec ses propres identifiants.
    tenant_labels = {a.split(".")[-1] for a in settings.TENANT_APPS} - {"contenttypes", "admin", "sessions", "auth"}
    copied, skipped = {}, []
    with connection.cursor() as c:
        c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        public_tables = {r[0] for r in c.fetchall()}
        c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s", [schema])
        tenant_tables = {r[0] for r in c.fetchall()}
        models = [m for m in apps.get_models(include_auto_created=True)
                  if m._meta.app_label in tenant_labels or m._meta.db_table in ("auth_user", "auth_group", "auth_user_groups")]
        tables = sorted({m._meta.db_table for m in models})
        for t in tables:
            if t not in public_tables or t not in tenant_tables:
                skipped.append(t)
                continue
            c.execute(f'SELECT count(*) FROM "{schema}"."{t}"')
            if c.fetchone()[0]:
                raise SignupError(f"Le schéma {schema} contient déjà des données ({t}) : reprise annulée.")
        if dry_run:
            return {"would_copy": [t for t in tables if t not in skipped], "skipped": skipped}
        with transaction.atomic():
            # Ordre : tables sans dépendances d'abord (utilisateurs avant profils…) — contraintes différées le temps de la copie.
            c.execute("SET CONSTRAINTS ALL DEFERRED")
            for t in [t for t in tables if t not in skipped]:
                c.execute(f"""SELECT column_name FROM information_schema.columns
                              WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position""", [schema, t])
                cols = [r[0] for r in c.fetchall()]
                c.execute(f"""SELECT column_name FROM information_schema.columns
                              WHERE table_schema = 'public' AND table_name = %s""", [t])
                public_cols = {r[0] for r in c.fetchall()}
                common = [col for col in cols if col in public_cols]
                collist = ", ".join(f'"{x}"' for x in common)
                c.execute(f'INSERT INTO "{schema}"."{t}" ({collist}) SELECT {collist} FROM "public"."{t}"')
                copied[t] = c.rowcount
                # Séquences (identifiants entiers auto-incrémentés, ex. utilisateurs).
                c.execute("""SELECT a.attname, pg_get_serial_sequence(%s, a.attname) FROM pg_attribute a
                             WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped""",
                          [f'"{schema}"."{t}"', f'"{schema}"."{t}"'])
                for col, seq in c.fetchall():
                    if seq:
                        c.execute(f'SELECT setval(%s, COALESCE((SELECT MAX("{col}") FROM "{schema}"."{t}"), 1))', [seq])
    return {"copied": copied, "skipped": skipped}


def platform_info() -> dict:
    return {"signup_open": bool(getattr(settings, "PLATFORM_SIGNUP_OPEN", False)), "base_domain": settings.TENANT_BASE_DOMAIN}


__all__ = ["create_msp", "adopt_public_data", "ensure_public_tenant", "platform_info", "SignupError", "Optional"]
