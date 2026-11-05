"""Creative rendering for A2Z: real-photo branded images and short animated photo reels.

Approved shop photographs only. Persist originals/renders in PostgreSQL so Render restarts
do not erase creative assets. This module NEVER publishes to social networks.
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from app.auth import require_auth
from app.db import Base, session_scope

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])
Image.MAX_IMAGE_PIXELS = 24_000_000
MAX_UPLOAD = 8 * 1024 * 1024
MAX_RENDER = 12 * 1024 * 1024
FORMATS = {"square": (1080, 1080), "portrait": (1080, 1350), "story": (1080, 1920)}
VALID_TAG = re.compile(r"[^a-z0-9 ]+")
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
LOGO = Path(__file__).parent / "static" / "a2z-aquariums-logo.png"


class MarketingMedia(Base):
    __tablename__ = "marketing_media"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(180))
    tags: Mapped[str] = mapped_column(String(500), default="")
    mime: Mapped[str] = mapped_column(String(80))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    approved: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MarketingCreative(Base):
    __tablename__ = "marketing_creatives"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[int] = mapped_column(Integer, ForeignKey("marketing_drafts.id"), index=True)
    media_id: Mapped[int] = mapped_column(Integer, ForeignKey("marketing_media.id"))
    format: Mapped[str] = mapped_column(String(30))
    mime: Mapped[str] = mapped_column(String(50))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def safe_text(s: str) -> str:
    return " ".join(str(s).replace("\n", " ").split()).strip()


def _font(size: int, bold: bool = True):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:
        return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, limit=3):
    tokens, lines, line = safe_text(text).split(), [], ""
    for token in tokens:
        candidate = (line + " " + token).strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] > max_width and line:
            lines.append(line)
            line = token
        else:
            line = candidate
        if len(lines) >= limit:
            break
    if line and len(lines) < limit:
        lines.append(line)
    return lines


def _headline(draft) -> str:
    # Draft title is owner-visible, rather than trusting long unstructured creative briefs.
    return safe_text(draft.title).upper()[:100]


def render_photo(photo_bytes: bytes, headline: str, format_name: str) -> bytes:
    if format_name not in FORMATS:
        raise ValueError("Unsupported format")
    w, h = FORMATS[format_name]
    with Image.open(io.BytesIO(photo_bytes)) as raw:
        photo = ImageOps.exif_transpose(raw).convert("RGB")
        bg = ImageOps.fit(photo, (w, h), method=Image.Resampling.LANCZOS, centering=(0.5, 0.48))
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    # Dark gradient preserves photo detail at the top, and guarantees legible footer.
    for y in range(h):
        alpha = int(205 * max(0, (y / h - 0.48) / 0.52) ** 1.4)
        if alpha:
            ld.line((0, y, w, y), fill=(5, 15, 25, alpha))
    photo = Image.alpha_composite(bg.convert("RGBA"), layer)
    d = ImageDraw.Draw(photo)
    pad = 72
    title_size = 66 if format_name != "story" else 74
    font = _font(title_size)
    lines = _wrap(d, headline, font, w - 2 * pad, limit=3)
    base_y = h - (230 if format_name == "square" else 270 if format_name == "portrait" else 320)
    for i, line in enumerate(lines):
        d.text((pad, base_y + i * (title_size + 12)), line, font=font, fill=(255, 255, 255, 255),
               stroke_width=1, stroke_fill=(7, 23, 34, 180))
    d.rounded_rectangle((pad, h - 104, w - pad, h - 96), radius=4, fill=(48, 197, 207, 255))
    # Preserve actual A2Z logo exactly as supplied; no redrawing or modifying it.
    if LOGO.exists():
        with Image.open(LOGO) as orig_logo:
            mark = ImageOps.exif_transpose(orig_logo).convert("RGBA")
            mark.thumbnail((140, 140), Image.Resampling.LANCZOS)
            photo.alpha_composite(mark, (w - pad - mark.width, 35))
    else:
        d.text((pad, 48), "A2Z AQUARIUMS", font=_font(34), fill="white")
    out = io.BytesIO()
    photo.convert("RGB").save(out, format="JPEG", quality=86, optimize=True, subsampling=0)
    return out.getvalue()


def render_reel(image_bytes: bytes) -> bytes:
    """Encode a six-second vertical MP4 from a finished story graphic.

    This is an animated photo post, not synthesized footage of living animals.
    """
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
    except ImportError as exc:
        raise RuntimeError("Video encoder not installed") from exc
    with tempfile.TemporaryDirectory(prefix="a2z-creative-") as folder:
        src, target = Path(folder) / "frame.jpg", Path(folder) / "reel.mp4"
        with Image.open(io.BytesIO(image_bytes)) as original:
            original.convert("RGB").resize((720, 1280), Image.Resampling.LANCZOS).save(src, "JPEG", quality=88)
        cmd = [
            get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
            "-loop", "1", "-framerate", "25", "-i", str(src),
            "-t", "6", "-vf", "scale=720:1280,format=yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27",
            "-r", "25", "-movflags", "+faststart", str(target),
        ]
        try:
            subprocess.run(cmd, check=True, timeout=70, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            raise RuntimeError("Video encoding failed") from exc
        if not target.exists() or target.stat().st_size > MAX_RENDER:
            raise RuntimeError("Video output invalid or too large")
        return target.read_bytes()


def media_details(m: MarketingMedia):
    return {"id": m.id, "label": m.label, "tags": m.tags,
            "approved": m.approved, "url": f"/marketing/api/media/{m.id}/file"}


def creative_details(c: MarketingCreative):
    return {"id": c.id, "draft_id": c.draft_id, "media_id": c.media_id,
            "format": c.format, "mime": c.mime,
            "url": f"/marketing/api/creatives/{c.id}/file"}


@router.get("/media")
async def list_media():
    async with session_scope() as session:
        rows = (await session.execute(
            select(MarketingMedia).order_by(MarketingMedia.id.desc()).limit(100)
        )).scalars().all()
        return {"data": [media_details(x) for x in rows]}


@router.post("/media")
async def upload_media(file: UploadFile = File(...), label: str = "", tags: str = ""):
    if file.content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(415, "Upload a JPEG, PNG or WebP photograph")
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Photo exceeds 8 MB")
    try:
        with Image.open(io.BytesIO(data)) as raw:
            raw.verify()
        with Image.open(io.BytesIO(data)) as raw:
            frame = ImageOps.exif_transpose(raw)
            if frame.width < 500 or frame.height < 500:
                raise HTTPException(422, "Photo must be at least 500 pixels in each dimension")
            # Normalize to a safe JPEG: strips metadata, eliminates unexpected encodings.
            normalized = io.BytesIO()
            frame.convert("RGB").save(normalized, "JPEG", quality=90, optimize=True)
            data = normalized.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, "Invalid image") from exc
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Normalized image exceeds 8 MB")
    label = safe_text(label or file.filename or "Store photo")[:180]
    tags = safe_text(tags).lower()[:500]
    async with session_scope() as session:
        m = MarketingMedia(label=label, tags=tags, mime="image/jpeg", data=data)
        session.add(m)
        await session.flush()
        return {"data": media_details(m)}


@router.get("/media/{media_id}/file")
async def media_file(media_id: int):
    async with session_scope() as session:
        m = await session.get(MarketingMedia, media_id)
        if m is None:
            raise HTTPException(404, "Photo not found")
        return Response(m.data, media_type=m.mime, headers={"Cache-Control": "private, no-store",
                        "X-Content-Type-Options": "nosniff"})


def match_media(draft, available: list[MarketingMedia]) -> MarketingMedia | None:
    if not available:
        return None
    words = set(VALID_TAG.sub(" ", (draft.title + " " + draft.category).lower()).split())
    scored = []
    for m in available:
        terms = set(VALID_TAG.sub(" ", (m.label + " " + m.tags).lower()).split())
        overlap = len(words & terms - {"the", "fish", "aquarium", "a2z", "and"})
        scored.append((overlap, -m.id, m))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    # Never blindly place unrelated animal photos under a species-specific claim.
    return scored[0][2] if scored[0][0] > 0 else None


async def _generate_creative(draft_id: int, media_id: int | None, fmt: str):
    from app.marketing import MarketingDraft
    if fmt not in (*FORMATS, "reel"):
        raise HTTPException(422, "Unsupported output format")
    async with session_scope() as session:
        draft = await session.get(MarketingDraft, draft_id)
        if draft is None:
            raise HTTPException(404, "Draft not found")
        if draft.status != "pending":
            raise HTTPException(409, "Only pending posts can have new creative")
        if media_id is not None:
            media = await session.get(MarketingMedia, media_id)
            if media is None or not media.approved:
                raise HTTPException(404, "Approved media not found")
        else:
            available = (await session.execute(select(MarketingMedia)
                         .where(MarketingMedia.approved.is_(True)))).scalars().all()
            media = match_media(draft, available)
            if media is None:
                raise HTTPException(409, "No suitable approved store photograph matches this post. Upload and tag a photo.")
        title, source, selected_id = _headline(draft), media.data, media.id

    try:
        jpg = render_photo(source, title, "story" if fmt == "reel" else fmt)
        content = render_reel(jpg) if fmt == "reel" else jpg
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    if len(content) > MAX_RENDER:
        raise HTTPException(413, "Creative asset too large")
    async with session_scope() as session:
        draft = await session.get(MarketingDraft, draft_id)
        if draft is None or draft.status != "pending":
            raise HTTPException(409, "Draft no longer pending")
        row = MarketingCreative(draft_id=draft_id, media_id=selected_id,
                                format=fmt, mime="video/mp4" if fmt == "reel" else "image/jpeg",
                                data=content)
        session.add(row)
        await session.flush()
        return {"data": creative_details(row)}


@router.post("/drafts/{draft_id}/render")
async def create_graphic(draft_id: int, format: str = "portrait", media_id: int | None = None):
    return await _generate_creative(draft_id, media_id, format)


@router.get("/drafts/{draft_id}/creatives")
async def list_creatives(draft_id: int):
    async with session_scope() as session:
        rows = (await session.execute(
            select(MarketingCreative).where(MarketingCreative.draft_id == draft_id)
            .order_by(MarketingCreative.id.desc()).limit(30)
        )).scalars().all()
        return {"data": [creative_details(x) for x in rows]}


@router.get("/creatives/{creative_id}/file")
async def creative_file(creative_id: int):
    async with session_scope() as session:
        c = await session.get(MarketingCreative, creative_id)
        if c is None:
            raise HTTPException(404, "Creative not found")
        ext = "mp4" if c.mime == "video/mp4" else "jpg"
        return Response(c.data, media_type=c.mime,
                        headers={"Cache-Control": "private, no-store",
                                 "Content-Disposition": f'inline; filename="a2z-creative-{c.id}.{ext}"',
                                 "X-Content-Type-Options": "nosniff"})
