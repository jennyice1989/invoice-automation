"""Image-led, AI-generated illustrative marketing scenes for A2Z.

Generated art is never represented as real store inventory or as an actual
product photograph. The store's authentic logo is composited after generation.
This module makes reviewable drafts only; nothing is published externally.
"""
from __future__ import annotations

import base64
import io
import os
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException
from PIL import Image, ImageDraw, ImageFont, ImageOps
from sqlalchemy import select

from app.auth import require_auth
from app.db import session_scope
from app.marketing import MarketingDraft
from app.marketing_creative import MarketingCreative, content_fingerprint, LOGO

router = APIRouter(prefix="/marketing/api", dependencies=[Depends(require_auth)])
MODEL = "gpt-image-2.5-flare"
MAX_IMAGE_BYTES = 12 * 1024 * 1024
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def headline_for(title: str) -> str:
    """Short social headline, not a long-document-style title."""
    title = re.sub(r"\s+", " ", title).strip()
    title = re.split(r"[:—–]", title, maxsplit=1)[0]
    words = title.split()
    return " ".join(words[:6]).upper()


def overlay_title(draft: MarketingDraft) -> str:
    """Prefer the creative brief's short proposed onscreen hook."""
    brief = draft.creative_brief or ""
    hit = re.search(r"(?:text overlay|overlay headline|on-image headline|headline)\\s*:\\s*['\\\"]([^'\\\"]{5,80})['\\\"]", brief, re.IGNORECASE)
    if hit:
        return hit.group(1).strip()
    return draft.title


def creative_prompt(draft: MarketingDraft) -> str:
    # Do not inject generic "sales floor" claims, exact products, labels, or
    # invented livestock. This image is conceptual editorial art.
    subject = re.sub(r"\s+", " ", draft.title).strip()[:180]
    direction = re.sub(r"\s+", " ", draft.creative_brief).strip()[:550]
    category = (draft.category or "education").lower()
    genre = {
        "education": "premium aquarium hobby educational editorial photograph",
        "community": "welcoming local aquarium hobby lifestyle editorial photograph",
        "product_spotlight": "premium aquarium equipment and aquarium lifestyle editorial photograph",
        "engagement": "attention-grabbing close-up aquarium hobby photograph",
    }.get(category, "premium aquarium hobby editorial photograph")
    return (
        f"Create one striking realistic editorial visual for a local aquarium store's social post. "
        f"Topic: {subject}. Visual direction: {direction}. Genre: {genre}. "
        "A real-photography aesthetic: rich authentic water texture, natural refraction, "
        "beautiful crisp focus, sophisticated lighting, compelling subject, "
        "believable fish anatomy if any fish are depicted. No cartoon, vector art, "
        "futuristic aquarium, surreal bioluminescence, fantasy organisms, "
        "fake-looking plastic plants or impossible aquatic husbandry. "
        "No visible prices, barcodes, labels, brand logos, watermarks, store signage "
        "or people impersonating real employees. No turtles. "
        "Do not invent a depiction of a specific stocked animal, sale, or real store location. "
        "Compose vertically for Instagram: strong visual subject in upper 70 percent, "
        "bottom 28 percent darker and uncluttered for a short headline overlay. "
        "NO TEXT IN THE GENERATED IMAGE. No split-panel, no marketing template, no typography. "
        "This is illustrative concept art, not documentary proof of in-store stock."
    )


def _font(size: int):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default()


def _title_lines(draw, title, max_width):
    words = headline_for(title).split()
    for sz in range(82, 39, -3):
        f = _font(sz)
        lines = []
        line = ""
        for word in words:
            candidate = (line + " " + word).strip()
            if draw.textbbox((0, 0), candidate, font=f)[2] < max_width:
                line = candidate
            else:
                if line:
                    lines.append(line)
                line = word
        if line:
            lines.append(line)
        if len(lines) <= 3 and all(draw.textbbox((0, 0), t, font=f)[2] < max_width for t in lines):
            return lines, f, sz + 11
    f = _font(40)
    return [" ".join(words[:4])], f, 50


