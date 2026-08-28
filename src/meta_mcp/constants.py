"""Constantes de l'API Meta (versions, endpoints, jeux de champs)."""

from __future__ import annotations

from typing import Final

GRAPH_API_VERSION: Final[str] = "v21.0"
GRAPH_API_BASE: Final[str] = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
THREADS_API_BASE: Final[str] = "https://graph.threads.net/v1.0"

TIMEOUT_SECONDS: Final[float] = 30.0
# Un téléversement vidéo est bien plus lent qu'un appel Graph classique.
UPLOAD_TIMEOUT_SECONDS: Final[float] = 600.0
# Au-delà, Meta recommande le protocole d'upload repris (non implémenté ici).
MAX_DIRECT_UPLOAD_BYTES: Final[int] = 200 * 1024 * 1024
CHARACTER_LIMIT: Final[int] = 25_000

PAGE_FIELDS: Final[str] = (
    "id,name,category,fan_count,followers_count,link,description,about,"
    "access_token,instagram_business_account"
)
POST_FIELDS: Final[str] = (
    "id,message,story,created_time,full_picture,permalink_url,from,attachments,"
    "shares,comments.summary(true).limit(0),reactions.summary(true).limit(0)"
)
IG_ACCOUNT_FIELDS: Final[str] = (
    "id,username,name,biography,followers_count,follows_count,media_count,"
    "profile_picture_url,website"
)
IG_MEDIA_FIELDS: Final[str] = (
    "id,media_type,media_product_type,media_url,thumbnail_url,permalink,caption,"
    "like_count,comments_count,timestamp"
)
THREADS_PROFILE_FIELDS: Final[str] = (
    "id,username,name,threads_profile_picture_url,threads_biography"
)
THREADS_MEDIA_FIELDS: Final[str] = (
    "id,media_product_type,media_type,media_url,permalink,username,text,timestamp,"
    "shortcode,thumbnail_url,is_quote_post"
)

# Métriques quotidiennes d'une Page Facebook.
FB_PAGE_DAILY_METRICS: Final[tuple[str, ...]] = (
    "page_impressions_unique",
    "page_post_engagements",
    "page_daily_follows_unique",
    "page_views_total",
)
# Métriques d'un post Facebook.
FB_POST_METRICS: Final[tuple[str, ...]] = (
    "post_impressions_unique",
    "post_engaged_users",
    "post_clicks",
    "post_reactions_by_type_total",
)
# Métriques quotidiennes d'un compte Instagram.
IG_ACCOUNT_DAILY_METRICS: Final[tuple[str, ...]] = ("reach", "profile_views")
# Métriques d'un média Instagram (post/reel).
IG_MEDIA_METRICS: Final[tuple[str, ...]] = (
    "reach",
    "likes",
    "comments",
    "saved",
    "shares",
    "total_interactions",
)
# Métriques d'une story Instagram.
IG_STORY_METRICS: Final[tuple[str, ...]] = ("reach", "replies", "navigation")
# Répartitions démographiques des abonnés Instagram.
IG_DEMOGRAPHIC_BREAKDOWNS: Final[tuple[str, ...]] = ("age,gender", "country", "city")

# Répartitions démographiques des abonnés d'une Page Facebook (organique).
FB_FANS_BREAKDOWNS: Final[dict[str, str]] = {
    "country": "page_fans_country",
    "city": "page_fans_city",
    "age_gender": "page_fans_gender_age",
    "locale": "page_fans_locale",
}

# Champs d'un compte publicitaire et d'une audience personnalisée.
AD_ACCOUNT_FIELDS: Final[str] = (
    "id,name,account_id,account_status,currency,timezone_name,amount_spent,business"
)
AUDIENCE_FIELDS: Final[str] = (
    "id,name,description,subtype,approximate_count_lower_bound,"
    "approximate_count_upper_bound,time_created,time_updated,delivery_status,"
    "operation_status,data_source"
)

# Schémas acceptés pour alimenter une audience personnalisée.
# Les valeurs sont normalisées puis hachées en SHA-256 localement, avant envoi.
AUDIENCE_SCHEMAS: Final[tuple[str, ...]] = ("EMAIL", "PHONE", "FN", "LN")
