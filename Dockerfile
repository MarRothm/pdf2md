# Web service image — built for linux/arm64, loaded onto the Mac mini from an archive.
FROM python:3.12-slim AS build

WORKDIR /build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir .

FROM python:3.12-slim

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN useradd --create-home --uid 10001 pdf2md \
    && mkdir -p /data/db /data/inbox /data/outbox \
    && chown -R pdf2md:pdf2md /data

COPY --from=build /opt/venv /opt/venv

USER pdf2md
WORKDIR /app
EXPOSE 8080

CMD ["uvicorn", "pdf2md.main:app", "--host", "0.0.0.0", "--port", "8080"]
