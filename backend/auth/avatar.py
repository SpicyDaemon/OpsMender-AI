"""Profile-picture validation + normalization.

Accepts a handful of common image formats, enforces a 5 MB upload ceiling, then
normalizes every upload to a PNG that fits within 200x200 (aspect preserved,
never upscaled). Normalizing to PNG guarantees the browser can render it (TIFF
and some BMP/ICO variants don't render in ``<img>``) and keeps the stored bytes
small. Raises :class:`ValueError` with an operator-friendly message on any
rejection.
"""

from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

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
        raise ValueError(
            f"Unsupported file type '.{ext}'. Allowed: {_ALLOWED_HINT}."
        )

    try:
        with Image.open(BytesIO(raw)) as opened:
            opened.load()  # force-decode to catch truncated/corrupt files
            if opened.format not in ALLOWED_FORMATS:
                raise ValueError(f"Unsupported image format. Allowed: {_ALLOWED_HINT}.")
            # Honor EXIF orientation (phone photos) then flatten + shrink.
            oriented = ImageOps.exif_transpose(opened)
            image = oriented.convert("RGBA")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("The file is not a valid image.") from exc

    image.thumbnail(TARGET_SIZE)  # aspect-preserving, only shrinks
    out = BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def to_data_url(png_bytes: bytes | None) -> str | None:
    """Encode normalized PNG bytes as a ``data:`` URL for inline rendering."""
    if not png_bytes:
        return None
    encoded = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{encoded}"
