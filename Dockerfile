FROM python:3.12-slim-bookworm@sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134 AS python-base

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

FROM python-base AS runtime

COPY requirements.lock /app/requirements.lock
RUN pip install -r /app/requirements.lock

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/app --shell /usr/sbin/nologin app \
    && mkdir -p /data /backups \
    && chown app:app /data /backups

COPY app /app/app
COPY migrations /app/migrations
COPY alembic.ini wsgi.py /app/
COPY scripts/entrypoint.sh /app/entrypoint.sh

RUN chmod 755 /app/entrypoint.sh \
    && chmod -R a+rX /app/app /app/migrations /app/alembic.ini /app/wsgi.py

USER app
EXPOSE 8080
VOLUME ["/data", "/backups"]
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "30", "--graceful-timeout", "30", "--access-logfile", "-", "--error-logfile", "-", "--capture-output", "wsgi:app"]

FROM runtime AS verify

USER root
COPY requirements-dev.lock pyproject.toml /app/
RUN pip install -r /app/requirements-dev.lock
COPY tests /app/tests
COPY scripts /app/scripts
COPY systemd /app/systemd
COPY compose.yaml compose.prod.yaml /app/
RUN chmod 755 /app/scripts/verify.sh /app/scripts/entrypoint.sh \
    && chmod 755 /app/scripts/run-backup-vps.sh /app/scripts/restore-vps.sh \
        /app/scripts/restore-drill-vps.sh /app/scripts/install-backup-timer-vps.sh \
        /app/scripts/deploy-vps.sh /app/scripts/deploy-remote.sh \
        /app/scripts/bootstrap-prod-secrets.sh /app/scripts/ensure-caddy-inventory.sh \
        /app/scripts/rollback-drill-vps.sh \
    && chmod -R a+rX /app/tests /app/scripts /app/pyproject.toml /app/systemd \
        /app/compose.yaml /app/compose.prod.yaml /app/scripts/deploy_lib.py
USER app
