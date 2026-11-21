from urllib.parse import urlsplit

from django.conf import settings
from django.http import QueryDict
from oauth2_provider.models import Application

AUTHORIZE_PATH = "/o/authorize/"


def _callback_origin(params):
    """The exact registered callback origin an authorization request will redirect to, if any."""
    app = Application.objects.filter(client_id=params.get("client_id", "")).first()
    redirect = params.get("redirect_uri", "")
    if app and redirect in app.redirect_uris.split():
        parsed = urlsplit(redirect)
        if parsed.scheme in {"http", "https"} and parsed.netloc and not parsed.username:
            return parsed.scheme + "://" + parsed.netloc
    return None


def _sign_in_next(request):
    """Where a sign-in form on this page goes once it succeeds."""
    if request.path in {"/login/", "/login/email/"}:
        return request.POST.get("next") or request.GET.get("next") or ""
    if request.path == "/login/email/code/":
        from .email_login import SESSION_KEY

        return (request.session.get(SESSION_KEY) or {}).get("next") or ""
    return ""


class UploadLimitMiddleware:
    """Stop spooling oversized avatar uploads; the view then reports a form error.

    Runs before CsrfViewMiddleware parses the multipart body. The CSRF token field
    precedes the file field, so token validation is unaffected.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        from django.core.files.uploadhandler import FileUploadHandler, StopUpload

        class RejectFile(FileUploadHandler):
            def new_file(self, *args, **kwargs):
                raise StopUpload(connection_reset=False)

        if request.path == "/profile/avatar/" and request.method == "POST":
            try:
                length = int(request.META.get("CONTENT_LENGTH") or 0)
            except ValueError:
                length = 0
            if length > settings.AVATAR_MAX_UPLOAD_BYTES + 65536:
                request.upload_too_large = True
                request.upload_handlers = [RejectFile(request)]
        return self.get_response(request)


class ResponsePolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        form_action = "'self'"
        # Chromium also applies form-action to the POST's redirect chain. Permit
        # only this application's exact registered callback origin on consent pages,
        # and on sign-in pages whose next step is that authorization request: signing in
        # there redirects to /o/authorize/, which redirects straight on to the callback
        # once consent is not needed. Without it the browser silently stays on the form.
        params = None
        if request.path == AUTHORIZE_PATH:
            params = request.POST if request.method == "POST" else request.GET
        else:
            next_url = urlsplit(_sign_in_next(request))
            if next_url.path == AUTHORIZE_PATH and next_url.netloc in {"", request.get_host()}:
                params = QueryDict(next_url.query)
        origin = _callback_origin(params) if params is not None else None
        if origin:
            form_action += " " + origin
        # The account page's "link Sakura Viewer" form POSTs to /link/sakura/, which 302s to Viewer.
        match = getattr(request, "resolver_match", None)
        if (match and match.url_name == "connections"
                and settings.SAKURA_REVERSE_OIDC_ENABLED and settings.SAKURA_REVERSE_LINK_ENABLED):
            from .sakura_oidc_views import VIEWER_ISSUER

            viewer = urlsplit(VIEWER_ISSUER)
            form_action += " " + viewer.scheme + "://" + viewer.netloc
        if not getattr(response, "public_cache", False):
            response["Cache-Control"] = "no-store"
        image_sources = "'self' data:"
        if settings.AVATAR_CDN_BASE_URL:
            cdn = urlsplit(settings.AVATAR_CDN_BASE_URL)
            image_sources += " " + cdn.scheme + "://" + cdn.netloc
        response["Content-Security-Policy"] = (
            f"default-src 'self'; style-src 'self'; script-src 'self'; img-src {image_sources}; "
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
        if not getattr(response, "public_cache", False):
            patch_vary_headers(response, ("Cookie",))
        return response
