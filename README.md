# meta-mcp-py

Serveur **MCP en Python** pour Meta Business : Facebook Pages, Instagram et Threads.
Lecture des statistiques et publication de contenu, directement depuis Claude.

Gratuit et sans intermédiaire : le serveur tape la **Graph API de Meta**, qui est
gratuite. Ton token reste chez toi et n'est envoyé qu'à `graph.facebook.com` et
`graph.threads.net`.

## Points clés

- **56 outils** couvrant Pages, Instagram, Threads, statistiques, audiences et publication
- **Garde-fou d'écriture** : sans `META_ENABLE_WRITES=true`, aucune publication ni
  suppression n'est possible — impossible de poster par accident
- **Aucune donnée envoyée à un tiers** : pas de télémétrie, pas de service externe
- Entièrement **typé**, testé (`pytest`), vérifié (`ruff`, `mypy strict`)
- Compatible SDK MCP **v1 et v2** (couche de compatibilité interne)

## Installation

```bash
git clone https://github.com/mathieubernardi/Meta_Business_MCP.git
cd Meta_Business_MCP
python -m venv .venv
source .venv/bin/activate      # Windows : .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env           # puis remplis META_ACCESS_TOKEN
```

## Configuration dans Claude

Ajoute ce bloc à ta configuration MCP :

```json
{
  "mcpServers": {
    "meta": {
      "command": "python",
      "args": ["-m", "meta_mcp"],
      "cwd": "/chemin/vers/meta-mcp-py",
      "env": {
        "META_ACCESS_TOKEN": "ton_token_longue_duree",
        "THREADS_ACCESS_TOKEN": "",
        "META_ENABLE_WRITES": "false"
      }
    }
  }
}
```

`THREADS_ACCESS_TOKEN` est optionnel : à ne renseigner que si ton token Threads
diffère du token Meta principal. Sans lui, les outils Threads réutilisent
`META_ACCESS_TOKEN`.

## Obtenir un token (gratuit, une seule fois)

