from django.conf import settings


def site(request):
    return {"dev_mode": settings.DEBUG, "supported_languages": settings.LANGUAGES}
