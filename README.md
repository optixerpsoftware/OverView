# OverView

Application d'administration de tenants Microsoft 365 (Django + Next.js) : organisations, connecteurs, boîtes aux lettres,
sécurité des comptes, gouvernance, partage OneDrive/SharePoint, flux de messagerie et quarantaine, tableau de bord,
alertes et notifications, recherche globale (Ctrl+K), actions en lot.

## Démarrage

```bash
docker compose up -d --build
```

- Frontend : http://localhost:3000
- API : http://localhost:8000

Services : `db` (PostgreSQL), `redis`, `backend` (Daphne, API + WebSocket), `worker` (Celery + planificateur),
`frontend` (Next.js). Les synchronisations tournent en arrière-plan (sécurité des comptes et alertes toutes les
10 min, règles Outlook toutes les 15 min, gouvernance et partage une fois par jour).

## Connecter un tenant Microsoft 365 en un clic (consentement administrateur)

Au lieu de créer une inscription d'application dans **chaque** tenant client et de recopier son ID et son secret,
OverView utilise **une seule application Azure multi-tenant**. Pour chaque client, un administrateur clique sur
**« Connecter Microsoft 365 »**, se connecte chez Microsoft et accepte les permissions : le connecteur est créé
automatiquement au retour.

### 1. Inscrire l'application OverView (une seule fois, dans votre propre tenant)

> Vous avez déjà une inscription OverView (mono-tenant) ? Inutile d'en recréer une : ouvrez-la →
> **Authentification** → *Types de comptes pris en charge* → **multilocataire**, ajoutez l'URI de redirection
> ci-dessous, enregistrez. Les permissions déjà accordées dans votre tenant restent valables.

1. Portail Azure → **Microsoft Entra ID → Inscriptions d'applications → Nouvelle inscription**.
2. Nom : `OverView`.
3. **Types de comptes pris en charge** : *Comptes dans un annuaire d'organisation (tout locataire Microsoft Entra ID —
   multilocataire)*.
4. **URI de redirection** : plateforme **Web**, valeur :

   ```
   http://localhost:8000/api/connectors/consent/callback/
   ```

   En production, remplacez par l'adresse publique de l'API, en **https** (Microsoft n'accepte `http` que pour
   `localhost`) : `https://api.exemple.com/api/connectors/consent/callback/`. La valeur exacte attendue est affichée
   par `GET /api/connectors/consent/config/` (champ `redirect_uri`).
5. **Certificats et secrets → Nouveau secret client** : copiez la **valeur** (elle n'est affichée qu'une fois).
6. **Autorisations de l'API → Ajouter une autorisation**, type **Autorisations d'application** :

   | API | Permissions |
   |---|---|
   | Microsoft Graph | `User.Read.All`, `User.ReadWrite.All`, `Directory.Read.All`, `Group.ReadWrite.All`, `AuditLog.Read.All`, `Reports.Read.All`, `Organization.Read.All`, `Policy.Read.All`, `Policy.ReadWrite.DeviceConfiguration`, `Policy.ReadWrite.AuthenticationMethod`, `UserAuthenticationMethod.ReadWrite.All`, `Device.ReadWrite.All`, `Application.Read.All`, `Mail.ReadWrite`, `MailboxSettings.ReadWrite`, `Files.ReadWrite.All`, `Sites.Read.All` |
   | Office 365 Exchange Online | `Exchange.ManageAsApp` (flux de messagerie et quarantaine) |

   Ne cochez que ce dont vous avez besoin : chaque client verra et acceptera exactement cette liste.

### 2. Configurer le serveur OverView

Dans `.env` à la racine :

```dotenv
M365_APPLICATION_ID=<ID d'application (client) de l'inscription OverView>
M365_TOKEN=<valeur du secret client>
# Adresses publiques : retour du consentement et liens des notifications
API_PUBLIC_URL=http://localhost:8000
APP_PUBLIC_URL=http://localhost:3000
```

Puis `docker compose up -d`. Le bouton **Connecter Microsoft 365** apparaît sur la page d'une organisation dès que
`M365_APPLICATION_ID` et `M365_TOKEN` sont définis.

