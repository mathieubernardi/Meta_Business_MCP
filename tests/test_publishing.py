"""Tests des outils de publication depuis des fichiers locaux."""

from __future__ import annotations

import time
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from meta_mcp.config import Settings
from meta_mcp.constants import GRAPH_API_BASE
from meta_mcp.server import build_server
from meta_mcp.tools import publishing


@pytest.fixture
def settings() -> Settings:
    return Settings(access_token="fake-token", enable_writes=True)


@pytest.fixture
def local_photo(tmp_path):
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"fake-jpeg-bytes")
    return path


def _form(request: httpx.Request) -> dict[str, str]:
    """Décode un corps application/x-www-form-urlencoded."""
    return {key: values[0] for key, values in parse_qs(request.content.decode()).items()}


def _mock_page_token() -> None:
    respx.get(f"{GRAPH_API_BASE}/123").mock(
        return_value=httpx.Response(200, json={"access_token": "page-token"})
    )


def _mock_reel_upload() -> None:
    """Upload FB réussi, vidéo encodée, conteneur Instagram créé."""
    _mock_page_token()
    respx.post(f"{GRAPH_API_BASE}/123/videos").mock(
        return_value=httpx.Response(200, json={"id": "video-1"})
    )
    respx.get(f"{GRAPH_API_BASE}/video-1").mock(
        return_value=httpx.Response(
            200,
            json={"status": {"video_status": "ready"}, "source": "https://cdn.fb/v.mp4"},
        )
    )
    respx.post(f"{GRAPH_API_BASE}/999/media").mock(
        return_value=httpx.Response(200, json={"id": "container-1"})
    )


def _mock_container_status(status: str) -> None:
    respx.get(f"{GRAPH_API_BASE}/container-1").mock(
        return_value=httpx.Response(200, json={"status_code": status})
    )


def _mock_unpublished_upload(photo_id: str, url: str) -> None:
    respx.get(f"{GRAPH_API_BASE}/123").mock(
        return_value=httpx.Response(200, json={"access_token": "page-token"})
    )
    respx.post(f"{GRAPH_API_BASE}/123/photos").mock(
        return_value=httpx.Response(200, json={"id": photo_id})
    )
    respx.get(f"{GRAPH_API_BASE}/{photo_id}").mock(
        return_value=httpx.Response(
            200, json={"images": [{"source": url, "width": 1080, "height": 1080}]}
        )
    )


@respx.mock
async def test_publish_page_photo_from_file_uploads_multipart(settings, local_photo):
    mcp, client = build_server(settings)
    respx.get(f"{GRAPH_API_BASE}/123").mock(
        return_value=httpx.Response(200, json={"access_token": "page-token"})
    )
    route = respx.post(f"{GRAPH_API_BASE}/123/photos").mock(
        return_value=httpx.Response(200, json={"id": "photo-1"})
    )
    result = await mcp.call_tool(
        "publish_page_photo_from_file",
        {"page_id": "123", "file_path": str(local_photo), "caption": "hello"},
    )
    assert b"fake-jpeg-bytes" in route.calls[0].request.content
    assert result.structured_content["id"] == "photo-1"
    await client.aclose()


@respx.mock
async def test_publish_page_photo_from_file_missing_file(settings):
    mcp, client = build_server(settings)
    respx.get(f"{GRAPH_API_BASE}/123").mock(
        return_value=httpx.Response(200, json={"access_token": "page-token"})
    )
    with pytest.raises(ToolError, match="fichier introuvable"):
        await mcp.call_tool(
            "publish_page_photo_from_file",
            {"page_id": "123", "file_path": "/does/not/exist.jpg"},
        )
    await client.aclose()


