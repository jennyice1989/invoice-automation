"""Creative renderer tests (no external API, no database required)."""
import io

from PIL import Image

from app.marketing import MarketingDraft
from app.marketing_creative import (
    content_fingerprint,
    match_media,
    MarketingMedia,
    render_photo,
    FORMATS,
)


def fixture_photo():
    im = Image.new("RGB", (1500, 1500), (25, 90, 130))
    blob = io.BytesIO()
    im.save(blob, "JPEG")
    return blob.getvalue()


def test_render_all_social_aspect_ratios():
    for fmt, dimensions in FORMATS.items():
        img = render_photo(fixture_photo(), "Aquarium maintenance basics", fmt)
        assert img.startswith(bytes([255, 216]))
        with Image.open(io.BytesIO(img)) as result:
            assert result.size == dimensions


def test_render_uses_actual_photo_not_synthesized_fish():
    payload = render_photo(fixture_photo(), "Simple water test tips", "square")
    with Image.open(io.BytesIO(payload)) as result:
        # The original photographic color remains prominent in the image.
        assert result.getpixel((200, 200))[2] >= 70


def test_approval_fingerprint_invalidated_on_caption_change():
    d = MarketingDraft(title="Water Care", category="education",
                       caption="Learn about filtration",
                       creative_brief="Use aquarium display")
    old = content_fingerprint(d)
    d.caption = "Learn about water testing"
    assert old != content_fingerprint(d)


def test_media_match_does_not_choose_unrelated_animal():
    d = MarketingDraft(title="Live plants guide", category="education",
                       caption="Plants are wonderful", creative_brief="Plant photo")
    unrelated = MarketingMedia(id=1, label="Crested gecko", tags="reptiles lizards",
                               mime="image/jpeg", data=fixture_photo())
    assert match_media(d, [unrelated]) is None


def test_media_match_prefers_relevant_tagged_photo():
    d = MarketingDraft(title="Coral health", category="education",
                       caption="Coral care basics", creative_brief="Reef aquarium")
    reef = MarketingMedia(id=2, label="Reef coral display", tags="coral saltwater",
                          mime="image/jpeg", data=fixture_photo())
    unrelated = MarketingMedia(id=3, label="Guppy", tags="freshwater fish",
                               mime="image/jpeg", data=fixture_photo())
    assert match_media(d, [unrelated, reef]).id == 2
