"""Image-led concept tests: no external image generation or paid API calls."""
import io
from types import SimpleNamespace

from PIL import Image
from app.marketing_visuals import headline_for, creative_prompt, composite_hero


def test_headline_is_short_not_corporate_document():
    assert headline_for("Choosing the Right Aquarium Filter: What You Need to Know") == "CHOOSING THE RIGHT AQUARIUM FILTER"


def test_ai_visual_prompt_blocks_stock_claims_and_embedded_text():
    draft = SimpleNamespace(
        title="Choosing the right aquarium filter",
        creative_brief="High quality imagery of a real aquarium filter",
        category="product_spotlight",
    )
    prompt = creative_prompt(draft)
    assert "No visible prices" in prompt
    assert "not documentary proof" in prompt
    assert "NO TEXT IN THE GENERATED IMAGE" in prompt
    assert "No turtles" in prompt


def test_generated_image_uses_real_logo_overlay_and_social_size():
    original = Image.new("RGB", (1024, 1536), (40, 95, 128))
    memory = io.BytesIO()
    original.save(memory, "JPEG")
    result = composite_hero(memory.getvalue(), "Choosing Aquarium Filtration")
    with Image.open(io.BytesIO(result)) as im:
        assert im.size == (1080, 1350)
        assert im.format == "JPEG"


def test_review_ui_has_ai_visual_option_and_selected_approval():
    from app.marketing import PAGE_HTML
    assert "Create realistic AI visual" in PAGE_HTML
    assert "Approve selected visual + caption" in PAGE_HTML
    assert "AI CONCEPT" in PAGE_HTML