@respx.mock
async def test_ig_publish_carousel_from_files_reuses_fb_cdn_urls(settings, tmp_path):
    mcp, client = build_server(settings)
    photo_a = tmp_path / "a.jpg"
    photo_a.write_bytes(b"a")
    photo_b = tmp_path / "b.jpg"
    photo_b.write_bytes(b"b")

    respx.get(f"{GRAPH_API_BASE}/123").mock(
        return_value=httpx.Response(200, json={"access_token": "page-token"})
    )
    respx.post(f"{GRAPH_API_BASE}/123/photos").mock(
        side_effect=[
            httpx.Response(200, json={"id": "photo-a"}),
            httpx.Response(200, json={"id": "photo-b"}),
        ]
    )
    respx.get(f"{GRAPH_API_BASE}/photo-a").mock(
        return_value=httpx.Response(
            200, json={"images": [{"source": "https://cdn.fb/a.jpg"}]}
        )
    )
    respx.get(f"{GRAPH_API_BASE}/photo-b").mock(
        return_value=httpx.Response(
            200, json={"images": [{"source": "https://cdn.fb/b.jpg"}]}
        )
    )

    media_route = respx.post(f"{GRAPH_API_BASE}/999/media").mock(
        return_value=httpx.Response(200, json={"id": "container-1", "status_code": "FINISHED"})
    )
    respx.get(f"{GRAPH_API_BASE}/container-1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAPH_API_BASE}/999/media_publish").mock(
        return_value=httpx.Response(200, json={"id": "published-1"})
    )

    result = await mcp.call_tool(
        "ig_publish_carousel_from_files",
        {
            "ig_user_id": "999",
            "page_id": "123",
            "file_paths": [str(photo_a), str(photo_b)],
            "caption": "carousel",
        },
    )
    payload = result.structured_content
    assert payload["uploaded_fb_photos"] == ["photo-a", "photo-b"]
    sent_bodies = [call.request.content.decode() for call in media_route.calls]
    assert any("cdn.fb%2Fa.jpg" in body or "cdn.fb/a.jpg" in body for body in sent_bodies)
    assert any("cdn.fb%2Fb.jpg" in body or "cdn.fb/b.jpg" in body for body in sent_bodies)
    await client.aclose()


@pytest.fixture
def local_video(tmp_path):
    path = tmp_path / "reel.mp4"
    path.write_bytes(b"fake-mp4-bytes")
    return path


@respx.mock
async def test_publish_page_video_from_file_uploads_multipart(settings, local_video):
    mcp, client = build_server(settings)
    respx.get(f"{GRAPH_API_BASE}/123").mock(
        return_value=httpx.Response(200, json={"access_token": "page-token"})
    )
    route = respx.post(f"{GRAPH_API_BASE}/123/videos").mock(
        return_value=httpx.Response(200, json={"id": "video-1"})
    )
    respx.get(f"{GRAPH_API_BASE}/video-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": {"video_status": "ready"},
                "source": "https://cdn.fb/v.mp4",
                "permalink_url": "/lawmaster/videos/1",
            },
        )
    )
    result = await mcp.call_tool(
        "publish_page_video_from_file",
        {"page_id": "123", "file_path": str(local_video), "description": "légende"},
    )
    body = route.calls[0].request.content
    assert b"fake-mp4-bytes" in body
    assert b"published" in body
    assert result.structured_content["id"] == "video-1"
    assert result.structured_content["url"] == "https://cdn.fb/v.mp4"
    await client.aclose()


@respx.mock
async def test_publish_page_video_from_file_missing_file(settings):
    mcp, client = build_server(settings)
    respx.get(f"{GRAPH_API_BASE}/123").mock(
        return_value=httpx.Response(200, json={"access_token": "page-token"})
    )
    with pytest.raises(ToolError, match="fichier introuvable"):
        await mcp.call_tool(
            "publish_page_video_from_file",
            {"page_id": "123", "file_path": "/does/not/exist.mp4"},
        )
    await client.aclose()


