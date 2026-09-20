from oauth2_provider.oauth2_validators import OAuth2Validator


class AccountValidator(OAuth2Validator):
    def validate_grant_type(self, client_id, grant_type, client, request, *args, **kwargs):
        if grant_type not in {"authorization_code", "refresh_token"}:
            return False
        return super().validate_grant_type(client_id, grant_type, client, request, *args, **kwargs)

    def validate_response_type(self, client_id, response_type, client, request, *args, **kwargs):
        if response_type != "code":
            return False
        return super().validate_response_type(client_id, response_type, client, request, *args, **kwargs)

    def get_additional_claims(self):
        return {
            "nickname": lambda request: request.user.nickname,
            "preferred_username": lambda request: request.user.username,
        }

    def validate_bearer_token(self, token, scopes, request):
        return bool(super().validate_bearer_token(token, scopes, request) and request.user.is_active)

    def validate_refresh_token(self, refresh_token, client, request, *args, **kwargs):
        return bool(
            super().validate_refresh_token(refresh_token, client, request, *args, **kwargs)
            and request.user.is_active
        )

    def validate_code(self, client_id, code, client, request, *args, **kwargs):
        return bool(
            super().validate_code(client_id, code, client, request, *args, **kwargs)
            and request.user.is_active
        )
