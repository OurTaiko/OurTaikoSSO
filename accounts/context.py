from django.conf import settings


def site(request):
    return {"dev_mode": settings.DEBUG, "supported_languages": settings.LANGUAGES,
            "sakura_reverse_oidc_enabled": settings.SAKURA_REVERSE_OIDC_ENABLED}
