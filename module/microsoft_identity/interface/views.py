"""Vues HTTP du module identity : async natives (Daphne), authentification JWT, mêmes URL et corps JSON qu'avant."""
from module.microsoft_identity.application.security_sync import SecuritySyncService
from module.microsoft_identity.application.services import (
    ConfirmationMismatch, ConnectorUnavailable, DeviceNotOwned, IdentityService, MailboxNotFound, MethodNotFound,
)
from shared_kernel.aio import db
from shared_kernel.async_api import AsyncAPIView, Response, graph_error_response, no_store, plain, status


def _identity() -> IdentityService:
    """Service d'actions ; après chaque action réussie il planifie une synchro (instantané local rafraîchi)."""
    return IdentityService(sync_trigger=SecuritySyncService().request_sync)


# Erreurs « attendues » : traduites par handle_exception ; toute autre erreur est une erreur Graph (502).
_EXPECTED = (MailboxNotFound, ConnectorUnavailable, DeviceNotOwned, MethodNotFound, ConfirmationMismatch, ValueError)


class _IdentityView(AsyncAPIView):
    def handle_exception(self, exc):
        if isinstance(exc, MailboxNotFound):
            return Response({'detail': 'Boîte introuvable.'}, status.HTTP_404_NOT_FOUND)
        if isinstance(exc, ConnectorUnavailable):
            return Response({'detail': 'Connecteur indisponible.'}, status.HTTP_409_CONFLICT)
        if isinstance(exc, DeviceNotOwned):
            return Response({'detail': "Cet appareil n'est pas enregistré pour ce compte."}, status.HTTP_404_NOT_FOUND)
        if isinstance(exc, MethodNotFound):
            return Response({'detail': "Cette méthode n'existe pas (ou plus) pour ce compte."}, status.HTTP_404_NOT_FOUND)
        if isinstance(exc, ConfirmationMismatch):
            return Response({'detail': "Confirmation invalide : saisis l'adresse exacte de la boîte."},
                            status.HTTP_400_BAD_REQUEST)
        from module.microsoft_identity.application.quarantine_service import QuarantineRefused
        if isinstance(exc, QuarantineRefused):
            return Response({'detail': str(exc)}, status.HTTP_409_CONFLICT)
        if isinstance(exc, ValueError):
            return Response({'detail': str(exc)}, status.HTTP_400_BAD_REQUEST)
        if isinstance(exc, AsyncAPIView._internal_errors()):   # authentification, JSON invalide…
            return super().handle_exception(exc)
        return graph_error_response(exc)


class SecurityOverviewView(_IdentityView):
    """GET : profil, appareils, méthodes MFA et journal d'actions, lus dans l'instantané local (instantané).
    `sync` indique l'état de la synchronisation en arrière-plan ; à la première ouverture elle est lancée
    et `profile` vaut null jusqu'à sa fin."""

    async def get(self, request, mailbox_id):
        data = await db(SecuritySyncService().overview)(mailbox_id)      # lecture locale (ORM) : thread dédié
        return no_store(Response(plain(data)))


class SecurityActivityView(_IdentityView):
    """GET : connexions (stockées localement, collectées de façon incrémentale) et sessions actives estimées."""

    async def get(self, request, mailbox_id):
        data = await db(SecuritySyncService().activity)(mailbox_id)
        return no_store(Response(plain(data)))


class SecuritySyncView(_IdentityView):
    """POST : demande une synchronisation immédiate (asynchrone, 202). Si une synchro tourne déjà, elle est conservée."""

    async def post(self, request, mailbox_id):
        started = await db(SecuritySyncService().request_sync)(mailbox_id)   # verrou en base + mise en file (Redis)
        return Response({'started': started}, status.HTTP_202_ACCEPTED)


class RevokeSessionsView(_IdentityView):
    """POST : déconnecte l'utilisateur de toutes ses sessions."""

    async def post(self, request, mailbox_id):
        await _identity().revoke_sessions_async(mailbox_id, by=request.user.get_username())
        return Response({'ok': True})


