"""Outils de publication (Facebook, Instagram, Threads).

Tous ces outils passent par `client.require_writes()` : sans
`META_ENABLE_WRITES=true`, ils refusent de s'exécuter.
"""

from __future__ import annotations

import asyncio
import mimetypes
import time
from pathlib import Path
from typing import Any

from .._compat import MCPServer
from ..client import MetaClient
from ..constants import MAX_DIRECT_UPLOAD_BYTES, UPLOAD_TIMEOUT_SECONDS

JSONDict = dict[str, Any]

_MAX_STATUS_POLLS = 20
# L'encodage d'un reel est nettement plus lent que celui d'une image.
_MAX_VIDEO_STATUS_POLLS = 100
_POLL_DELAY_SECONDS = 3.0
# Bornes de programmation imposées par Meta.
MIN_SCHEDULE_DELAY_SECONDS = 10 * 60
MAX_SCHEDULE_DELAY_SECONDS = 180 * 24 * 60 * 60


async def _wait_for_container(
    client: MetaClient, container_id: str, max_polls: int = _MAX_STATUS_POLLS
) -> str:
    """Attend qu'un conteneur Instagram soit prêt. Renvoie son statut final."""
    for _ in range(max_polls):
        data = await client.get(container_id, {"fields": "status_code,status"})
        status = str(data.get("status_code", "")).upper()
        if status in {"FINISHED", "ERROR", "EXPIRED"}:
            return status
        await asyncio.sleep(_POLL_DELAY_SECONDS)
    return "TIMEOUT"


async def _wait_for_page_video_source(
    client: MetaClient,
    video_id: str,
    token: str,
    max_polls: int = _MAX_VIDEO_STATUS_POLLS,
) -> JSONDict:
    """Attend qu'une vidéo de Page soit encodée et expose son URL `source`.

    Une vidéo fraîchement téléversée reste en `processing` : son champ `source`
    demeure vide tant que Meta n'a pas terminé l'encodage — d'où les échecs
    « URL introuvable » quand on l'exploite trop tôt pour l'ingestion Instagram.
    On interroge `status.video_status` jusqu'à `ready`, puis on renvoie le
    payload complet (`source`, `permalink_url`). En cas d'erreur d'encodage ou
    de dépassement du délai, on renvoie un dict contenant une clé `error`.
    """
    last: JSONDict = {}
    for _ in range(max_polls):
        last = await client.get(
            video_id, {"fields": "status,source,permalink_url"}, token=token
        )
        status = last.get("status") or {}
        video_status = str(status.get("video_status", "")).lower()
        if video_status == "ready" and last.get("source"):
            return last
        if video_status == "error":
            return {"error": "encodage de la vidéo en erreur", "detail": status}
        await asyncio.sleep(_POLL_DELAY_SECONDS)
    return {"error": "délai d'encodage dépassé", "detail": last}


async def _upload_unpublished_photo(
    client: MetaClient, page_id: str, file_path: str, token: str
) -> JSONDict:
    """Téléverse un fichier local en photo non publiée sur la Page.

    Astuce de publication : une photo non publiée obtient quand même une URL
    CDN publique (`images[0].source`), réutilisable telle quelle par l'API
    Instagram (`image_url`) — pas besoin d'hébergement externe pour les
    carrousels ou images IG construits à partir de fichiers locaux.
    """
    path = Path(file_path)
    if not path.is_file():
        return {"error": f"fichier introuvable : {file_path}"}
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    uploaded = await client.post_multipart(
        f"{page_id}/photos",
        {"published": "false"},
        files={"source": (path.name, path.read_bytes(), content_type)},
        token=token,
    )
    media_id = str(uploaded.get("id", ""))
    if not media_id:
        return {"error": "téléversement échoué", "detail": uploaded}
    detail = await client.get(media_id, {"fields": "images"}, token=token)
    images = detail.get("images") or []
    url = images[0].get("source") if images else None
    return {"id": media_id, "url": url}


