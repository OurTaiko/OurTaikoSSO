"""Viewer OIDC -> existing formal OurTaiko binding resolution.

No credentials, password forwarding, email matching, provisioning or account
mutation occurs here. The caller MUST fully verify OIDC via a maintained RP.
"""
import re

from .models import ExternalLink

_VIEWER_SUB = re.compile(r"[1-9][0-9]{0,19}\Z")
_MAX_UINT64 = 18446744073709551615


class ViewerSubjectInvalid(ValueError):
    pass


class ViewerBindingMissing(LookupError):
    pass


def verified_viewer_sub(value):
    """Return the canonical decimal Viewer account ID, never through float."""
    if not isinstance(value, str) or _VIEWER_SUB.fullmatch(value) is None:
        raise ViewerSubjectInvalid("Invalid OIDC subject")
    if int(value) > _MAX_UINT64:
        raise ViewerSubjectInvalid("Invalid OIDC subject")
    return value


def bound_ourtaiko_user(viewer_sub, *, application_id="Sakura"):
    """Find a previously confirmed formal OurTaiko -> Viewer exact mapping.

    The formal client is `Sakura`. A staging or unrelated Application's binding
    must NEVER grant a login in the formal reverse flow.
    """
    sub = verified_viewer_sub(viewer_sub)
    link = (
        ExternalLink.objects.select_related("user")
        .filter(application__client_id=application_id, external_id=sub)
        .first()
    )
    if link is None or not link.user.is_active:
        raise ViewerBindingMissing("Viewer account has no active formal link")
    return link.user
