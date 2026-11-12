FROM python:3.12-slim-bookworm AS runtime-base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM runtime-base AS dependency-audit
COPY requirements-security.txt /audit/requirements-security.txt
RUN python -m venv /opt/dependency-audit \
    && /opt/dependency-audit/bin/pip install --no-cache-dir -r /audit/requirements-security.txt
ARG DEPENDENCY_AUDIT_NONCE=local
RUN /opt/dependency-audit/bin/pip-audit --disable-pip --no-deps \
    -r /app/requirements.txt -f json -o /audit/result.json

FROM runtime-base AS runtime
COPY --from=dependency-audit /audit/result.json /runtime-dependency-audit.json
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
COPY --chown=app:app app ./app
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app scripts ./scripts
# Production requires APP_CONFIGS; ignored local credentials stay in Compose's
# development bind mount and must not enter a release image layer.
COPY --chown=app:app alembic.ini run.py ./
USER app
EXPOSE 8000
CMD ["python", "run.py"]
