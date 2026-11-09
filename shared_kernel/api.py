import os
from functools import cached_property
from pathlib import Path
from typing import Optional

from asgiref.sync import async_to_sync
from dotenv import load_dotenv
from azure.identity import ClientSecretCredential, UsernamePasswordCredential
from msgraph import GraphServiceClient

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

APP_SCOPES =["https://graph.microsoft.com/.default"]
DELEGATED_SCOPES = ["User.Read"]


class Graph:
    """Client Microsoft Graph.

    - Mode Application (client secret) : permissions "Application" (ex: User.Read.All).
      `/me` n'existe pas dans ce mode.
    - Mode Délégué (M365_USERNAME / M365_PASSWORD) : permet `/me`.
      Ne fonctionne pas avec MFA ; à remplacer par un flux OAuth interactif en production.

    La configuration est lue uniquement depuis l'environnement (.env) et le client
    est créé à la première utilisation (pas à l'import du module).
    """

    def __init__(self):
        self.client_id = os.environ.get("M365_APPLICATION_ID")
        self.tenant_id = os.environ.get("M365_TENANT_ID")
        self.client_secret = os.environ.get("M365_TOKEN")
        self.username = os.environ.get("M365_USERNAME")
        self.password = os.environ.get("M365_PASSWORD")

    @property
    def delegated(self) -> bool:
        return bool(self.username and self.password)

    @cached_property
    def client(self) -> GraphServiceClient:
        if not (self.client_id and self.tenant_id):
            raise ValueError("M365_APPLICATION_ID et M365_TENANT_ID sont requis")

        if self.delegated:
            credential = UsernamePasswordCredential(
                client_id=self.client_id,
                tenant_id=self.tenant_id,
                username=self.username,
                password=self.password,
            )
            scopes = DELEGATED_SCOPES
        else:
            if not self.client_secret:
                raise ValueError("M365_TOKEN (client secret) est requis en mode Application")
            credential = ClientSecretCredential(
                tenant_id=self.tenant_id,
                client_id=self.client_id,
                client_secret=self.client_secret,
            )
            scopes = APP_SCOPES

        self._credential = credential
        self._scopes = scopes
        return GraphServiceClient(credentials=credential, scopes=scopes)

    def access_token(self, scopes: Optional[list] = None) -> str:
        """Jeton d'accès (Graph par défaut ; `scopes=[".../.default"]` pour une autre ressource, ex. Exchange Online).
        azure-identity met le jeton en cache et le renouvelle."""
        _ = self.client  # construit le credential si besoin
        return self._credential.get_token(*(scopes or self._scopes)).token

    # --- API asynchrone (le SDK msgraph est async-only) ---
    async def get_me(self):
        """Utilisateur courant. Mode délégué uniquement."""
        if not self.delegated:
            raise RuntimeError("get_me() nécessite M365_USERNAME/M365_PASSWORD (mode délégué)")
        return await self.client.me.get()

    async def get_user(self, user_id_or_principal: str):
        """Un utilisateur par id ou UPN (User.Read.All en mode Application)."""
        return await self.client.users.by_user_id(user_id_or_principal).get()

    async def get_outlook_rule(self, rule_id: Optional[str] = None, name: Optional[str] = None, user_id: str = "me"):
        if user_id == "me" and self.delegated:
            return await self.client.me.mail_folders.by_mail_folder_id("inbox").message_rules.get()
        return await self.client.users.by_user_id(user_id).mail_folders.by_mail_folder_id("inbox").message_rules.get()

    # --- Variantes synchrones pour du code Django classique ---
    def get_me_sync(self):
        return async_to_sync(self.get_me)()

    def get_user_sync(self, user_id_or_principal: str):
        return async_to_sync(self.get_user)(user_id_or_principal)

    def get_outlook_rule_sync(self, rule_id: Optional[str] = None, name: Optional[str] = None, user_id: str = "me"):
        return async_to_sync(self.get_outlook_rule)(rule_id, name, user_id)


graph = Graph()
Graphe = graph  # alias de compatibilité

if __name__ == "__main__":
    import asyncio

    async def main():
        user = os.environ.get("M365_TEST_USER")
        try:
            if graph.delegated:
                me = await graph.get_me()

                print(f"OK : {me.display_name} ({me.mail}) delegated")
            elif user:
                u = await graph.get_user(user)
                rule = graph.get_outlook_rule_sync()
                print(rule)
                print(f"OK : {u.display_name} ({u.mail})")
            else:
                graph.client

                print("Client initialisé. Définissez M365_TEST_USER pour tester get_user().")
        except Exception as e:
            print(f"Erreur : {type(e).__name__}: {e}")

    asyncio.run(main())
