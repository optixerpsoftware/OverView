from __future__ import annotations

from datetime import timezone
from urllib.parse import quote

from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.audit_logs.sign_ins.sign_ins_request_builder import SignInsRequestBuilder
from msgraph.generated.models.password_profile import PasswordProfile
from msgraph.generated.models.user import User
from msgraph.generated.users.item.user_item_request_builder import UserItemRequestBuilder

from module.microsoft_identity.domain.entities import (
    AuthMethodInfo, DeviceInfo, SignInInfo, UserSecurityProfile,
)
from module.microsoft_identity.domain.ports import IdentityGateway
from module.microsoft_identity.domain.totp import totp_code
from shared_kernel.graph_rest import BASE, BETA, GraphRest, safe_id

# type Graph -> (kind, libellé, est un facteur MFA)
_METHODS = {
    "microsoftAuthenticatorAuthenticationMethod": ("authenticator", "Microsoft Authenticator", True),
    "phoneAuthenticationMethod": ("phone", "Téléphone (SMS / appel)", True),
    "emailAuthenticationMethod": ("email", "Courriel de récupération", True),
    "fido2AuthenticationMethod": ("fido2", "Clé de sécurité FIDO2", True),
    "windowsHelloForBusinessAuthenticationMethod": ("hello", "Windows Hello Entreprise", True),
    "softwareOathAuthenticationMethod": ("oath", "Application TOTP", True),
    "hardwareOathAuthenticationMethod": ("hardware_oath", "Application TOTP (jeton OATH)", True),
    "temporaryAccessPassAuthenticationMethod": ("tap", "Passe d'accès temporaire", True),
    "passwordAuthenticationMethod": ("password", "Mot de passe", False),
    "platformCredentialAuthenticationMethod": ("platform", "Identifiant de plateforme", True),
}


# type de méthode -> segment d'URL Graph pour la suppression
_DELETE_SEGMENTS = {
    "authenticator": "microsoftAuthenticatorMethods", "phone": "phoneMethods", "email": "emailMethods",
    "fido2": "fido2Methods", "hello": "windowsHelloForBusinessMethods", "oath": "softwareOathMethods",
    "tap": "temporaryAccessPassMethods", "platform": "platformCredentialMethods",
    "hardware_oath": "hardwareOathMethods",   # API bêta
}
_WRITE = "UserAuthenticationMethod.ReadWrite.All"