@respx.mock
async def test_publish_page_video_from_file_rejects_oversized(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(publishing, "MAX_DIRECT_UPLOAD_BYTES", 4)
    mcp, client = build_server(settings)
    big = tmp_path / "big.mp4"
    big.write_bytes(b"far-too-many-bytes")
    respx.get(f"{GRAPH_API_BASE}/123").mock(
        return_value=httpx.Response(200, json={"access_token": "page-token"})
    )
    with pytest.raises(ToolError, match="trop volumineux"):
        await mcp.call_tool(
            "publish_page_video_from_file",
            {"page_id": "123", "file_path": str(big)},
        )
    await client.aclose()


@respx.mock
async def test_ig_publish_reel_from_file_reuses_fb_source_and_cleans_up(settings, local_video):
    mcp, client = build_server(settings)
    respx.get(f"{GRAPH_API_BASE}/123").mock(
        return_value=httpx.Response(200, json={"access_token": "page-token"})
    )
    respx.post(f"{GRAPH_API_BASE}/123/videos").mock(
        return_value=httpx.Response(200, json={"id": "video-1"})
    )
    respx.get(f"{GRAPH_API_BASE}/video-1").mock(
        return_value=httpx.Response(
            200,
            json={"status": {"video_status": "ready"}, "source": "https://cdn.fb/v.mp4"},
        )
    )
    media_route = respx.post(f"{GRAPH_API_BASE}/999/media").mock(
        return_value=httpx.Response(200, json={"id": "container-1"})
    )
    respx.get(f"{GRAPH_API_BASE}/container-1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAPH_API_BASE}/999/media_publish").mock(
        return_value=httpx.Response(200, json={"id": "reel-1"})
    )
    delete_route = respx.delete(f"{GRAPH_API_BASE}/video-1").mock(
        return_value=httpx.Response(200, json={"success": True})
    )

    result = await mcp.call_tool(
        "ig_publish_reel_from_file",
        {
            "ig_user_id": "999",
            "page_id": "123",
            "file_path": str(local_video),
            "caption": "reel",
        },
    )
    payload = result.structured_content
    assert payload["published"]["id"] == "reel-1"
    assert payload["uploaded_fb_video"] == "video-1"
    assert payload["uploaded_fb_video_deleted"] is True
    assert delete_route.called
    sent = media_route.calls[0].request.content.decode()
    assert "REELS" in sent
    assert "cdn.fb%2Fv.mp4" in sent or "cdn.fb/v.mp4" in sent
    await client.aclose()


@respx.mock
async def test_ig_publish_reel_from_file_keeps_video_when_asked(settings, local_video):
    mcp, client = build_server(settings)
    respx.get(f"{GRAPH_API_BASE}/123").mock(
        return_value=httpx.Response(200, json={"access_token": "page-token"})
    )
    respx.post(f"{GRAPH_API_BASE}/123/videos").mock(
        return_value=httpx.Response(200, json={"id": "video-1"})
    )
    respx.get(f"{GRAPH_API_BASE}/video-1").mock(
        return_value=httpx.Response(
            200,
            json={"status": {"video_status": "ready"}, "source": "https://cdn.fb/v.mp4"},
        )
    )
    respx.post(f"{GRAPH_API_BASE}/999/media").mock(
        return_value=httpx.Response(200, json={"id": "container-1"})
    )
    respx.get(f"{GRAPH_API_BASE}/container-1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAPH_API_BASE}/999/media_publish").mock(
        return_value=httpx.Response(200, json={"id": "reel-1"})
    )
    delete_route = respx.delete(f"{GRAPH_API_BASE}/video-1")

    result = await mcp.call_tool(
        "ig_publish_reel_from_file",
        {
            "ig_user_id": "999",
            "page_id": "123",
            "file_path": str(local_video),
            "keep_fb_video": True,
        },
    )
    assert "uploaded_fb_video_deleted" not in result.structured_content
    assert not delete_route.called
    await client.aclose()


@respx.mock
async def test_publish_page_video_from_file_schedules(settings, local_video):
    mcp, client = build_server(settings)
    respx.get(f"{GRAPH_API_BASE}/123").mock(
        return_value=httpx.Response(200, json={"access_token": "page-token"})
    )
    route = respx.post(f"{GRAPH_API_BASE}/123/videos").mock(
        return_value=httpx.Response(200, json={"id": "video-1"})
    )
    respx.get(f"{GRAPH_API_BASE}/video-1").mock(
        return_value=httpx.Response(200, json={"source": "https://cdn.fb/v.mp4"})
    )
    when = int(time.time()) + 3600
    result = await mcp.call_tool(
        "publish_page_video_from_file",
        {
            "page_id": "123",
            "file_path": str(local_video),
            "description": "légende",
            "scheduled_publish_time": when,
        },
    )
    body = route.calls[0].request.content.decode(errors="ignore")
    assert "scheduled_publish_time" in body
    assert str(when) in body
    # Une vidéo programmée doit partir non publiée.
    assert 'name="published"\r\n\r\nfalse' in body
    assert result.structured_content["scheduled_publish_time"] == when
    await client.aclose()


@respx.mock
async def test_publish_page_video_from_file_rejects_near_schedule(settings, local_video):
    mcp, client = build_server(settings)
    with pytest.raises(ToolError, match="trop proche"):
        await mcp.call_tool(
            "publish_page_video_from_file",
            {
                "page_id": "123",
                "file_path": str(local_video),
                "scheduled_publish_time": int(time.time()) + 60,
            },
        )
    await client.aclose()


@respx.mock
async def test_publish_page_video_from_file_rejects_far_schedule(settings, local_video):
    mcp, client = build_server(settings)
    with pytest.raises(ToolError, match="trop lointaine"):
        await mcp.call_tool(
            "publish_page_video_from_file",
            {
                "page_id": "123",
                "file_path": str(local_video),
                "scheduled_publish_time": int(time.time()) + 400 * 24 * 3600,
            },
        )
    await client.aclose()


@respx.mock
async def test_publish_page_post_with_schedule_is_not_published_immediately(settings):
    """Régression : sans `published=False`, la date était ignorée et le post partait aussitôt."""
    mcp, client = build_server(settings)
    _mock_page_token()
    route = respx.post(f"{GRAPH_API_BASE}/123/feed").mock(
        return_value=httpx.Response(200, json={"id": "post-1"})
    )
    when = int(time.time()) + 3600
    await mcp.call_tool(
        "publish_page_post",
        {"page_id": "123", "message": "plus tard", "scheduled_publish_time": when},
    )
    form = _form(route.calls[0].request)
    assert form["published"] == "false"
    assert form["scheduled_publish_time"] == str(when)
    await client.aclose()


@respx.mock
async def test_publish_page_post_rejects_near_schedule(settings):
    mcp, client = build_server(settings)
    with pytest.raises(ToolError, match="trop proche"):
        await mcp.call_tool(
            "publish_page_post",
            {"page_id": "123", "message": "x", "scheduled_publish_time": int(time.time()) + 60},
        )
    await client.aclose()


@respx.mock
async def test_publish_page_video_published_does_not_wait_for_encoding(
    settings, local_video, monkeypatch
):
    """Régression : une vidéo déjà en ligne attendait l'encodage puis renvoyait une
    erreur au-delà du délai, ce qui poussait à la republier (doublon)."""
    monkeypatch.setattr(publishing, "_POLL_DELAY_SECONDS", 0)
    mcp, client = build_server(settings)
    _mock_page_token()
    respx.post(f"{GRAPH_API_BASE}/123/videos").mock(
        return_value=httpx.Response(200, json={"id": "video-1"})
    )
    details = respx.get(f"{GRAPH_API_BASE}/video-1").mock(
        return_value=httpx.Response(
            200,
            json={"status": {"video_status": "processing"}, "permalink_url": "/v/1"},
        )
    )
    result = await mcp.call_tool(
        "publish_page_video_from_file",
        {"page_id": "123", "file_path": str(local_video)},
    )
    payload = result.structured_content
    assert "error" not in payload
    assert payload["id"] == "video-1"
    assert details.call_count == 1
    await client.aclose()


@respx.mock
async def test_publish_page_video_published_survives_details_failure(settings, local_video):
    """Une fois la vidéo en ligne, un échec de lecture des détails ne doit pas
    se transformer en erreur (sinon : nouvelle tentative et doublon)."""
    mcp, client = build_server(settings)
    _mock_page_token()
    respx.post(f"{GRAPH_API_BASE}/123/videos").mock(
        return_value=httpx.Response(200, json={"id": "video-1"})
    )
    respx.get(f"{GRAPH_API_BASE}/video-1").mock(
        return_value=httpx.Response(
            500, json={"error": {"message": "boom", "type": "OAuthException"}}
        )
    )
    result = await mcp.call_tool(
        "publish_page_video_from_file",
        {"page_id": "123", "file_path": str(local_video)},
    )
    payload = result.structured_content
    assert payload is not None, result
    assert "error" not in payload
    assert payload["id"] == "video-1"
    await client.aclose()


@respx.mock
async def test_ig_reel_from_file_keeps_source_while_instagram_is_processing(
    settings, local_video, monkeypatch
):
    """Régression : au-delà du délai d'attente, la vidéo source était supprimée alors
    qu'Instagram pouvait encore l'ingérer — le conteneur ne pouvait plus aboutir."""
    monkeypatch.setattr(publishing, "_POLL_DELAY_SECONDS", 0)
    mcp, client = build_server(settings)
    _mock_reel_upload()
    _mock_container_status("IN_PROGRESS")
    publish_route = respx.post(f"{GRAPH_API_BASE}/999/media_publish")
    delete_route = respx.delete(f"{GRAPH_API_BASE}/video-1")

    with pytest.raises(ToolError, match="ig_publish_container") as exc_info:
        await mcp.call_tool(
            "ig_publish_reel_from_file",
            {"ig_user_id": "999", "page_id": "123", "file_path": str(local_video)},
        )
    message = str(exc_info.value)
    assert "container-1" in message
    assert "video-1" in message  # l'id à supprimer plus tard est donné
    assert not delete_route.called
    assert not publish_route.called
    await client.aclose()


@pytest.mark.parametrize(("keep_fb_video", "deleted"), [(False, True), (True, False)])
@respx.mock
async def test_ig_reel_from_file_cleans_up_when_instagram_rejects_video(
    settings, local_video, keep_fb_video, deleted
):
    """Conteneur en ERROR : Instagram n'a plus besoin de la source, on la supprime
    (sauf `keep_fb_video`)."""
    mcp, client = build_server(settings)
    _mock_reel_upload()
    _mock_container_status("ERROR")
    delete_route = respx.delete(f"{GRAPH_API_BASE}/video-1").mock(
        return_value=httpx.Response(200, json={"success": True})
    )

    with pytest.raises(ToolError, match="statut ERROR"):
        await mcp.call_tool(
            "ig_publish_reel_from_file",
            {
                "ig_user_id": "999",
                "page_id": "123",
                "file_path": str(local_video),
                "keep_fb_video": keep_fb_video,
            },
        )
    assert delete_route.called is deleted
    await client.aclose()


@respx.mock
async def test_ig_reel_from_file_cleans_up_when_publish_raises(settings, local_video):
    """Régression : une exception pendant la publication sautait le nettoyage et
    laissait la vidéo intermédiaire sur la Page."""
    mcp, client = build_server(settings)
    _mock_reel_upload()
    _mock_container_status("FINISHED")
    respx.post(f"{GRAPH_API_BASE}/999/media_publish").mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "quota atteint", "type": "OAuthException"}}
        )
    )
    delete_route = respx.delete(f"{GRAPH_API_BASE}/video-1").mock(
        return_value=httpx.Response(200, json={"success": True})
    )

    with pytest.raises(ToolError, match="quota atteint"):
        await mcp.call_tool(
            "ig_publish_reel_from_file",
            {"ig_user_id": "999", "page_id": "123", "file_path": str(local_video)},
        )
    assert delete_route.called
    await client.aclose()


