# syntax=docker/dockerfile:1.7
#
# One image for everything: the web app (the default command), celery
# workers, celery beat and migrations, each picks its command in Kubernetes.
# Runs as an unprivileged user with a read only root filesystem

ARG PYTHON_VERSION=3.14

FROM ghcr.io/astral-sh/uv:0.13.0 AS uv

FROM python:${PYTHON_VERSION}-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

# Dependencies first, so they're cached until the lock file changes
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# Static files are served by whitenoise from the image
RUN DJANGO_SETTINGS_MODULE=config.settings.production \
    DJANGO_SECRET_KEY=collectstatic-only \
    DJANGO_DATABASE_URL=sqlite://:memory: \
    /app/.venv/bin/python manage.py collectstatic --noinput

FROM python:${PYTHON_VERSION}-slim
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="qsology" \
      org.opencontainers.image.source="https://github.com/qsology/qsology" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

RUN groupadd --system --gid 10001 qsology \
    && useradd --system --uid 10001 --gid qsology --no-create-home \
       --home-dir /nonexistent --shell /usr/sbin/nologin qsology

# Owned by root, so the app can't change its own code
COPY --from=build /app /app
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    OTEL_SERVICE_VERSION="${VERSION}" \
    OTEL_RESOURCE_ATTRIBUTES="service.version=${VERSION},vcs.revision=${REVISION}"

USER 10001:10001
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application"]
