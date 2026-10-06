# MinIO's original registry images are no longer publicly available.
# Build the original RELEASE.2024-05-28T17-19-04Z from its official commit.
FROM golang:1.22-bookworm AS build
WORKDIR /src
ADD https://github.com/minio/minio/archive/f79a4ef4d0dc3e6562cad0d1d1db674bc8c75531.tar.gz /tmp/minio.tar.gz
RUN tar -xzf /tmp/minio.tar.gz --strip-components=1 -C /src
RUN CGO_ENABLED=0 go build -trimpath -o /out/minio .

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/minio /usr/bin/minio
COPY --from=build /src/LICENSE /licenses/LICENSE
COPY --from=build /src/dockerscripts/docker-entrypoint.sh /usr/bin/docker-entrypoint.sh
EXPOSE 9000 9001
ENTRYPOINT ["/usr/bin/docker-entrypoint.sh"]
CMD ["minio"]