class GraphIdentityGateway(IdentityGateway):
    def __init__(self, graph):
        self.graph = graph  # shared_kernel.api.Graph (mode Application)
        self.rest = GraphRest(graph)

    @property
    def _users(self):
        return self.graph.client.users

    async def get_profile(self, upn):
        params = UserItemRequestBuilder.UserItemRequestBuilderGetQueryParameters(
            select=["id", "displayName", "accountEnabled",
                    "signInSessionsValidFromDateTime", "lastPasswordChangeDateTime"])
        u = await self._users.by_user_id(upn).get(
            request_configuration=RequestConfiguration(query_parameters=params))
        return UserSecurityProfile(
            user_id=u.id or "", display_name=u.display_name or upn,
            account_enabled=u.account_enabled,
            sessions_valid_from=u.sign_in_sessions_valid_from_date_time,
            last_password_change=u.last_password_change_date_time,
        )

    async def list_devices(self, upn):
        res = await self._users.by_user_id(upn).registered_devices.get()
        out = []
        for d in (res.value if res else None) or []:
            out.append(DeviceInfo(
                id=d.id or "",
                device_id=getattr(d, "device_id", None) or "",
                display_name=getattr(d, "display_name", None) or "(sans nom)",
                operating_system=getattr(d, "operating_system", None) or "",
                operating_system_version=getattr(d, "operating_system_version", None) or "",
                trust_type=getattr(d, "trust_type", None) or "",
                is_compliant=getattr(d, "is_compliant", None),
                is_managed=getattr(d, "is_managed", None),
                last_sign_in=getattr(d, "approximate_last_sign_in_date_time", None),
                registered_at=getattr(d, "registration_date_time", None),
            ))
        return out

    async def list_auth_methods(self, upn):
        res = await self._users.by_user_id(upn).authentication.methods.get()
        out = []
        for m in (res.value if res else None) or []:
            t = (getattr(m, "odata_type", None) or "").rsplit(".", 1)[-1]
            kind, label, mfa = _METHODS.get(t, ("other", t or "Autre méthode", True))
            detail = (getattr(m, "display_name", None) or getattr(m, "device_tag", None)
                      or getattr(m, "phone_number", None) or getattr(m, "email_address", None) or "")
            out.append(AuthMethodInfo(id=m.id or "", kind=kind, label=label,
                                      detail=str(detail), is_mfa=mfa))
        return out

    async def list_sign_ins(self, user_id, top=100, since=None):
        # Filtre par userId : ~3x plus rapide que par userPrincipalName (mesuré : 8 s contre 29 s).
        flt = f"userId eq '{user_id}'"
        if since is not None:  # synchro incrémentale : seulement ce qui est nouveau depuis le curseur
            flt += f" and createdDateTime ge {since.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
        params = SignInsRequestBuilder.SignInsRequestBuilderGetQueryParameters(filter=flt, top=top)
        builder = self.graph.client.audit_logs.sign_ins
        res = await builder.get(request_configuration=RequestConfiguration(query_parameters=params))
        out = []
        for _ in range(10):  # pagination (10 pages max) : la première synchro peut dépasser 100 connexions
            for s in (res.value if res else None) or []:
                loc, dev, st = s.location, s.device_detail, s.status
                code = (st.error_code if st and st.error_code is not None else 0)
                out.append(SignInInfo(
                    at=s.created_date_time, app=s.app_display_name or "", ip=s.ip_address or "",
                    city=(loc.city if loc else "") or "", country=(loc.country_or_region if loc else "") or "",
                    client=s.client_app_used or "", os=(dev.operating_system if dev else "") or "",
                    browser=(dev.browser if dev else "") or "",
                    interactive=bool(s.is_interactive), success=code == 0, error_code=code, id=s.id or "",
                ))
            nxt = getattr(res, "odata_next_link", None) if res else None
            if not nxt:
                break
            res = await builder.with_url(nxt).get()
        out.sort(key=lambda x: x.at.timestamp() if x.at else 0, reverse=True)
        return out

    async def revoke_sessions(self, upn):
        await self._users.by_user_id(upn).revoke_sign_in_sessions.post()

    async def delete_auth_method(self, upn, kind, method_id):
        segment = _DELETE_SEGMENTS[kind]  # KeyError = type non supprimable (ex. mot de passe)
        await self.rest.delete(
            f"/users/{quote(upn, safe='@')}/authentication/{segment}/{safe_id(method_id)}", requires=_WRITE,
            base=BETA if kind == "hardware_oath" else BASE)

    async def add_phone_method(self, upn, phone_number, phone_type):
        await self.rest.post(f"/users/{quote(upn, safe='@')}/authentication/phoneMethods",
                             {"phoneNumber": phone_number, "phoneType": phone_type}, requires=_WRITE)

    async def add_email_method(self, upn, email):
        await self.rest.post(f"/users/{quote(upn, safe='@')}/authentication/emailMethods",
                             {"emailAddress": email}, requires=_WRITE)

    async def add_totp_method(self, upn, secret, serial, label):
        """Jeton OATH (API bêta) : création, puis assignation + activation avec un premier code TOTP
        calculé depuis la clé. Si l'assignation échoue, le jeton orphelin est supprimé."""
        profile = await self.get_profile(upn)
        base = "/directory/authenticationMethodDevices/hardwareOathDevices"
        need = "Policy.ReadWrite.AuthenticationMethod"
        dev = await self.rest.post(base, {
            "serialNumber": serial, "manufacturer": "OverView", "model": "TOTP", "secretKey": secret,
            "timeIntervalInSeconds": 30, "hashFunction": "hmacsha1",
        }, requires=need, base=BETA)
        try:
            # Action liée à la collection de méthodes OATH de l'utilisateur (définition $metadata bêta) :
            # le jeton est passé dans `device`, et le code prouve qu'on détient la clé.
            await self.rest.post(
                f"/users/{quote(upn, safe='@')}/authentication/hardwareOathMethods/assignAndActivate",
                {"device": {"id": dev["id"]}, "displayName": label, "verificationCode": totp_code(secret)},
                requires=_WRITE, base=BETA)
        except Exception:
            try:
                await self.rest.delete(f"{base}/{safe_id(dev['id'])}", requires=need, base=BETA)
            except Exception:
                pass  # nettoyage au mieux ; l'erreur d'origine est celle qui compte
            raise

    async def create_temporary_access_pass(self, upn, lifetime_minutes, once):
        res = await self.rest.post(
            f"/users/{quote(upn, safe='@')}/authentication/temporaryAccessPassMethods",
            {"lifetimeInMinutes": lifetime_minutes, "isUsableOnce": once}, requires=_WRITE)
        return {"pass": res.get("temporaryAccessPass", ""), "lifetime_minutes": res.get("lifetimeInMinutes", lifetime_minutes),
                "once": bool(res.get("isUsableOnce", once))}

    async def delete_device(self, device_object_id):
        # Supprime l'objet appareil dans Entra ID (Device.ReadWrite.All requis).
        await self.graph.client.devices.by_device_id(device_object_id).delete()

    async def internal_domains(self):
        rows = await self.rest.get_all("/domains", {"$select": "id,isVerified"}, requires="Domain.Read.All")
        return [d["id"].lower() for d in rows if d.get("isVerified")]

    async def list_message_rules(self, upn):
        return await self.rest.get_all(f"/users/{quote(upn, safe='@')}/mailFolders/inbox/messageRules",
                                       requires="MailboxSettings.Read")

    async def delete_message_rule(self, upn, rule_id):
        await self.rest.delete(f"/users/{quote(upn, safe='@')}/mailFolders/inbox/messageRules/{safe_id(rule_id)}",
                               requires="MailboxSettings.ReadWrite")

    async def clear_mailbox_forwarding(self, upn):
        """Retire le transfert SMTP de la boîte (Exchange Online). Renvoie True s'il y en avait un."""
        from module.microsoft_outlook.infrastructure.exchange_gateway import ExchangeGateway
        exo = ExchangeGateway(self.rest.graph, str(self.rest.graph.tenant_id))
        rows = await exo.invoke("Get-Mailbox", {"Identity": upn})
        had = bool(rows and (rows[0].get("ForwardingSmtpAddress") or rows[0].get("ForwardingAddress")))
        if had:
            await exo.invoke("Set-Mailbox", {"Identity": upn, "ForwardingSmtpAddress": None, "ForwardingAddress": None,
                                             "DeliverToMailboxAndForward": False})
        return had

    async def set_account_enabled(self, upn, enabled):
        await self.rest.patch(f"/users/{quote(upn, safe='@')}", {"accountEnabled": bool(enabled)}, requires="User.ReadWrite.All")

    async def set_password(self, upn, password, force_change):
        body = User(password_profile=PasswordProfile(
            password=password, force_change_password_next_sign_in=force_change))
        await self._users.by_user_id(upn).patch(body)
