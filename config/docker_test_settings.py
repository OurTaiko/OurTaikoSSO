"""Only for the dedicated unification-lab Docker database, never production."""

import os

import dj_database_url

from .test_settings import *  # noqa: F403

if os.getenv("UNIFICATION_LAB") != "true":
    raise RuntimeError("Docker tests require the isolated UNIFICATION_LAB environment.")
DATABASES = {"default": dj_database_url.config()}
