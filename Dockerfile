FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --require-hashes -r requirements.txt && groupadd -g 10001 sso && useradd -r -u 10001 -g sso sso
COPY . .
RUN DJANGO_DEBUG=true DJANGO_SECRET_KEY=build-only-placeholder-not-a-production-secret-key python manage.py collectstatic --noinput && mkdir -p /app/.data && chown 10001:10001 /app/.data
USER 10001:10001
CMD ["gunicorn", "--config", "deploy/gunicorn.conf.py", "config.wsgi:application"]