def composite_hero(image_data: bytes, title: str) -> bytes:
    """Image-led design: natural photo dominates, only one short title and small logo."""
    with Image.open(io.BytesIO(image_data)) as raw:
        raw = ImageOps.exif_transpose(raw)
        bg = ImageOps.fit(raw.convert("RGB"), (1080, 1350), method=Image.Resampling.LANCZOS)
    image = bg.convert("RGBA")
    tint = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shade = ImageDraw.Draw(tint)
    for y in range(760, 1350):
        opacity = int(230 * ((y - 760) / 590) ** 1.25)
        shade.line((0, y, 1080, y), fill=(0, 13, 24, opacity))
    image = Image.alpha_composite(image, tint)
    draw = ImageDraw.Draw(image)
    lines, f, spacing = _title_lines(draw, title, 950)
    start_y = 1180 - spacing * len(lines)
    for i, line in enumerate(lines):
        draw.text((65, start_y + i * spacing), line, font=f, fill=(255, 255, 255))
    draw.line((65, 1258, 315, 1258), fill=(76, 217, 222), width=7)
    if LOGO.exists():
        with Image.open(LOGO) as logo_source:
            logo = logo_source.convert("RGBA")
            logo.thumbnail((134, 134), Image.Resampling.LANCZOS)
            image.alpha_composite(logo, (1080 - 55 - logo.width, 35))
    # Clear and explicit distinction from in-store documentary photography.
    draw.text((65, 1302), "AI CONCEPT IMAGE  •  NOT STORE INVENTORY", font=_font(17), fill=(210, 225, 230))
    result = io.BytesIO()
    image.convert("RGB").save(result, "JPEG", quality=88, optimize=True)
    return result.getvalue()


async def generate_visual(draft_id: int):
    """Create a billed AI illustrative concept for a pending draft."""
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise HTTPException(503, "OPENAI_API_KEY missing")
    async with session_scope() as session:
        draft = await session.get(MarketingDraft, draft_id)
        if draft is None:
            raise HTTPException(404, "Draft not found")
        if draft.status != "pending":
            raise HTTPException(409, "Only pending drafts can be generated")
        # Keep runaway image costs contained if a user repeatedly taps generate.
        count = len((await session.execute(
            select(MarketingCreative.id).where(
                MarketingCreative.draft_id == draft_id,
                MarketingCreative.format == "ai-portrait"
            )
        )).scalars().all())
        if count >= 5:
            raise HTTPException(429, "Five AI concepts already exist for this post")
        prompt = creative_prompt(draft)
        title = overlay_title(draft)
        fingerprint = content_fingerprint(draft)
    async with httpx.AsyncClient(timeout=180) as client:
        try:
            response = await client.post(
                "https://api.openai.com/v1/images/generations",
                headers={"Authorization": "Bearer " + key},
                json={
                    "model": os.getenv("MARKETING_IMAGE_MODEL", MODEL),
                    "prompt": prompt,
                    "size": "1024x1536",
                    "quality": "medium",
                    "output_format": "jpeg",
                    "output_compression": 83,
                    "n": 1,
                },
            )
            response.raise_for_status()
            data = base64.b64decode(response.json()["data"][0]["b64_json"], validate=True)
        except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
            raise HTTPException(502, "AI visual generation failed. Check image API access and billing.") from exc
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(502, "Generated image was empty or too large")
    try:
        rendered = composite_hero(data, title)
    except (OSError, ValueError) as exc:
        raise HTTPException(502, "Generated image cannot be rendered") from exc
    async with session_scope() as session:
        draft = await session.get(MarketingDraft, draft_id)
        if draft is None or draft.status != "pending":
            raise HTTPException(409, "Draft changed while visual was generating")
        if content_fingerprint(draft) != fingerprint:
            raise HTTPException(409, "Draft text changed; retry the image")
        creative = MarketingCreative(
            draft_id=draft_id, media_id=None, format="ai-portrait",
            mime="image/jpeg", content_fingerprint=fingerprint, data=rendered
        )
        session.add(creative)
        await session.flush()
        return {"data": {
            "id": creative.id, "url": f"/marketing/api/creatives/{creative.id}/file",
            "format": creative.format, "ai_generated": True,
            "review_required": True, "published": False
        }}


@router.post("/drafts/{draft_id}/ai-visual")
async def generate_visual_endpoint(draft_id: int):
    return await generate_visual(draft_id)
