FROM python:3.11-slim

LABEL maintainer="Alexander Graf <alex@otherguy.io>"

WORKDIR /app

# Install system dependencies if any (none needed for standard mysql-connector-python)
RUN apt-get update && apt-get install -y --no-install-recommends git-all \
    && rm -rf /var/lib/apt/lists/* \

# Copy requirements and install
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Copy the script
COPY clean.py .
COPY nextcloud_scan.py .

# Build arguments
ARG VCS_REF=main
ARG BUILD_DATE=""
ARG VERSION="${VCS_REF}"

LABEL org.label-schema.schema-version="1.0" \
    org.label-schema.name="nextcloud-cleanup" \
    org.label-schema.vendor="otherguy" \
    org.label-schema.version="${VERSION}" \
    org.label-schema.build-date="${BUILD_DATE}" \
    org.label-schema.description="Cleans up files on Nextcloud S3 storage that are left over from canceled uploads." \
    org.label-schema.vcs-url="https://github.com/otherguy/nextcloud-cleanup" \
    org.label-schema.vcs-ref="${VCS_REF}"

ENV VERSION="${VERSION}" \
    BUILD_DATE="${BUILD_DATE}" \
    VCS_REF="${VCS_REF}"

ENTRYPOINT ["python", "nextcloud_scan.py"]
