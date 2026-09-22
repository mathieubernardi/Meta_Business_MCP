"""Outils de publication (Facebook, Instagram, Threads).

Tous ces outils passent par `client.require_writes()` : sans
`META_ENABLE_WRITES=true`, ils refusent de s'exécuter.

Un outil qui ne peut pas aboutir lève une exception (voir `meta_mcp.errors`) :
le client MCP la reçoit avec `isError: true`.
"""

from __future__ import annotations

import asyncio
import mimetypes
import time
from pathlib import Path
from typing import Any

import httpx

from .._compat import MCPServer
from ..client import GraphAPIError, MetaClient
from ..constants import MAX_DIRECT_UPLOAD_BYTES, UPLOAD_TIMEOUT_SECONDS
from ..errors import ContainerNotReadyError, OperationError, ToolInputError

JSONDict = dict[str, Any]

_MAX_STATUS_POLLS = 20
# L'encodage d'un reel est nettement plus lent que celui d'une image.
_MAX_VIDEO_STATUS_POLLS = 100
_POLL_DELAY_SECONDS = 3.0
# Bornes de programmation imposées par Meta.
MIN_SCHEDULE_DELAY_SECONDS = 10 * 60
MAX_SCHEDULE_DELAY_SECONDS = 180 * 24 * 60 * 60


def _check_schedule(scheduled_publish_time: int) -> None:
    """Vérifie les bornes de programmation imposées par Meta."""
    delay = scheduled_publish_time - int(time.time())
    if delay < MIN_SCHEDULE_DELAY_SECONDS:
        raise ToolInputError(
            f"date de programmation trop proche ({delay} s d'avance) : "
            "Meta impose au moins 10 minutes d'avance"
        )
    if delay > MAX_SCHEDULE_DELAY_SECONDS:
        raise ToolInputError(
            f"date de programmation trop lointaine ({delay} s d'avance) : "
            "Meta impose au plus 6 mois d'avance"
        )


def _check_carousel_size(count: int) -> None:
    if not 2 <= count <= 10:
        raise ToolInputError("un carrousel Instagram accepte entre 2 et 10 images")


def _check_page_carousel_size(count: int) -> None:
    if count == 0:
        raise ToolInputError("un carrousel Facebook demande au moins une image")


def _local_file(file_path: str) -> Path:
    """Chemin d'un fichier local existant."""
    path = Path(file_path)
    if not path.is_file():
        raise ToolInputError(f"fichier introuvable : {file_path}")
    return path


async def _container_status(client: MetaClient, container_id: str) -> str:
    data = await client.get(container_id, {"fields": "status_code,status"})
    return str(data.get("status_code", "")).upper()


async def _wait_for_container(
    client: MetaClient, container_id: str, max_polls: int = _MAX_STATUS_POLLS
) -> str:
    """Attend qu'un conteneur Instagram soit prêt. Renvoie son statut final."""
    for _ in range(max_polls):
        status = await _container_status(client, container_id)
        if status in {"FINISHED", "ERROR", "EXPIRED"}:
            return status
        await asyncio.sleep(_POLL_DELAY_SECONDS)
    return "TIMEOUT"


async def _create_container(client: MetaClient, ig_user_id: str, params: JSONDict) -> str:
    """Crée un conteneur Instagram et renvoie son id."""
    container = await client.post(f"{ig_user_id}/media", params)
    container_id = str(container.get("id", ""))
    if not container_id:
        raise OperationError(f"création du conteneur Instagram échouée : {container}")
    return container_id


async def _publish_container(
    client: MetaClient,
    ig_user_id: str,
    container_id: str,
    max_polls: int = _MAX_STATUS_POLLS,
) -> JSONDict:
    """Attend qu'un conteneur Instagram soit prêt, puis le publie."""
    status = await _wait_for_container(client, container_id, max_polls)
    if status != "FINISHED":
        raise ContainerNotReadyError(container_id, status)
    published = await client.post(
        f"{ig_user_id}/media_publish", {"creation_id": container_id}
    )
    return {"published": published, "container_id": container_id}


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
    payload complet (`source`, `permalink_url`).
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
            raise OperationError(f"encodage de la vidéo {video_id} en erreur : {status}")
        await asyncio.sleep(_POLL_DELAY_SECONDS)
    raise OperationError(
        f"délai d'encodage dépassé pour la vidéo {video_id} "
        f"(dernier statut : {last.get('status')})"
    )


async def _delete_intermediate_video(
    client: MetaClient, video_id: str, token: str
) -> JSONDict:
    """Supprime la vidéo intermédiaire d'un reel, sans masquer l'issue de la publication."""
    try:
        await client.delete(video_id, token=token)
    except (GraphAPIError, httpx.HTTPError) as exc:
        return {"uploaded_fb_video_deleted": False, "cleanup_error": str(exc)}
    return {"uploaded_fb_video_deleted": True}