@respx.mock
async def test_ig_publish_container_publishes_finished_container(settings):
    mcp, client = build_server(settings)
    _mock_container_status("FINISHED")
    route = respx.post(f"{GRAPH_API_BASE}/999/media_publish").mock(
        return_value=httpx.Response(200, json={"id": "reel-1"})
    )
    result = await mcp.call_tool(
        "ig_publish_container", {"ig_user_id": "999", "container_id": "container-1"}
    )
    assert result.structured_content["published"]["id"] == "reel-1"
    assert _form(route.calls[0].request)["creation_id"] == "container-1"
    await client.aclose()


@respx.mock
async def test_ig_publish_container_refuses_unfinished_container(settings):
    mcp, client = build_server(settings)
    _mock_container_status("IN_PROGRESS")
    publish_route = respx.post(f"{GRAPH_API_BASE}/999/media_publish")
    with pytest.raises(ToolError, match="IN_PROGRESS"):
        await mcp.call_tool(
            "ig_publish_container", {"ig_user_id": "999", "container_id": "container-1"}
        )
    assert not publish_route.called
    await client.aclose()


async def test_ig_publish_container_requires_writes():
    mcp, client = build_server(Settings(access_token="fake-token", enable_writes=False))
    with pytest.raises(ToolError, match="META_ENABLE_WRITES"):
        await mcp.call_tool(
            "ig_publish_container", {"ig_user_id": "999", "container_id": "container-1"}
        )
    await client.aclose()


