"""Avatar decoding, WebP re-encoding and storage.

Uploads are fully decoded and re-encoded, so stored objects never carry the
original bytes, metadata (EXIF/GPS) or extra frames.
"""

import hashlib
import io
import logging
from pathlib import PurePath

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

EXTENSIONS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}
MAX_PIXELS = 40_000_000
MAX_SIDE = 512


class AvatarError(ValueError):
    pass


def storage():
    return storages["avatars"]


def to_webp(upload):
    """Return WebP bytes for an uploaded JPEG/PNG/WebP file, or raise AvatarError."""
    extension = PurePath(upload.name or "").suffix.lower()
    if extension not in EXTENSIONS:
        raise AvatarError(_("仅支持 JPG、JPEG、PNG 或 WebP 图片。"))
    if upload.size > settings.AVATAR_MAX_UPLOAD_BYTES:
        raise AvatarError(_("图片不能超过 5 MB。"))
    try:
        # Restrict decoders to the accepted formats; extension and content must agree.
        with Image.open(upload, formats=[EXTENSIONS[extension]]) as image:
            width, height = image.size
            if width < 1 or height < 1 or width * height > MAX_PIXELS:
                raise AvatarError(_("图片尺寸过大。"))
            image.seek(0)  # Animated PNG/WebP: keep the first frame.
            image = ImageOps.exif_transpose(image).convert("RGBA")
    except AvatarError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise AvatarError(_("无法读取这张图片，请确认文件格式与扩展名一致。")) from None
    side = min(MAX_SIDE, image.width, image.height)
    image = ImageOps.fit(image, (side, side), Image.Resampling.LANCZOS)
    if image.getextrema()[3][0] == 255:
        image = image.convert("RGB")
    out = io.BytesIO()
    image.save(out, "WEBP", quality=85, method=4)
    return out.getvalue()


def _delete_later(name):
    def delete():
        try:
            storage().delete(name)
        except Exception:
            # An orphaned object is harmless; never fail the user's request for it.
            logger.warning("Could not delete avatar object %s", name, exc_info=True)

    transaction.on_commit(delete)


def replace(user_model, user_pk, data):
    """Store WebP bytes and point the user at them; the previous object is removed after commit."""
    name = f"avatars/{user_pk}/{hashlib.sha256(data).hexdigest()[:32]}.webp"
    store = storage()
    if not store.exists(name):
        saved = store.save(name, ContentFile(data))
        if saved != name:
            store.delete(saved)
            raise RuntimeError("avatar storage renamed a content-addressed object")
    with transaction.atomic():
        locked = user_model.objects.select_for_update().get(pk=user_pk)
        old = locked.avatar
        user_model.objects.filter(pk=user_pk).update(avatar=name)
        if old and old != name:
            _delete_later(old)
    return name


def clear(user_model, user_pk):
    with transaction.atomic():
        locked = user_model.objects.select_for_update().get(pk=user_pk)
        if locked.avatar:
            user_model.objects.filter(pk=user_pk).update(avatar="")
            _delete_later(locked.avatar)
