# syntax=docker/dockerfile:1.7
# One image, two processes: the API (default) and the ingestion worker (`lean-rag-worker`).
# Base images come from ECR Public to avoid Docker Hub pull limits on AWS build hosts.
ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim-bookworm

FROM ${PYTHON_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install .

FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    RAG_LOCAL_DATA_DIR=/tmp/lean-rag
RUN groupadd --system app && useradd --system --gid app --home /app --shell /usr/sbin/nologin app
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
USER app
EXPOSE 8000
CMD ["lean-rag-api"]
