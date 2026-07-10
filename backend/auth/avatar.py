"""Profile-picture validation + normalization.

Accepts a handful of common image formats, enforces a 5 MB upload ceiling, then
fits the image within 200x200 (aspect preserved, never upscaled). Output:

* an **animated GIF** stays an animated GIF — every frame is resized so the
  avatar keeps moving; and
* everything else is normalized to a **PNG** (which also guarantees the browser
  can render it — TIFF and some BMP/ICO variants don't render in ``<img>``).

Raises :class:`ValueError` with an operator-friendly message on any rejection.
"""

from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
TARGET_SIZE = (200, 200)

# Operator-facing allowed upload extensions.
ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {"png", "jpg", "jpeg", "gif", "bmp", "ico", "tiff", "tif"}
)
# Pillow format names the above decode to (defense-in-depth on the decoded
# content, not just the filename).
ALLOWED_FORMATS: frozenset[str] = frozenset(
    {"PNG", "JPEG", "GIF", "BMP", "ICO", "TIFF"}
)

_ALLOWED_HINT = ".png, .jpg, .jpeg, .gif, .bmp, .ico, .tiff"


def _extension(filename: str | None) -> str:
    if not filename or "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def process_avatar(raw: bytes, filename: str | None = None) -> bytes:
    """Validate + normalize an uploaded avatar to a <=200x200 PNG.

    Raises ValueError on an empty/oversized file, an unsupported extension, or
    content that doesn't decode as one of the allowed image formats.
    """

    if not raw:
        raise ValueError("The uploaded file is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("Image is larger than the 5 MB limit.")

    ext = _extension(filename)
    if filename and ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported file type '.{ext}'. Allowed: {_ALLOWED_HINT}.")

    try:
        with Image.open(BytesIO(raw)) as opened:
            opened.load()  # force-decode to catch truncated/corrupt files
            if opened.format not in ALLOWED_FORMATS:
                raise ValueError(f"Unsupported image format. Allowed: {_ALLOWED_HINT}.")
            # Keep an animated GIF animated; everything else becomes a PNG.
            if (
                opened.format == "GIF"
                and getattr(opened, "is_animated", False)
                and getattr(opened, "n_frames", 1) > 1
            ):
                return _resize_animated_gif(opened)
            # Honor EXIF orientation (phone photos) then flatten + shrink.
            oriented = ImageOps.exif_transpose(opened)
            image = oriented.convert("RGBA")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("The file is not a valid image.") from exc

    image.thumbnail(TARGET_SIZE)  # aspect-preserving, only shrinks
    out = BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _resize_animated_gif(img: Image.Image) -> bytes:
    """Resize every frame of an animated GIF to fit 200x200, preserving timing.

    Each frame is read fully composited (Pillow applies GIF disposal on seek),
    resized independently, then re-assembled with the original per-frame
    durations and loop count."""

    frames: list[Image.Image] = []
    durations: list[int] = []
    for frame in ImageSequence.Iterator(img):
        composited = frame.convert("RGBA")
        composited.thumbnail(TARGET_SIZE)
        frames.append(composited)
        durations.append(int(frame.info.get("duration", 100)))

    out = BytesIO()
    frames[0].save(
        out,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        loop=int(img.info.get("loop", 0)),
        duration=durations,
        disposal=2,
        optimize=True,
    )
    return out.getvalue()


def to_data_url(image_bytes: bytes | None) -> str | None:
    """Encode stored avatar bytes as a ``data:`` URL for inline rendering.

    The stored bytes are a normalized PNG, or an animated GIF — sniff which from
    the magic header so the data URL carries the right media type."""
    if not image_bytes:
        return None
    mime = "image/gif" if image_bytes[:4] == b"GIF8" else "image/png"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{encoded}"
