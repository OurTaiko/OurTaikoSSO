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


class ViewerBindingConflict(LookupError):
    pass


class ViewerBindingMissing(LookupError):
    pass


class ViewerAccountDisabled(LookupError):
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
    if application_id != "Sakura":
        raise ViewerBindingMissing("Formal Sakura application required")
    try:
        link = ExternalLink.objects.select_related("user", "application").get(
            application__client_id="Sakura", external_id=sub,
        )
    except ExternalLink.DoesNotExist:
        raise ViewerBindingMissing("Viewer account has no formal link") from None
    except ExternalLink.MultipleObjectsReturned:
        raise ViewerBindingConflict("Ambiguous formal binding") from None
    if not link.user.is_active:
        # The pair still exists, so the Viewer account must not be given a second OurTaiko one.
        raise ViewerAccountDisabled("Viewer account is linked to a disabled account")
    return link.user