### 3. Connecter un client

1. OverView → **Organisations** → ouvrez (ou créez) l'organisation du client.
2. **Connecter Microsoft 365** : rien à saisir, le bouton redirige directement vers Microsoft. Le tenant connecté est
   celui du compte administrateur qui se connecte (utilisez une fenêtre privée si votre navigateur est déjà connecté à
   un autre tenant).
3. Sur la page Microsoft, connectez-vous avec un **administrateur général** du tenant **client**, puis **Accepter**.
4. Retour automatique dans OverView : le connecteur est créé, testé et nommé d'après l'organisation Microsoft 365.
   Si Microsoft n'a pas encore propagé le consentement (quelques secondes), le message l'indique : utilisez
   **Tester la connexion** une minute plus tard.

Reconnecter un tenant déjà connu (après un retrait de consentement, par exemple) réactive le connecteur existant au
lieu d'en créer un second.

### 4. Étape manuelle restante : Exchange Online

La permission `Exchange.ManageAsApp` ne suffit pas seule : dans **chaque** tenant client, un administrateur doit
attribuer le rôle **Exchange Administrator** à l'application OverView :
Entra ID → **Rôles et administrateurs → Exchange Administrator → Ajouter des attributions** → rechercher `OverView`.
Sans ce rôle, le flux de messagerie et la quarantaine renvoient « Accès refusé par Exchange Online ».

### Sécurité du flux

- Le paramètre `state` envoyé à Microsoft est **signé** et lié à une demande enregistrée : il expire après
  **15 minutes** et n'est utilisable **qu'une fois** (un retour rejoué ou falsifié est refusé).
- Le callback ne crée de connecteur que pour l'organisation choisie par l'utilisateur connecté qui a lancé la demande.
- Le secret de l'application reste côté serveur (chiffré en base dans chaque connecteur).
- **Rotation du secret** : créez un nouveau secret dans Azure, mettez à jour `M365_TOKEN`, puis utilisez
  **Changer le secret** sur les connecteurs existants (ou reconnectez-les avec le bouton).
- Un client peut retirer l'accès à tout moment : Entra ID → **Applications d'entreprise → OverView → Supprimer**.

### Méthode manuelle (toujours disponible)

**Ajouter manuellement** sur la page de l'organisation : ID du tenant, ID de l'application et secret d'une
inscription créée dans le tenant du client (application mono-tenant). Utile si le client refuse une application
multi-tenant.

## Alertes et notifications

Les alertes sont recalculées toutes les 10 minutes à partir des données synchronisées : liens anonymes et partages
externes, règles Outlook qui transfèrent vers l'extérieur ou suppriment des messages, comptes sans MFA, connexions
depuis un nouveau pays ou rafales d'échecs, constats de gouvernance graves, messages malveillants en quarantaine.

Notifications par organisation : **Alertes → Notifications** (courriels et/ou webhook Teams, gravité minimale).
Pour les courriels, configurez un vrai serveur d'envoi dans `config/settings.py` (`MAILERS`) ; par défaut, ils sont
affichés dans les journaux du worker. Pour Teams : dans le canal, **Workflows → « Publier dans un canal lorsqu'une
demande de webhook est reçue »**, puis collez l'URL générée.

## Accès depuis un autre poste du réseau

Ajoutez l'adresse IP du serveur dans `.env` : `EXTRA_HOSTS=192.168.1.10`, puis `docker compose up -d`. Ouvrez
`http://192.168.1.10:3000` ; les ports 3000 et 8000 doivent être joignables.

## Licence

Code source disponible sous la licence [PolyForm Noncommercial 1.0.0](LICENSE).
Vous pouvez lire, utiliser, modifier et partager le code **à des fins non commerciales uniquement**
(étude, recherche, usage personnel, associations, établissements d'enseignement…).
Tout usage commercial est interdit sans autorisation écrite du titulaire des droits.

> Ce n'est pas une licence « open source » au sens de l'OSI (celles-ci interdisent de restreindre l'usage commercial) :
> le terme exact est *source-available*.
