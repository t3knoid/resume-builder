# syntax=docker/dockerfile:1
# resume-builder image (PRD section 11). Contains code, templates, migrations and system
# libraries only. User data, databases, output and secrets are never copied in (.dockerignore).

# Pinned by digest (D4). Update deliberately: look up the new digest, rebuild, run the tests.
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

# WeasyPrint's libraries and a fixed font set, so PDFs render the same everywhere (DK2).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libfontconfig1 \
      fonts-dejavu-core fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp \
    RB_DATA_ROOT=/data \
    RB_DB_ROOT=/db \
    RB_OUTPUT_ROOT=/output \
    RB_BACKUP_ROOT=/backups \
    RB_PORT=8000

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
# The user guides, packaged as the in-app help (issue #34): only these three, never the contributor docs.
COPY docs/QUICKSTART.md docs/USER-MANUAL.md docs/AI-AGENTS.md ./src/resume_builder/help/
RUN pip install ".[pdf,test]"
COPY examples ./examples
COPY tests ./tests
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
# Strip CRLF in case the repo was checked out on Windows without .gitattributes applied.
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh \
 && chmod 0755 /usr/local/bin/entrypoint.sh \
 && mkdir -p /data /db /output /backups \
 && chmod -R a+rX /app

# Identifies the image that produced each document (R12.2, R12.7). CI passes the git commit.
ARG RB_BUILD_ID=local-build
ENV RB_IMAGE_DIGEST=${RB_BUILD_ID}

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["rb", "serve"]