async def _upload_unpublished_photo(
    client: MetaClient, page_id: str, file_path: str, token: str
) -> JSONDict:
    """Téléverse un fichier local en photo non publiée sur la Page.

    Astuce de publication : une photo non publiée obtient quand même une URL
    CDN publique (`images[0].source`), réutilisable telle quelle par l'API
    Instagram (`image_url`) — pas besoin d'hébergement externe pour les
    carrousels ou images IG construits à partir de fichiers locaux.
    """
    path = _local_file(file_path)
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    uploaded = await client.post_multipart(
        f"{page_id}/photos",
        {"published": "false"},
        files={"source": (path.name, path.read_bytes(), content_type)},
        token=token,
    )
    media_id = str(uploaded.get("id", ""))
    if not media_id:
        raise OperationError(f"téléversement de {path.name} échoué : {uploaded}")
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
    """Téléverse une vidéo locale sur la Page et renvoie son id.

    Même astuce que pour les photos : une vidéo téléversée non publiée expose,
    une fois encodée, une URL `source` exploitable par l'ingestion Instagram
    (voir `_wait_for_page_video_source`). Cette URL est signée et temporaire —
    elle doit être consommée immédiatement, pas stockée.
    """
    path = _local_file(file_path)
    size = path.stat().st_size
    if size > MAX_DIRECT_UPLOAD_BYTES:
        raise ToolInputError(
            f"fichier trop volumineux pour un upload direct : "
            f"{size / 1024 / 1024:.1f} Mo > {MAX_DIRECT_UPLOAD_BYTES / 1024 / 1024:.0f} Mo "
            "— découpe la vidéo ou passe par le protocole d'upload repris de Meta"
        )
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
        raise OperationError(f"téléversement de {path.name} échoué : {uploaded}")

    result: JSONDict = {"id": video_id, "url": None, "permalink_url": None, "bytes": size}
    if scheduled_publish_time is not None:
        result["scheduled_publish_time"] = scheduled_publish_time
    elif not published:
        # Non publiée : l'URL `source` n'existe qu'après encodage ; l'appelant
        # l'attend s'il en a besoin.
        return result

    # Publiée ou programmée : Meta a pris la vidéo en charge. Attendre l'encodage
    # pourrait transformer un envoi réussi en erreur et pousser à republier
    # (doublon) — on lit les détails une seule fois, sans jamais échouer.
    try:
        detail = await client.get(video_id, {"fields": "source,permalink_url"}, token=token)
    except (GraphAPIError, httpx.HTTPError):
        return result
    result["url"] = detail.get("source")
    result["permalink_url"] = detail.get("permalink_url")
    return result


