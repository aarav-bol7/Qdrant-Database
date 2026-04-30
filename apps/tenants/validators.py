import re

from django.core.validators import RegexValidator

SLUG_PATTERN = r"^[a-z0-9][a-z0-9_]{2,39}$"
SLUG_REGEX = re.compile(SLUG_PATTERN)

slug_validator = RegexValidator(
    regex=SLUG_PATTERN,
    message=(
        "Must be 3-40 characters of lowercase alphanumeric or underscore, "
        "starting with an alphanumeric character."
    ),
    code="invalid_slug",
)


class InvalidIdentifierError(ValueError):
    """Raised when a tenant_id, bot_id, or other slug-shaped ID fails validation."""


def validate_slug(value: str, *, field_name: str = "identifier") -> None:
    if not isinstance(value, str) or not SLUG_REGEX.fullmatch(value):
        raise InvalidIdentifierError(f"{field_name} {value!r} does not match {SLUG_PATTERN}")