async def _upload_page_video(
    client: MetaClient,
    page_id: str,
    file_path: str,
    token: str,
    *,
    description: str | None = None,
    published: bool = False,
    scheduled_publish_time: int | None = None,
) -> JSONDict:
    """Téléverse une vidéo locale sur la Page et renvoie son id et son URL source.

    Même astuce que pour les photos : une vidéo téléversée non publiée expose
    une URL `source` exploitable par l'ingestion Instagram, ce qui évite tout
    hébergement externe. Cette URL est signée et temporaire — elle doit être
    consommée immédiatement, pas stockée.
    """
    path = Path(file_path)
    if not path.is_file():
        return {"error": f"fichier introuvable : {file_path}"}
    size = path.stat().st_size
    if size > MAX_DIRECT_UPLOAD_BYTES:
        return {
            "error": (
                f"fichier trop volumineux pour un upload direct : "
                f"{size / 1024 / 1024:.1f} Mo > "
                f"{MAX_DIRECT_UPLOAD_BYTES / 1024 / 1024:.0f} Mo"
            ),
            "hint": "découpe la vidéo ou passe par le protocole d'upload repris de Meta",
        }
    payload: JSONDict = {"description": description}
    if scheduled_publish_time is not None:
        # Une vidéo programmée est déposée non publiée : Meta la publie à l'heure dite.
        payload["published"] = "false"
        payload["scheduled_publish_time"] = scheduled_publish_time
    else:
        payload["published"] = "true" if published else "false"
    content_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
    uploaded = await client.post_multipart(
        f"{page_id}/videos",
        payload,
        files={"source": (path.name, path.read_bytes(), content_type)},
        token=token,
        timeout=UPLOAD_TIMEOUT_SECONDS,
    )
    video_id = str(uploaded.get("id", ""))
    if not video_id:
        return {"error": "téléversement échoué", "detail": uploaded}

    # Une vidéo programmée n'a pas besoin de son URL `source` (elle sera publiée
    # par Meta à l'heure dite) : on évite l'attente d'encodage inutile.
    if scheduled_publish_time is not None:
        detail = await client.get(
            video_id, {"fields": "permalink_url"}, token=token
        )
        return {
            "id": video_id,
            "url": None,
            "permalink_url": detail.get("permalink_url"),
            "bytes": size,
            "scheduled_publish_time": scheduled_publish_time,
        }

    # Cas immédiat (notamment l'alimentation d'un reel Instagram) : on attend que
    # l'encodage soit terminé pour disposer d'une URL `source` exploitable.
    ready = await _wait_for_page_video_source(client, video_id, token)
    if ready.get("error"):
        return {"id": video_id, "url": None, "bytes": size, **ready}
    return {
        "id": video_id,
        "url": ready.get("source"),
        "permalink_url": ready.get("permalink_url"),
        "bytes": size,
    }