async def _post_with_attached_media(
    client: MetaClient, page_id: str, message: str, media_ids: list[str], token: str
) -> JSONDict:
    """Publie un post de Page rattachant des photos déjà téléversées non publiées."""
    payload: JSONDict = {"message": message}
    for index, media_id in enumerate(media_ids):
        payload[f"attached_media[{index}]"] = f'{{"media_fbid":"{media_id}"}}'
    result = await client.post(f"{page_id}/feed", payload, token=token)
    return {"post": result, "uploaded_media": media_ids}


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

        Pour programmer : `scheduled_publish_time` (horodatage Unix UTC, entre
        10 minutes et 6 mois dans le futur) ; Meta publie alors le post à l'heure
        dite, quelle que soit la valeur de `published`. `published=False` sans date
        crée un post non publié, absent du fil.
        """
        client.require_writes("publish_page_post")
        payload: JSONDict = {"message": message, "link": link}
        if scheduled_publish_time is not None:
            _check_schedule(scheduled_publish_time)
            payload["published"] = "false"
            payload["scheduled_publish_time"] = scheduled_publish_time
        elif not published:
            payload["published"] = "false"
        token = await client.page_token(page_id)
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
        _check_page_carousel_size(len(image_urls))
        token = await client.page_token(page_id)
        media_ids: list[str] = []
        for url in image_urls:
            uploaded = await client.post(
                f"{page_id}/photos", {"url": url, "published": "false"}, token=token
            )
            if not uploaded.get("id"):
                raise OperationError(f"téléversement de l'image échoué : {url} ({uploaded})")
            media_ids.append(str(uploaded["id"]))
        return await _post_with_attached_media(client, page_id, message, media_ids, token)

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
        path = _local_file(file_path)
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
        _check_page_carousel_size(len(file_paths))
        token = await client.page_token(page_id)
        media_ids: list[str] = []
        for file_path in file_paths:
            uploaded = await _upload_unpublished_photo(client, page_id, file_path, token)
            media_ids.append(uploaded["id"])
        return await _post_with_attached_media(client, page_id, message, media_ids, token)

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
            _check_schedule(scheduled_publish_time)
        token = await client.page_token(page_id)
        uploaded = await _upload_page_video(
            client,
            page_id,
            file_path,
            token,
            description=description,
            published=published,
            scheduled_publish_time=scheduled_publish_time,
        )
        if not published and scheduled_publish_time is None:
            # Vidéo non publiée : on attend son encodage pour exposer une URL
            # `source` réutilisable, par exemple pour alimenter un reel Instagram.
            ready = await _wait_for_page_video_source(client, uploaded["id"], token)
            uploaded["url"] = ready.get("source")
            uploaded["permalink_url"] = ready.get("permalink_url")
        return uploaded

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
        container_id = await _create_container(
            client, ig_user_id, {"image_url": image_url, "caption": caption}
        )
        return await _publish_container(client, ig_user_id, container_id)

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
        if not uploaded["url"]:
            raise OperationError(
                f"URL CDN introuvable pour la photo téléversée {uploaded['id']} ({file_path})"
            )
        result = await ig_publish_image(ig_user_id, uploaded["url"], caption)
        result["uploaded_fb_photo"] = uploaded["id"]
        return result

    @mcp.tool()
    async def ig_publish_carousel(
        ig_user_id: str, image_urls: list[str], caption: str | None = None
    ) -> JSONDict:
        """Publie un carrousel Instagram (2 à 10 images, URLs publiques JPEG)."""
        client.require_writes("ig_publish_carousel")
        _check_carousel_size(len(image_urls))
        children: list[str] = []
        for url in image_urls:
            item_id = await _create_container(
                client, ig_user_id, {"image_url": url, "is_carousel_item": "true"}
            )
            if (status := await _wait_for_container(client, item_id)) != "FINISHED":
                raise OperationError(f"image du carrousel non traitée (statut {status}) : {url}")
            children.append(item_id)
        container_id = await _create_container(
            client,
            ig_user_id,
            {"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption},
        )
        result = await _publish_container(client, ig_user_id, container_id)
        result["children"] = children
        return result

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
        _check_carousel_size(len(file_paths))
        token = await client.page_token(page_id)
        image_urls: list[str] = []
        uploaded_photo_ids: list[str] = []
        for file_path in file_paths:
            uploaded = await _upload_unpublished_photo(client, page_id, file_path, token)
            if not uploaded["url"]:
                raise OperationError(f"URL CDN introuvable pour la photo téléversée ({file_path})")
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
        """Publie un reel Instagram depuis une URL vidéo publique (MP4).

        L'encodage peut dépasser le délai d'attente : l'erreur donne alors le
        `container_id`, à suivre avec `ig_container_status` puis à publier avec
        `ig_publish_container`.
        """
        client.require_writes("ig_publish_reel")
        container_id = await _create_container(
            client,
            ig_user_id,
            {
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "cover_url": cover_url,
                "share_to_feed": "true" if share_to_feed else "false",
            },
        )
        return await _publish_container(
            client, ig_user_id, container_id, _MAX_VIDEO_STATUS_POLLS
        )

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

        Si Instagram traite encore la vidéo au-delà du délai d'attente, la vidéo
        intermédiaire est conservée (Instagram peut encore la lire) et l'erreur
        indique comment finir la publication.
        """
        client.require_writes("ig_publish_reel_from_file")
        token = await client.page_token(page_id)
        uploaded = await _upload_page_video(client, page_id, file_path, token)
        video_id = uploaded["id"]
        try:
            video = await _wait_for_page_video_source(client, video_id, token)
            result = await ig_publish_reel(
                ig_user_id, video["source"], caption, cover_url, share_to_feed
            )
        except Exception as exc:
            if isinstance(exc, ContainerNotReadyError) and exc.still_processing:
                # Instagram ingère peut-être encore la vidéo : sa source doit rester en ligne.
                raise OperationError(
                    f"{exc}. Vidéo intermédiaire {video_id} conservée pour l'ingestion : "
                    "supprime-la avec delete_page_post une fois le reel publié."
                ) from exc
            if not keep_fb_video:
                await _delete_intermediate_video(client, video_id, token)
            raise
        result["uploaded_fb_video"] = video_id
        if not keep_fb_video:
            result.update(await _delete_intermediate_video(client, video_id, token))
        return result

    @mcp.tool()
    async def ig_container_status(container_id: str) -> JSONDict:
        """Vérifie l'état d'un conteneur Instagram en cours de traitement."""
        return await client.get(container_id, {"fields": "status_code,status"})

    @mcp.tool()
    async def ig_publish_container(ig_user_id: str, container_id: str) -> JSONDict:
        """Publie un conteneur Instagram déjà créé, une fois son traitement terminé.

        Reprise après un délai dépassé (reel long à encoder) : suivre le conteneur
        avec `ig_container_status`, puis le publier ici dès qu'il est `FINISHED`.
        """
        client.require_writes("ig_publish_container")
        status = await _container_status(client, container_id)
        if status != "FINISHED":
            raise ContainerNotReadyError(container_id, status or "INCONNU")
        published = await client.post(
            f"{ig_user_id}/media_publish", {"creation_id": container_id}
        )
        return {"published": published, "container_id": container_id}

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
