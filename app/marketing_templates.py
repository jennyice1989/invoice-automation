"""Branded photographic-free templates for A2Z social posts.

These graphics deliberately depict no animals or products; they only contain
abstract aquarium-inspired shapes, category labels, and the unaltered A2Z logo.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

SIZES = {"square": (1080, 1080), "portrait": (1080, 1350), "story": (1080, 1920)}
LOGO = Path(__file__).parent / "static" / "a2z-aquariums-logo.png"
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")


def _font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:
        return ImageFont.load_default()


def _wrap(draw, text, font, width, max_lines=5):
    words = " ".join(str(text).split()).split()
    lines = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= width:
            current = candidate
        elif current:
            lines.append(current)
            current = word
        else:
            lines.append(word)
        if len(lines) >= max_lines:
            return lines[:max_lines]
    if current:
        lines.append(current)
    return lines[:max_lines]


def render_template(headline: str, category: str, fmt: str = "portrait") -> bytes:
    if fmt not in SIZES:
        raise ValueError("Unsupported social graphic format")
    w, h = SIZES[fmt]
    # All artistic elements are abstract. No invented fish, products, or displays.
    img = Image.new("RGB", (w, h))
    pixels = img.load()
    for y in range(h):
        t = y / h
        top = (8, 28, 45)
        bottom = (3, 75, 89)
        color = tuple(int(a * (1 - t) + b * t) for a, b in zip(top, bottom))
        for x in range(w):
            pixels[x, y] = color
    art = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(art)
    # Large curves and rings add depth without suggesting real aquarium livestock.
    d.ellipse((-w // 2, -h // 5, w + w // 5, int(h * .55)),
              outline=(64, 202, 208, 38), width=7)
    d.ellipse((int(w * .25), int(h * .05), int(w * 1.38), int(h * .82)),
              outline=(56, 188, 199, 45), width=12)
    for i in range(13):
        x = int(90 + (i * 191) % (w - 100))
        y = int(h * .12 + (i * 157) % int(h * .70))
        radius = 7 + (i % 4) * 12
        d.ellipse((x-radius, y-radius, x+radius, y+radius),
                  outline=(153, 228, 234, 42 + 8 * (i % 3)), width=3)
    d.polygon([(0, int(h*.83)), (w, int(h*.69)), (w,h), (0,h)],
              fill=(0, 20, 34, 120))
    img = Image.alpha_composite(img.convert("RGBA"), art)
    draw = ImageDraw.Draw(img)
    margin = 75
    label = {"education":"AQUARIUM EDUCATION", "product_spotlight":"PRODUCT SPOTLIGHT",
             "community":"A2Z COMMUNITY", "engagement":"LET'S TALK AQUARIUMS"}.get(category, "A2Z AQUARIUMS")
    label_font = _font(25, bold=True)
    draw.rounded_rectangle((margin, int(h*.36), margin+min(w-2*margin, 430),
                             int(h*.36)+56), radius=18, fill=(11, 103, 117, 230))
    draw.text((margin+22, int(h*.36)+13), label, font=label_font, fill=(223, 255, 255))
    title = " ".join(str(headline).upper().split())[:110]
    fit_width = w - 2*margin
    best = (None, None)
    for font_size in (73, 65, 58, 50, 43):
        font = _font(font_size, bold=True)
        lines = _wrap(draw, title, font, fit_width, max_lines=5)
        if lines and len(lines) <= 4 and all(draw.textbbox((0,0),line,font=font)[2] <= fit_width for line in lines):
            best=(font,lines)
            break
    if best == (None,None):
        font = _font(43,bold=True)
        best=(font,_wrap(draw,title,font,fit_width,max_lines=5))
    font, lines = best
    y = int(h*.43)
    spacing = font.size + 17 if hasattr(font,"size") else 62
    for line in lines:
        draw.text((margin,y),line,font=font,fill=(248,253,255))
        y += spacing
    draw.line((margin, int(h*.84), w-margin, int(h*.84)), fill=(68,210,218), width=8)
    draw.text((margin,int(h*.87)), "EXPLORE  •  LEARN  •  DISCOVER",font=_font(25),fill=(205,240,243))
    if LOGO.exists():
        with Image.open(LOGO) as source:
            logo=source.convert("RGBA")
            logo.thumbnail((175,175), Image.Resampling.LANCZOS)
            img.alpha_composite(logo,(w-margin-logo.width,70))
    else:
        draw.text((margin,85),"A2Z AQUARIUMS",font=_font(36,True),fill="white")
    output = BytesIO()
    img.convert("RGB").save(output, "JPEG", quality=88, optimize=True)
    return output.getvalue()
