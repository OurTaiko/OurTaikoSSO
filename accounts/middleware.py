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
