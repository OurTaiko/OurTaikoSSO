from django.contrib.auth.hashers import BCryptPasswordHasher


class LegacyGoBCryptHasher(BCryptPasswordHasher):
    """Verify existing Go bcrypt hashes, with the same 72-byte registration ceiling."""

    def verify(self, password, encoded):
        if len(password.encode("utf-8")) > 72:
            return False
        try:
            return super().verify(password, encoded)
        except ValueError:
            return False

    def harden_runtime(self, password, encoded):
        if len(password.encode("utf-8")) <= 72:
            super().harden_runtime(password, encoded)
