"""Optional live tests against a real Jellyfin server.

These are skipped unless JELLYFIN_URL, JELLYFIN_USERNAME and JELLYFIN_PASSWORD
are set. They only read data and fetch a small byte range; they never report
playback or change library state.

Example:
    JELLYFIN_URL=http://europa.local:8096/jellyfin \
    JELLYFIN_USERNAME=jellyfin JELLYFIN_PASSWORD=jellyfin \
    uv run pytest tests/test_live.py -v
"""

from __future__ import annotations

import os

import pytest
import requests

from jellytui.api import JellyfinClient

URL = os.environ.get("JELLYFIN_URL", "")
USERNAME = os.environ.get("JELLYFIN_USERNAME", "")
PASSWORD = os.environ.get("JELLYFIN_PASSWORD", "")

pytestmark = pytest.mark.skipif(
    not (URL and USERNAME and PASSWORD),
    reason="set JELLYFIN_URL, JELLYFIN_USERNAME and JELLYFIN_PASSWORD to run live tests",
)


@pytest.fixture(scope="module")
def live_client() -> JellyfinClient:
    client = JellyfinClient(URL, device_id="jellytui-live-test", timeout=(10, 60))
    client.login(USERNAME, PASSWORD)
    return client


def test_views_and_libraries(live_client: JellyfinClient):
    views = live_client.get_views()
    assert views
    assert all(view.id and view.name for view in views)


def test_resume_and_item_metadata(live_client: JellyfinClient):
    resume = live_client.get_resume(limit=5)
    assert isinstance(resume, list)
    if resume:
        item = resume[0]
        assert item.id and item.name
        assert item.type


def test_direct_play_byte_range(live_client: JellyfinClient):
    movies = next(
        (view for view in live_client.get_views() if view.collection_type == "movies"),
        None,
    )
    if movies is None:
        pytest.skip("no movies library")
    items = live_client.get_items(
        parent_id=movies.id, include_types="Movie", limit=20
    )
    item = next(
        (
            candidate
            for candidate in items
            if candidate.media_source and candidate.media_source.supports_direct_play
        ),
        None,
    )
    if item is None:
        pytest.skip("no directly playable movie")
    source = item.media_source
    assert source is not None
    url = live_client.stream_url(item, source, play_session_id="jellytui-live-test")
    response = requests.get(
        url,
        headers={"Range": "bytes=0-65535"},
        stream=True,
        timeout=30,
    )
    try:
        assert response.status_code in (200, 206)
        chunk = next(response.iter_content(65536), b"")
        assert chunk
    finally:
        response.close()


def test_artwork_download(live_client: JellyfinClient):
    resume = live_client.get_resume(limit=10)
    item = next((candidate for candidate in resume if candidate.image_tag), None)
    if item is None:
        pytest.skip("no item with artwork")
    artwork = live_client.fetch_image(item.id, tag=item.image_tag, max_height=200)
    assert artwork is not None
    data, content_type = artwork
    assert data[:4] in {b"\x89PNG", b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1"}
    assert content_type.startswith("image/")
