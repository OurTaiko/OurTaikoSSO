from oauth2_provider.oauth2_validators import OAuth2Validator


class AccountValidator(OAuth2Validator):
    @staticmethod
    def application_enabled(client):
        from .models import ApplicationAccess

        return (
            client is not None
            and not ApplicationAccess.objects.filter(application=client, enabled=False).exists()
        )

    def validate_client_id(self, client_id, request, *args, **kwargs):
        return bool(
            super().validate_client_id(client_id, request, *args, **kwargs)
            and self.application_enabled(request.client)
        )

    def validate_grant_type(self, client_id, grant_type, client, request, *args, **kwargs):
        if not self.application_enabled(client) or grant_type not in {"authorization_code", "refresh_token"}:
            return False
        return super().validate_grant_type(client_id, grant_type, client, request, *args, **kwargs)

    def validate_response_type(self, client_id, response_type, client, request, *args, **kwargs):
        if not self.application_enabled(client) or response_type != "code":
            return False
        return super().validate_response_type(client_id, response_type, client, request, *args, **kwargs)

    def get_additional_claims(self):
        return {
            "nickname": lambda request: request.user.nickname,
            "locale": lambda request: request.user.preferred_language,
            "preferred_username": lambda request: request.user.username,
            "picture": lambda request: request.user.absolute_avatar_url or None,
        }

    def get_oidc_claims(self, token, token_handler, request):
        # Omit unset optional claims (e.g. picture) instead of emitting null.
        claims = super().get_oidc_claims(token, token_handler, request)
        return {k: v for k, v in claims.items() if v is not None}

    def validate_bearer_token(self, token, scopes, request):
        return bool(
            super().validate_bearer_token(token, scopes, request)
            and request.user.is_active
            and self.application_enabled(request.client)
        )

    def validate_refresh_token(self, refresh_token, client, request, *args, **kwargs):
        return bool(
            super().validate_refresh_token(refresh_token, client, request, *args, **kwargs)
            and request.user.is_active
            and self.application_enabled(client)
        )

    def validate_code(self, client_id, code, client, request, *args, **kwargs):
        return bool(
            super().validate_code(client_id, code, client, request, *args, **kwargs)
            and request.user.is_active
            and self.application_enabled(client)
        )
