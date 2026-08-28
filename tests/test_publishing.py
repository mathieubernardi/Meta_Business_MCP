"""Tests des outils de publication depuis des fichiers locaux."""

from __future__ import annotations

import httpx
import pytest
import respx

from meta_mcp.config import Settings
from meta_mcp.constants import GRAPH_API_BASE
from meta_mcp.server import build_server


@pytest.fixture
def settings() -> Settings:
    return Settings(access_token="fake-token", enable_writes=True)


@pytest.fixture
def local_photo(tmp_path):
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"fake-jpeg-bytes")
    return path


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
    result = await mcp.call_tool(
        "publish_page_photo_from_file",
        {"page_id": "123", "file_path": "/does/not/exist.jpg"},
    )
    assert "error" in result.structured_content
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
    result = await mcp.call_tool(
        "publish_page_video_from_file",
        {"page_id": "123", "file_path": "/does/not/exist.mp4"},
    )
    assert "error" in result.structured_content
    await client.aclose()


@respx.mock
async def test_publish_page_video_from_file_rejects_oversized(settings, tmp_path, monkeypatch):
    from meta_mcp.tools import publishing

    monkeypatch.setattr(publishing, "MAX_DIRECT_UPLOAD_BYTES", 4)
    mcp, client = build_server(settings)
    big = tmp_path / "big.mp4"
    big.write_bytes(b"far-too-many-bytes")
    respx.get(f"{GRAPH_API_BASE}/123").mock(
        return_value=httpx.Response(200, json={"access_token": "page-token"})
    )
    result = await mcp.call_tool(
        "publish_page_video_from_file",
        {"page_id": "123", "file_path": str(big)},
    )
    assert "trop volumineux" in result.structured_content["error"]
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
    import time

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
    import time

    mcp, client = build_server(settings)
    result = await mcp.call_tool(
        "publish_page_video_from_file",
        {
            "page_id": "123",
            "file_path": str(local_video),
            "scheduled_publish_time": int(time.time()) + 60,
        },
    )
    assert "trop proche" in result.structured_content["error"]
    await client.aclose()


@respx.mock
async def test_publish_page_video_from_file_rejects_far_schedule(settings, local_video):
    import time

    mcp, client = build_server(settings)
    result = await mcp.call_tool(
        "publish_page_video_from_file",
        {
            "page_id": "123",
            "file_path": str(local_video),
            "scheduled_publish_time": int(time.time()) + 400 * 24 * 3600,
        },
    )
    assert "trop lointaine" in result.structured_content["error"]
    await client.aclose()