class DeleteDeviceView(_IdentityView):
    """DELETE {"confirm": "<nom exact de l'appareil>"} : supprime l'appareil de l'annuaire Entra ID."""

    async def delete(self, request, mailbox_id, device_id):
        await _identity().delete_device_async(
            mailbox_id, device_id, by=request.user.get_username(), confirm=request.data.get('confirm'))
        return Response(status=status.HTTP_204_NO_CONTENT)


class DeleteAuthMethodView(_IdentityView):
    """DELETE {"confirm": "<adresse de la boîte>"} : supprime une méthode d'authentification."""

    async def delete(self, request, mailbox_id, method_id):
        await _identity().delete_auth_method_async(
            mailbox_id, method_id, by=request.user.get_username(), confirm=request.data.get('confirm'))
        return Response(status=status.HTTP_204_NO_CONTENT)


class AddAuthMethodView(_IdentityView):
    """POST {"kind": "phone|email|tap|totp", "value", "phone_type", "lifetime_minutes", "once", "confirm"}.

    Pour `tap` et `totp`, la réponse contient le secret (affiché une seule fois)."""

    async def post(self, request, mailbox_id):
        d = request.data
        result = await _identity().add_auth_method_async(
            mailbox_id, by=request.user.get_username(), confirm=d.get('confirm'), kind=d.get('kind'),
            value=d.get('value'), phone_type=d.get('phone_type', 'mobile'),
            lifetime_minutes=d.get('lifetime_minutes', 60), once=bool(d.get('once', True)))
        kind = d.get('kind')
        return no_store(Response({'ok': True, 'tap': result if kind == 'tap' else None,
                                  'totp': result if kind == 'totp' else None}, status.HTTP_201_CREATED))


class ResetPasswordView(_IdentityView):
    """POST {"confirm": "<adresse>", "force_change": true} : renvoie le nouveau mot de passe (une fois)."""

    async def post(self, request, mailbox_id):
        pw = await _identity().reset_password_async(
            mailbox_id, by=request.user.get_username(), confirm=request.data.get('confirm'),
            force_change=bool(request.data.get('force_change', True)))
        return no_store(Response({'password': pw}))


class EmergencyView(_IdentityView):
    """POST {"confirm": "<adresse>"} : pipeline d'urgence complet."""

    async def post(self, request, mailbox_id):
        raw = request.data.get('options') if isinstance(request.data, dict) else None
        options = {k: bool(v) for k, v in (raw or {}).items() if k in ('remove_forwarding', 'disable_account', 'create_tap')}
        svc = _identity()
        steps, pw = await svc.emergency_async(
            mailbox_id, by=request.user.get_username(), confirm=request.data.get('confirm'), options=options)
        return no_store(Response({'steps': plain(steps), 'password': pw, 'tap': getattr(svc, 'last_tap', None),
                                  'ok': all(s.ok for s in steps)}))


class QuarantineView(_IdentityView):
    """GET : état. POST {confirm, reason?} : met le compte en quarantaine. DELETE {confirm} : lève la quarantaine."""

    async def get(self, request, mailbox_id):
        from module.microsoft_identity.application.quarantine_service import AccountQuarantineService
        return Response({'quarantine': await db(AccountQuarantineService.status)(mailbox_id)})

    async def post(self, request, mailbox_id):
        from module.microsoft_identity.application.quarantine_service import AccountQuarantineService
        b = request.data if isinstance(request.data, dict) else {}
        return Response({'quarantine': await AccountQuarantineService().start_async(
            mailbox_id, confirm=b.get('confirm'), by=request.user.get_username(), reason=str(b.get('reason', '')))})

    async def delete(self, request, mailbox_id):
        from module.microsoft_identity.application.quarantine_service import AccountQuarantineService
        b = request.data if isinstance(request.data, dict) else {}
        return Response(await AccountQuarantineService().release_async(
            mailbox_id, confirm=b.get('confirm'), by=request.user.get_username()))
