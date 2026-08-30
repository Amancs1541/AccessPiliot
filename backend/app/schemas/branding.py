from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Only raster image data URIs are accepted — deliberately excludes image/svg+xml, since an SVG can carry embedded
# script content that some contexts will execute; a plain <img src="data:image/png;base64,..."> cannot.
_DATA_URI_PATTERN = re.compile(r"^data:image/(png|jpeg|jpg|gif|webp);base64,")
_MAX_LOGO_LENGTH = 2_800_000  # ~2MB of source image data once base64-inflated


class BrandingSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    sign_in_logo: str | None = None
    internal_logo: str | None = None
    powered_by_text: str | None = None


def _validate_logo(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) > _MAX_LOGO_LENGTH:
        raise ValueError("Logo image is too large (max ~2MB).")
    if not _DATA_URI_PATTERN.match(value):
        raise ValueError("Logo must be a PNG, JPEG, GIF, or WEBP image.")
    return value


class BrandingSettingsUpdateRequest(BaseModel):
    sign_in_logo: str | None = None
    internal_logo: str | None = None
    powered_by_text: str | None = Field(default=None, max_length=100)

    @field_validator("sign_in_logo", "internal_logo")
    @classmethod
    def _check_logo(cls, value: str | None) -> str | None:
        return _validate_logo(value)
