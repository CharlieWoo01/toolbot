# syntax=docker/dockerfile:1
#
# Toolbot image: Python runtime + git + the toolbot CLI.
#
#   docker build -t toolbot:dev .
#   docker run --rm toolbot:dev toolbot --help
#   docker run --rm -v "$PWD:/work" -w /work toolbot:dev toolbot outdated
#
# The base image is pinned by tag and digest.

ARG PYTHON_IMAGE=python:3.13.15-slim-trixie@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b

FROM ${PYTHON_IMAGE} AS build
WORKDIR /src
COPY pyproject.toml README.md requirements.lock ./
COPY src/ src/
RUN pip wheel --no-cache-dir --wheel-dir /wheels --constraint requirements.lock .

FROM ${PYTHON_IMAGE}
ARG TOOLBOT_VERSION=dev
ARG TOOLBOT_REVISION=unknown
LABEL org.opencontainers.image.title="toolbot" \
      org.opencontainers.image.description="Automated update requests for standalone tools and binary release artifacts" \
      org.opencontainers.image.version="${TOOLBOT_VERSION}" \
      org.opencontainers.image.revision="${TOOLBOT_REVISION}"

# git is Toolbot's only system dependency (branches, commits, pushes).
RUN apt-get update \
 && apt-get install --yes --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links /wheels toolbot \
 && rm -rf /wheels \
 && useradd --create-home --uid 10001 toolbot

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER toolbot
WORKDIR /home/toolbot
CMD ["toolbot", "--help"]