1. Crée une app sur https://developers.facebook.com/apps (type *Business*).
2. Ouvre le [Graph API Explorer](https://developers.facebook.com/tools/explorer).
3. Ajoute les permissions **en lecture seule** :
   `pages_read_engagement`, `pages_show_list`, `read_insights`,
   `instagram_basic`, `instagram_manage_insights`.
   Pour publier, ajoute `pages_manage_posts` et `instagram_content_publish`.
   Pour les audiences publicitaires, ajoute `ads_management`.
4. Échange le token court contre un token longue durée (~60 jours) :

   ```bash
   curl "https://graph.facebook.com/v21.0/oauth/access_token?grant_type=fb_exchange_token&client_id=APP_ID&client_secret=APP_SECRET&fb_exchange_token=TOKEN_COURT"
   ```

Ton compte Instagram doit être **Business/Créateur** et rattaché à la Page.

## Démarrage rapide

Une fois branché, demande simplement :

1. `health_check` — vérifie la configuration
2. `list_pages` — récupère l'ID de ta Page
3. `account_summary` — bilan Facebook + Instagram sur 30 jours

## Outils disponibles

### Diagnostic
`health_check` · `token_permissions` · `token_info` · `graph_get`

### Facebook Pages (lecture)
`list_pages` · `get_page` · `list_page_posts` · `get_post` · `list_post_comments`
`get_page_insights` · `get_post_insights`

### Instagram (lecture)
`find_instagram_account` · `get_ig_account` · `list_ig_media` · `get_ig_media`
`get_ig_media_insights` · `get_ig_account_insights` · `get_ig_audience_demographics`
`list_ig_stories` · `get_ig_story_insights` · `list_ig_comments`

### Threads (lecture)
`get_threads_profile` · `list_threads_posts` · `get_threads_insights`

### Analyse
`account_summary` · `top_ig_posts` · `follower_growth`

### Audience organique — aucun compte publicitaire requis
`get_page_audience` · `audience_overview`

Qui sont tes abonnés : répartition par pays, ville, âge/genre et langue.
`audience_overview` renvoie le top 5 de chaque catégorie en un appel.

> Meta n'agrège pas ces données tant que l'audience est trop petite : sur une page
> jeune, attends-toi à un résultat vide et un champ `note` explicatif.

### Audiences publicitaires — nécessite `ads_management` + compte publicitaire
`list_ad_accounts` · `list_custom_audiences` · `get_custom_audience`
`create_custom_audience` · `add_users_to_audience` · `remove_users_from_audience`
`create_lookalike_audience` · `delete_custom_audience`

Les données personnelles (email, téléphone, nom) sont normalisées puis **hachées en
SHA-256 localement** avant tout envoi : Meta ne reçoit jamais de donnée en clair.

> **RGPD** : constituer une audience à partir de données clients suppose une base
> légale et une information préalable des personnes. `remove_users_from_audience`
> permet de traiter une demande d'opposition ou d'effacement.

### Publication — nécessite `META_ENABLE_WRITES=true`
`publish_page_post` · `publish_page_photo` · `publish_page_carousel`
`publish_page_photo_from_file` · `publish_page_carousel_from_files`
`publish_page_video_from_file`
`delete_page_post` · `reply_to_comment` · `ig_publish_image` · `ig_publish_carousel`
`ig_publish_image_from_file` · `ig_publish_carousel_from_files`
`ig_publish_reel` · `ig_publish_reel_from_file` · `ig_container_status`
`ig_publish_container`
`ig_reply_to_comment` · `ig_delete_comment`
`ig_publishing_limit` · `publish_thread`

Les outils `_from_file(s)` téléversent des fichiers locaux (upload multipart) au
lieu de nécessiter des URLs publiques. Pour Instagram, l'astuce consiste à
téléverser chaque photo en non publiée sur la Page Facebook liée (`page_id`) :
Meta lui attribue quand même une URL CDN publique, réutilisée pour construire
l'image ou le carrousel Instagram — sans aucun hébergement externe.

### Vidéo
`publish_page_video_from_file` publie un MP4 local sur la Page Facebook
(`description` sert de légende). Avec `published=False`, la vidéo est téléversée
sans apparaître dans le fil. Avec `scheduled_publish_time` (horodatage Unix UTC,
entre 10 minutes et 6 mois dans le futur), Meta la publie à l'heure dite —
la machine locale n'a pas besoin d'être allumée à ce moment-là.

⚠️ Instagram n'a pas d'équivalent : l'API Content Publishing ne connaît aucune
programmation, un reel part à l'instant où `ig_publish_reel*` est appelé. Pour
programmer côté Instagram, il faut passer par Meta Business Suite ou déclencher
l'outil à l'heure voulue.

`ig_publish_reel_from_file` applique aux vidéos la même astuce que pour les
photos : le MP4 est téléversé non publié sur la Page liée, son URL `source` est
réutilisée pour l'ingestion du reel, puis la vidéo intermédiaire est supprimée
(`keep_fb_video=True` pour la conserver). Deux limites à connaître :

- l'upload direct est plafonné à `MAX_DIRECT_UPLOAD_BYTES` (200 Mo) ; au-delà,
  Meta impose son protocole d'upload repris, non implémenté ici ;
- l'encodage d'un reel est lent — l'attente va jusqu'à 5 minutes. Au-delà, l'outil
  échoue en donnant le `container_id` et **conserve** la vidéo intermédiaire,
  qu'Instagram peut encore être en train de lire : suis le conteneur avec
  `ig_container_status`, publie-le avec `ig_publish_container`, puis supprime la
  vidéo avec `delete_page_post`. En cas d'échec définitif (conteneur refusé,
  publication rejetée), la vidéo intermédiaire est supprimée.

Instagram attend un reel en 9:16. Une vidéo 4:5 ou carrée est acceptée mais
recadrée ou encadrée à l'affichage.

## Sécurité

Le mode écriture est **désactivé par défaut**. C'est volontaire : un assistant qui
peut publier ou supprimer sans filet est un risque réel. Deux niveaux de protection :

1. `META_ENABLE_WRITES` doit valoir `true` explicitement.
2. Le token lui-même doit porter les permissions de publication.

Tant que l'un des deux manque, rien ne peut être publié ni supprimé.

## Développement

```bash
pytest              # tests
ruff check src tests
mypy src
```

## Notes

Les noms de métriques Meta évoluent d'une version d'API à l'autre. Ils sont
regroupés dans `src/meta_mcp/constants.py` — un seul endroit à ajuster.

Un outil qui ne peut pas aboutir (paramètre invalide, fichier introuvable,
conteneur Instagram refusé…) lève une erreur : le client MCP la reçoit avec
`isError: true`. Les résultats partiels, eux, ne sont pas des erreurs : les outils
de statistiques renvoient un champ `errors` listant les métriques indisponibles
plutôt que d'échouer complètement.

## Licence

MIT — voir [LICENSE](LICENSE).
