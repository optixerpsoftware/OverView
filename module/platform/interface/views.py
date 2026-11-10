"""Vues publiques de la plateforme : informations et inscription d'un nouveau MSP."""
from shared_kernel.aio import db
from shared_kernel.async_api import AsyncAPIView, Response, no_store, status

from module.platform.service import SignupError, create_msp, platform_info


class PlatformInfoView(AsyncAPIView):
    authentication_required = False

    async def get(self, request):
        return Response(await db(platform_info)())



# ---------------------------------------------------------------- console de la plateforme
from module.platform import console  # noqa: E402


class _Console(AsyncAPIView):
    def handle_exception(self, exc):
        if isinstance(exc, console.PlatformAuthFailed):
            return Response({"detail": str(exc)}, status.HTTP_401_UNAUTHORIZED)
        if isinstance(exc, (console.PlatformError, SignupError)):
            return Response({"detail": str(exc)}, status.HTTP_400_BAD_REQUEST)
        return super().handle_exception(exc)


def _b(request) -> dict:
    return request.data if isinstance(request.data, dict) else {}


class ConsoleLoginView(_Console):
    authentication_required = False

    async def post(self, request):
        b = _b(request)
        return no_store(Response(await db(console.login)(str(b.get("username", "")), str(b.get("password", "")))))


class ConsoleVerifyView(_Console):
    authentication_required = False

    async def post(self, request):
        b = _b(request)
        return no_store(Response(await db(console.verify)(str(b.get("mfa_token", "")), str(b.get("code", "")))))


class ConsoleMeView(_Console):
    async def get(self, request):
        return Response({"username": request.user.get_username(), "platform_admin": True})


class ConsoleOverviewView(_Console):
    """GET : MSP (avec chiffres), super-administrateurs, réglages, journal."""

    async def get(self, request):
        return Response({"msps": await db(console.msps)(), "admins": await db(console.admins)(),
                         "journal": await db(console.journal)(), "plans": console.plans()})


class ConsoleMspsView(_Console):
    """POST {name, slug, admin_username, admin_password, admin_email?} : crée un MSP (seul moyen, avec create_msp)."""

    async def post(self, request):
        b, me = _b(request), request.user.get_username()
        r = await db(create_msp)(name=str(b.get("name", "")), slug=str(b.get("slug", "")),
                                 admin_username=str(b.get("admin_username", "")), admin_password=str(b.get("admin_password", "")),
                                 admin_email=str(b.get("admin_email", "")))
        await db(console.audit)(me, "msp_create", r["slug"], r["domain"])
        if isinstance(b.get("subscription"), dict):      # forfait, limites, échéance et contact choisis à la création
            from module.platform.models import Msp
            msp_id = await db(lambda: Msp.objects.filter(slug=r["slug"]).values_list("pk", flat=True).first())()
            await db(console.update_msp)(msp_id, actor=me, subscription=b["subscription"])
        return no_store(Response(r, status.HTTP_201_CREATED))


class ConsoleMspView(_Console):
    """PATCH {name?, active?} : renommer, suspendre ou réactiver un MSP."""

    async def get(self, request, msp_id):
        return Response(await db(console.msp_detail)(msp_id))

    async def patch(self, request, msp_id):
        b = _b(request)
        return Response(await db(console.update_msp)(msp_id, actor=request.user.get_username(), name=b.get("name"),
                                                     active=b.get("active"), subscription=b.get("subscription")))


class ConsoleDomainsView(_Console):
    async def post(self, request, msp_id):
        return Response(await db(console.add_domain)(msp_id, str(_b(request).get("domain", "")), request.user.get_username()))

    async def delete(self, request, msp_id):
        return Response(await db(console.remove_domain)(msp_id, _b(request).get("domain_id"), request.user.get_username()))


class ConsoleRescueAdminView(_Console):
    """POST {username, password} : crée un administrateur dans l'espace du MSP (support)."""

    async def post(self, request, msp_id):
        b = _b(request)
        return no_store(Response(await db(console.rescue_admin)(msp_id, username=str(b.get("username", "")),
                                                                password=str(b.get("password", "")),
                                                                actor=request.user.get_username()), status.HTTP_201_CREATED))


class ConsoleAdminsView(_Console):
    """POST {username, password, email?} : nouveau super-administrateur (clé TOTP renvoyée une seule fois)."""

    async def post(self, request):
        b = _b(request)
        r = await db(console.create_platform_admin)(str(b.get("username", "")), str(b.get("password", "")), str(b.get("email", "")))
        await db(console.audit)(request.user.get_username(), "admin_create", r["username"])
        return no_store(Response(r, status.HTTP_201_CREATED))



class ConsoleMspUserMfaView(_Console):
    """POST : réinitialise le MFA d'un compte d'un MSP (support)."""

    async def post(self, request, msp_id, user_id):
        return Response(await db(console.reset_msp_user_mfa)(msp_id, user_id, request.user.get_username()))


class ConsoleAdminView(_Console):
    """PATCH {active} : désactiver / réactiver un super-administrateur."""

    async def patch(self, request, user_id):
        return Response(await db(console.set_admin_active)(user_id, bool(_b(request).get("active")), request.user.pk,
                                                           request.user.get_username()))


class ConsoleAdminMfaView(_Console):
    """POST : nouvelle clé MFA d'un super-administrateur (renvoyée une seule fois)."""

    async def post(self, request, user_id):
        return no_store(Response(await db(console.reset_admin_mfa)(user_id, request.user.get_username())))


class SubscriptionView(AsyncAPIView):
    """GET : abonnement du MSP connecté (forfait, quotas, consommation, échéance). Lecture seule, rôle Lecture et plus."""

    async def get(self, request):
        from module.platform.limits import subscription
        data = await db(subscription)()
        if data is None:
            return Response({"detail": "Aucun abonnement pour cet espace."}, status.HTTP_404_NOT_FOUND)
        return Response(data)
