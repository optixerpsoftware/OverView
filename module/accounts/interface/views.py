"""Vues : connexion en deux étapes, mon compte (MFA), gestion des comptes OverView."""
from rest_framework.permissions import BasePermission

from module.accounts.application.service import AccountError, AccountService, AuthFailed, check_access
from shared_kernel.aio import db
from shared_kernel.async_api import AsyncAPIView, Response, no_store, status


class _View(AsyncAPIView):
    def handle_exception(self, exc):
        if isinstance(exc, AuthFailed):
            return Response({"detail": str(exc)}, status.HTTP_401_UNAUTHORIZED)
        if isinstance(exc, (AccountError, ValueError, KeyError)):
            return Response({"detail": str(exc)}, status.HTTP_400_BAD_REQUEST)
        return super().handle_exception(exc)


def _body(request) -> dict:
    return request.data if isinstance(request.data, dict) else {}


class LoginView(_View):
    """POST {username, password} → jetons, ou {mfa_required, mfa_token} si le compte a le MFA activé."""
    authentication_required = False

    async def post(self, request):
        b = _body(request)
        return no_store(Response(await db(AccountService.login)(str(b.get("username", "")), str(b.get("password", "")))))


class MfaVerifyView(_View):
    """POST {mfa_token, code} → jetons (code TOTP à 6 chiffres ; verrouillage après 5 erreurs)."""
    authentication_required = False

    async def post(self, request):
        b = _body(request)
        return no_store(Response(await db(AccountService.verify)(str(b.get("mfa_token", "")), str(b.get("code", "")))))


class MeView(_View):
    async def get(self, request):
        return Response(await db(AccountService.me)(request.user))


class MfaSetupView(_View):
    """POST : nouvelle clé TOTP (à scanner). PUT {code} : active. DELETE {code} : désactive."""

    async def post(self, request):
        return no_store(Response(await db(AccountService.mfa_setup)(request.user)))

    async def put(self, request):
        return Response(await db(AccountService.mfa_enable)(request.user, str(_body(request).get("code", ""))))

    async def delete(self, request):
        return Response(await db(AccountService.mfa_disable)(request.user, str(_body(request).get("code", ""))))


class UsersView(_View):
    """GET : comptes OverView. POST {username, password, role, email?} : crée un compte (administrateurs)."""

    async def get(self, request):
        return Response(await db(AccountService.users)())

    async def post(self, request):
        b = _body(request)
        return Response(await db(AccountService.create_user)(
            str(b.get("username", "")), str(b.get("password", "")), str(b.get("role", "viewer")), str(b.get("email", ""))),
            status.HTTP_201_CREATED)


class UserDetailView(_View):
    """PATCH {role?, active?, reset_mfa?}."""

    async def patch(self, request, user_id):
        b = _body(request)
        return Response(await db(AccountService.update_user)(
            request.user, user_id, role=b.get("role"), active=b.get("active"), reset_mfa=bool(b.get("reset_mfa"))))


class RolePermission(BasePermission):
    """Même contrôle d'accès pour les vues DRF (CRUD) que pour les vues async."""
    message = "Droits insuffisants."

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        denied = check_access(request.user, request.method, request.path)
        if denied:
            self.message = denied[1]
            return False
        return True
