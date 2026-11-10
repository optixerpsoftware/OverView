"""Vues publiques de la plateforme : informations et inscription d'un nouveau MSP."""
from shared_kernel.aio import db
from shared_kernel.async_api import AsyncAPIView, Response, no_store, status

from module.platform.service import SignupError, create_msp, platform_info


class PlatformInfoView(AsyncAPIView):
    authentication_required = False

    async def get(self, request):
        return Response(platform_info())


class SignupView(AsyncAPIView):
    """POST {name, slug, admin_username, admin_password, admin_email?} : crée l'espace d'un MSP.
    Fermé par défaut (PLATFORM_SIGNUP_OPEN=false) : les MSP sont alors créés par la commande `create_msp`."""
    authentication_required = False

    def handle_exception(self, exc):
        if isinstance(exc, SignupError):
            return Response({"detail": str(exc)}, status.HTTP_400_BAD_REQUEST)
        return super().handle_exception(exc)

    async def post(self, request):
        if not platform_info()["signup_open"]:
            return Response({"detail": "Les inscriptions sont fermées. Contacte l'éditeur d'OverView."}, status.HTTP_403_FORBIDDEN)
        b = request.data if isinstance(request.data, dict) else {}
        data = await db(create_msp)(name=str(b.get("name", "")), slug=str(b.get("slug", "")),
                                    admin_username=str(b.get("admin_username", "")),
                                    admin_password=str(b.get("admin_password", "")), admin_email=str(b.get("admin_email", "")))
        return no_store(Response(data, status.HTTP_201_CREATED))
