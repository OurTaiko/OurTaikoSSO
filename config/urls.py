from django.conf import settings
from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import include, path
from oauth2_provider import views as oauth_views

from accounts import api, views

oauth_patterns = [
    path("authorize/", oauth_views.AuthorizationView.as_view(), name="authorize"),
    path("token/", oauth_views.TokenView.as_view(), name="token"),
    path("revoke/", oauth_views.RevokeTokenView.as_view(), name="revoke-token"),
    path(".well-known/jwks.json", oauth_views.JwksInfoView.as_view(), name="jwks-info"),
    path("userinfo/", oauth_views.UserInfoView.as_view(), name="user-info"),
    path(
        ".well-known/openid-configuration",
        oauth_views.ConnectDiscoveryInfoView.as_view(),
        name="oidc-connect-discovery-info",
    ),
]
urlpatterns = [
    path("", views.overview, name="overview"),
    path("login/", views.AccountLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("register/", views.register, name="register"),
    path("verify/<str:token>/", views.verify_email, name="verify"),
    path("resend-verification/", views.resend_verification, name="resend-verification"),
    path("language/", views.set_language, name="set-language"),
    path("profile/", views.profile, name="profile"),
    path("profile/avatar/", views.profile_avatar, name="profile-avatar"),
    path("avatars/<str:user_id>/<str:digest>.webp", views.avatar, name="avatar"),
    path("security/", views.security, name="security"),
    path("password/reset/", views.password_reset, name="password-reset"),
    path("password/reset/<uidb64>/<token>/", views.ResetConfirmView.as_view(), name="password_reset_confirm"),
    path("sessions/", views.sessions, name="sessions"),
    path("sessions/revoke/", views.revoke_session, name="revoke-session"),
    path("sessions/revoke-all/", views.revoke_all, name="revoke-all"),
    path("connections/", views.connections, name="connections"),
    path("connections/revoke/", views.revoke_connection, name="revoke-connection"),
    path("healthz", views.health),
    path("readyz", views.ready),
    path("internal/v1/game/login", api.game_login),
    path("internal/v1/game/introspect", api.introspect),
    path("internal/v1/game/revoke", api.revoke),
    path("internal/v1/users/lookup-by-id", api.profiles),
    # Deprecated: compatibility alias only; new callers must use lookup-by-id.
    path("internal/v1/users/lookup", api.profiles),
    path("internal/v1/users/lookup-by-username", api.profiles_by_username),
    path("internal/v1/users/search", api.search_profiles),
    path("internal/v1/web/introspect", api.web_introspect),
    path("internal/v1/web/revoke", api.web_revoke),
    path(".well-known/openid-configuration", oauth_views.ConnectDiscoveryInfoView.as_view()),
    path("o/", include((oauth_patterns, "oauth2_provider"), namespace="oauth2_provider")),
    path("admin/", admin.site.urls),
]
if settings.DEBUG:
    from accounts import demo

    urlpatterns += [
        path("dev/demo/start/", demo.start, name="demo-start"),
        path("dev/demo/callback/", demo.callback, name="demo-callback"),
    ]
