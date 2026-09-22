from urllib.parse import urlsplit

from oauth2_provider.models import Application


class ResponsePolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        form_action = "'self'"
        # Chromium also applies form-action to the POST's redirect chain. Permit
        # only this application's exact registered callback origin on consent pages.
        if request.path == "/o/authorize/":
            params = request.POST if request.method == "POST" else request.GET
            app = Application.objects.filter(client_id=params.get("client_id", "")).first()
            redirect = params.get("redirect_uri", "")
            if app and redirect in app.redirect_uris.split():
                parsed = urlsplit(redirect)
                if parsed.scheme in {"http", "https"} and parsed.netloc and not parsed.username:
                    form_action += " " + parsed.scheme + "://" + parsed.netloc
        response["Cache-Control"] = "no-store"
        response["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
            "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
            "form-action " + form_action
        )
        return response


class AccountLocaleMiddleware:
    """Account preference is authoritative; anonymous visitors default to Chinese.

    Do not infer preference from Accept-Language or overwrite an account on login.
    Language switching uses a CSRF-protected POST and only changes this field.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        from django.utils import translation
        from django.utils.cache import patch_vary_headers

        language = (
            request.user.preferred_language
            if request.user.is_authenticated
            else request.session.get("preferred_language", settings.LANGUAGE_CODE)
        )
        if language not in dict(settings.LANGUAGES):
            language = settings.LANGUAGE_CODE
        request.LANGUAGE_CODE = language
        with translation.override(language):
            response = self.get_response(request)
        response.setdefault("Content-Language", language)
        patch_vary_headers(response, ("Cookie",))
        return response
