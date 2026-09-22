#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
.venv/bin/python manage.py compilemessages --ignore=.venv --ignore=.data --verbosity=0
exec .venv/bin/python manage.py runserver "${SSO_LISTEN_ADDR:-127.0.0.1:8090}"