@respx.mock
async def test_publish_page_carousel_attaches_every_image(settings):
    mcp, client = build_server(settings)
    _mock_page_token()
    respx.post(f"{GRAPH_API_BASE}/123/photos").mock(
        side_effect=[
            httpx.Response(200, json={"id": "photo-a"}),
            httpx.Response(200, json={"id": "photo-b"}),
        ]
    )
    feed_route = respx.post(f"{GRAPH_API_BASE}/123/feed").mock(
        return_value=httpx.Response(200, json={"id": "post-1"})
    )
    result = await mcp.call_tool(
        "publish_page_carousel",
        {"page_id": "123", "image_urls": ["https://x/a.jpg", "https://x/b.jpg"], "message": "hi"},
    )
    form = _form(feed_route.calls[0].request)
    assert form["attached_media[0]"] == '{"media_fbid":"photo-a"}'
    assert form["attached_media[1]"] == '{"media_fbid":"photo-b"}'
    assert result.structured_content["uploaded_media"] == ["photo-a", "photo-b"]
    await client.aclose()


@respx.mock
async def test_publish_page_carousel_fails_instead_of_dropping_an_image(settings):
    """Régression : une image sans id était ignorée et le carrousel partait incomplet."""
    mcp, client = build_server(settings)
    _mock_page_token()
    respx.post(f"{GRAPH_API_BASE}/123/photos").mock(
        side_effect=[
            httpx.Response(200, json={"id": "photo-a"}),
            httpx.Response(200, json={}),
        ]
    )
    feed_route = respx.post(f"{GRAPH_API_BASE}/123/feed")
    with pytest.raises(ToolError, match="x/b"):
        await mcp.call_tool(
            "publish_page_carousel",
            {
                "page_id": "123",
                "image_urls": ["https://x/a.jpg", "https://x/b.jpg"],
                "message": "hi",
            },
        )
    assert not feed_route.called
    await client.aclose()
