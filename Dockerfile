FROM python:3.13-slim-bookworm
WORKDIR /opt/osm-public-transport-matcher
COPY . .
RUN pip install --no-cache-dir '.[swiss,gtfs,acquisition]'
WORKDIR /work
ENTRYPOINT ["osm-public-transport-matcher"]