def register(mcp: MCPServer, client: MetaClient) -> None:
    """Enregistre les outils de publication sur le serveur MCP."""

    # ── Facebook ────────────────────────────────────────────────────────────
    @mcp.tool()
    async def publish_page_post(
        page_id: str,
        message: str,
        link: str | None = None,
        published: bool = True,
        scheduled_publish_time: int | None = None,
    ) -> JSONDict:
        """Publie un message texte (ou avec lien) sur une Page Facebook.

        Pour programmer : `published=False` + `scheduled_publish_time`
        (horodatage Unix, entre 10 min et 6 mois dans le futur).
        """
        client.require_writes("publish_page_post")
        token = await client.page_token(page_id)
        payload: JSONDict = {"message": message, "link": link}
        if not published:
            payload["published"] = "false"
            payload["scheduled_publish_time"] = scheduled_publish_time
        return await client.post(f"{page_id}/feed", payload, token=token)

    @mcp.tool()
    async def publish_page_photo(
        page_id: str,
        image_url: str,
        caption: str | None = None,
        published: bool = True,
    ) -> JSONDict:
        """Publie une photo sur une Page Facebook depuis une URL publique."""
        client.require_writes("publish_page_photo")
        token = await client.page_token(page_id)
        payload: JSONDict = {"url": image_url, "caption": caption}
        if not published:
            payload["published"] = "false"
        return await client.post(f"{page_id}/photos", payload, token=token)

    @mcp.tool()
    async def publish_page_carousel(
        page_id: str, image_urls: list[str], message: str
    ) -> JSONDict:
        """Publie un carrousel (plusieurs photos) sur une Page Facebook.

        Les images sont d'abord téléversées non publiées, puis rattachées au post.
        """
        client.require_writes("publish_page_carousel")
        token = await client.page_token(page_id)
        media_ids: list[str] = []
        for url in image_urls:
            uploaded = await client.post(
                f"{page_id}/photos", {"url": url, "published": "false"}, token=token
            )
            media_id = uploaded.get("id")
            if media_id:
                media_ids.append(str(media_id))
        payload: JSONDict = {"message": message}
        for index, media_id in enumerate(media_ids):
            payload[f"attached_media[{index}]"] = f'{{"media_fbid":"{media_id}"}}'
        result = await client.post(f"{page_id}/feed", payload, token=token)
        return {"post": result, "uploaded_media": media_ids}

    @mcp.tool()
    async def publish_page_photo_from_file(
        page_id: str,
        file_path: str,
        caption: str | None = None,
        published: bool = True,
    ) -> JSONDict:
        """Publie une photo sur une Page Facebook depuis un fichier local (upload direct)."""
        client.require_writes("publish_page_photo_from_file")
        token = await client.page_token(page_id)
        path = Path(file_path)
        if not path.is_file():
            return {"error": f"fichier introuvable : {file_path}"}
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data: JSONDict = {"caption": caption}
        if not published:
            data["published"] = "false"
        return await client.post_multipart(
            f"{page_id}/photos",
            data,
            files={"source": (path.name, path.read_bytes(), content_type)},
            token=token,
        )

    @mcp.tool()
    async def publish_page_carousel_from_files(
        page_id: str, file_paths: list[str], message: str
    ) -> JSONDict:
        """Publie un carrousel Facebook depuis des fichiers locaux (upload direct).

        Chaque fichier est téléversé non publié puis rattaché au post, comme
        `publish_page_carousel` mais sans passer par des URLs publiques.
        """
        client.require_writes("publish_page_carousel_from_files")
        token = await client.page_token(page_id)
        media_ids: list[str] = []
        for file_path in file_paths:
            uploaded = await _upload_unpublished_photo(client, page_id, file_path, token)
            if not uploaded.get("id"):
                return {"error": f"téléversement échoué : {file_path}", "detail": uploaded}
            media_ids.append(uploaded["id"])
        payload: JSONDict = {"message": message}
        for index, media_id in enumerate(media_ids):
            payload[f"attached_media[{index}]"] = f'{{"media_fbid":"{media_id}"}}'
        result = await client.post(f"{page_id}/feed", payload, token=token)
        return {"post": result, "uploaded_media": media_ids}

    @mcp.tool()
    async def publish_page_video_from_file(
        page_id: str,
        file_path: str,
        description: str | None = None,
        published: bool = True,
        scheduled_publish_time: int | None = None,
    ) -> JSONDict:
        """Publie une vidéo sur une Page Facebook depuis un fichier local.

        Téléversement multipart direct sur `{page_id}/videos`, sans hébergement
        externe. `description` sert de légende. Avec `published=False`, la vidéo
        est téléversée sans apparaître dans le fil — utile pour préparer une
        publication ou alimenter un reel Instagram.

        Pour programmer : `scheduled_publish_time` (horodatage Unix UTC, entre
        10 minutes et 6 mois dans le futur). Meta publie alors la vidéo à
        l'heure dite, sans que la machine locale ait besoin d'être allumée.
        """
        client.require_writes("publish_page_video_from_file")
        if scheduled_publish_time is not None:
            delay = scheduled_publish_time - int(time.time())
            if delay < MIN_SCHEDULE_DELAY_SECONDS:
                return {
                    "error": "date de programmation trop proche",
                    "hint": "Meta impose au moins 10 minutes d'avance",
                    "seconds_ahead": delay,
                }
            if delay > MAX_SCHEDULE_DELAY_SECONDS:
                return {
                    "error": "date de programmation trop lointaine",
                    "hint": "Meta impose au plus 6 mois d'avance",
                    "seconds_ahead": delay,
                }
        token = await client.page_token(page_id)
        return await _upload_page_video(
            client,
            page_id,
            file_path,
            token,
            description=description,
            published=published,
            scheduled_publish_time=scheduled_publish_time,
        )

    @mcp.tool()
    async def delete_page_post(post_id: str) -> JSONDict:
        """Supprime une publication Facebook. Action irréversible."""
        client.require_writes("delete_page_post")
        return await client.delete(post_id)

    @mcp.tool()
    async def reply_to_comment(comment_id: str, message: str) -> JSONDict:
        """Répond à un commentaire (Facebook ou Instagram)."""
        client.require_writes("reply_to_comment")
        return await client.post(f"{comment_id}/replies", {"message": message})

    # ── Instagram ───────────────────────────────────────────────────────────
    @mcp.tool()
    async def ig_publish_image(
        ig_user_id: str, image_url: str, caption: str | None = None
    ) -> JSONDict:
        """Publie une image sur Instagram depuis une URL publique (JPEG)."""
        client.require_writes("ig_publish_image")
        container = await client.post(
            f"{ig_user_id}/media", {"image_url": image_url, "caption": caption}
        )
        container_id = str(container.get("id", ""))
        status = await _wait_for_container(client, container_id)
        if status != "FINISHED":
            return {"error": f"conteneur non prêt (statut {status})",
                    "container_id": container_id}
        published = await client.post(
            f"{ig_user_id}/media_publish", {"creation_id": container_id}
        )
        return {"published": published, "container_id": container_id}

    @mcp.tool()
    async def ig_publish_image_from_file(
        ig_user_id: str, page_id: str, file_path: str, caption: str | None = None
    ) -> JSONDict:
        """Publie une image Instagram depuis un fichier local.

        Le fichier est d'abord téléversé non publié sur la Page Facebook liée
        (`page_id`) via upload multipart, ce qui produit une URL CDN publique
        réutilisée pour la publication Instagram — sans hébergement externe.
        """
        client.require_writes("ig_publish_image_from_file")
        token = await client.page_token(page_id)
        uploaded = await _upload_unpublished_photo(client, page_id, file_path, token)
        if not uploaded.get("url"):
            return {"error": "téléversement échoué ou URL introuvable", "detail": uploaded}
        result = await ig_publish_image(ig_user_id, uploaded["url"], caption)
        result["uploaded_fb_photo"] = uploaded["id"]
        return result

    @mcp.tool()
    async def ig_publish_carousel(
        ig_user_id: str, image_urls: list[str], caption: str | None = None
    ) -> JSONDict:
        """Publie un carrousel Instagram (2 à 10 images, URLs publiques JPEG)."""
        client.require_writes("ig_publish_carousel")
        if not 2 <= len(image_urls) <= 10:
            return {"error": "un carrousel Instagram accepte entre 2 et 10 images"}
        children: list[str] = []
        for url in image_urls:
            item = await client.post(
                f"{ig_user_id}/media",
                {"image_url": url, "is_carousel_item": "true"},
            )
            item_id = str(item.get("id", ""))
            if await _wait_for_container(client, item_id) != "FINISHED":
                return {"error": f"image non traitée : {url}"}
            children.append(item_id)
        container = await client.post(
            f"{ig_user_id}/media",
            {"media_type": "CAROUSEL", "children": ",".join(children),
             "caption": caption},
        )
        container_id = str(container.get("id", ""))
        status = await _wait_for_container(client, container_id)
        if status != "FINISHED":
            return {"error": f"carrousel non prêt (statut {status})",
                    "container_id": container_id}
        published = await client.post(
            f"{ig_user_id}/media_publish", {"creation_id": container_id}
        )
        return {"published": published, "children": children}

    @mcp.tool()
    async def ig_publish_carousel_from_files(
        ig_user_id: str,
        page_id: str,
        file_paths: list[str],
        caption: str | None = None,
    ) -> JSONDict:
        """Publie un carrousel Instagram (2 à 10 images) depuis des fichiers locaux.

        Chaque fichier est d'abord téléversé non publié sur la Page Facebook
        liée (`page_id`) via upload multipart ; l'URL CDN publique obtenue est
        ensuite réutilisée pour construire le carrousel Instagram — plus besoin
        d'héberger les images ailleurs avant de publier.
        """
        client.require_writes("ig_publish_carousel_from_files")
        if not 2 <= len(file_paths) <= 10:
            return {"error": "un carrousel Instagram accepte entre 2 et 10 images"}
        token = await client.page_token(page_id)
        image_urls: list[str] = []
        uploaded_photo_ids: list[str] = []
        for file_path in file_paths:
            uploaded = await _upload_unpublished_photo(client, page_id, file_path, token)
            if not uploaded.get("url"):
                return {"error": f"téléversement échoué : {file_path}", "detail": uploaded}
            image_urls.append(uploaded["url"])
            uploaded_photo_ids.append(uploaded["id"])
        result = await ig_publish_carousel(ig_user_id, image_urls, caption)
        result["uploaded_fb_photos"] = uploaded_photo_ids
        return result

    @mcp.tool()
    async def ig_publish_reel(
        ig_user_id: str,
        video_url: str,
        caption: str | None = None,
        cover_url: str | None = None,
        share_to_feed: bool = True,
    ) -> JSONDict:
        """Publie un reel Instagram depuis une URL vidéo publique (MP4)."""
        client.require_writes("ig_publish_reel")
        container = await client.post(
            f"{ig_user_id}/media",
            {
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "cover_url": cover_url,
                "share_to_feed": "true" if share_to_feed else "false",
            },
        )
        container_id = str(container.get("id", ""))
        status = await _wait_for_container(client, container_id, _MAX_VIDEO_STATUS_POLLS)
        if status != "FINISHED":
            return {"error": f"vidéo non prête (statut {status})",
                    "container_id": container_id,
                    "hint": "relance ig_container_status : l'encodage peut être long"}
        published = await client.post(
            f"{ig_user_id}/media_publish", {"creation_id": container_id}
        )
        return {"published": published, "container_id": container_id}

    @mcp.tool()
    async def ig_publish_reel_from_file(
        ig_user_id: str,
        page_id: str,
        file_path: str,
        caption: str | None = None,
        cover_url: str | None = None,
        share_to_feed: bool = True,
        keep_fb_video: bool = False,
    ) -> JSONDict:
        """Publie un reel Instagram depuis un fichier vidéo local (MP4).

        La vidéo est d'abord téléversée non publiée sur la Page Facebook liée
        (`page_id`), ce qui produit une URL source réutilisée pour l'ingestion
        Instagram — sans hébergement externe. Cette vidéo intermédiaire est
        ensuite supprimée, sauf si `keep_fb_video=True`.
        """
        client.require_writes("ig_publish_reel_from_file")
        token = await client.page_token(page_id)
        uploaded = await _upload_page_video(client, page_id, file_path, token)
        if not uploaded.get("url"):
            return {"error": "téléversement échoué ou URL introuvable", "detail": uploaded}
        result: JSONDict = await ig_publish_reel(
            ig_user_id, uploaded["url"], caption, cover_url, share_to_feed
        )
        result["uploaded_fb_video"] = uploaded["id"]
        if not keep_fb_video:
            try:
                await client.delete(str(uploaded["id"]), token=token)
                result["uploaded_fb_video_deleted"] = True
            except Exception as exc:  # noqa: BLE001 - nettoyage best-effort
                result["uploaded_fb_video_deleted"] = False
                result["cleanup_error"] = str(exc)
        return result

    @mcp.tool()
    async def ig_container_status(container_id: str) -> JSONDict:
        """Vérifie l'état d'un conteneur Instagram en cours de traitement."""
        return await client.get(container_id, {"fields": "status_code,status"})

    @mcp.tool()
    async def ig_reply_to_comment(comment_id: str, message: str) -> JSONDict:
        """Répond à un commentaire Instagram."""
        client.require_writes("ig_reply_to_comment")
        return await client.post(f"{comment_id}/replies", {"message": message})

    @mcp.tool()
    async def ig_delete_comment(comment_id: str) -> JSONDict:
        """Supprime un commentaire Instagram. Action irréversible."""
        client.require_writes("ig_delete_comment")
        return await client.delete(comment_id)

    @mcp.tool()
    async def ig_publishing_limit(ig_user_id: str) -> JSONDict:
        """Quota de publication Instagram restant (limite de 50 posts / 24 h)."""
        return await client.get(
            f"{ig_user_id}/content_publishing_limit",
            {"fields": "config,quota_usage"},
        )

    # ── Threads ─────────────────────────────────────────────────────────────
    @mcp.tool()
    async def publish_thread(text: str, image_url: str | None = None) -> JSONDict:
        """Publie une publication sur Threads (texte, ou image + texte)."""
        client.require_writes("publish_thread")
        payload: JSONDict = {
            "media_type": "IMAGE" if image_url else "TEXT",
            "text": text,
            "image_url": image_url,
        }
        container = await client.threads_post("me/threads", payload)
        container_id = str(container.get("id", ""))
        published = await client.threads_post(
            "me/threads_publish", {"creation_id": container_id}
        )
        return {"published": published, "container_id": container_id}
