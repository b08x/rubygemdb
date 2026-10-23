# RubyGemDB Ephemeral Indexer Container

This container packages a clean Ruby 4.0.4 runtime equipped with `rubydex`, `decode`, and `bake` to extract AST symbol hierarchies and structured documentation from Ruby gem source trees without requiring complex host Ruby dependencies.

## Image Specification

- **Base Image**: `docker.io/library/ruby:4.0.4-slim`
- **System Packages**: `git`, `build-essential`, `ca-certificates`
- **Ruby Gems**: `rubydex`, `decode`, `bake`
- **Entrypoint**: `ruby /app/extract_graph.rb`

## Building the Image

The Python container runner (`ContainerRunner`) builds this image automatically if missing, using Podman (priority) or Docker (fallback):

```bash
podman build -t rubygemdb-indexer:latest -f docker/indexer.Dockerfile docker/
```

## Running Manually

```bash
podman run --rm \
  -v /path/to/host/gems:/gems:ro,z \
  -v /path/to/host/output:/output:rw,z \
  rubygemdb-indexer:latest \
  -o /output/graph.json /gems/gem1 /gems/gem2
```
