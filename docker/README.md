# RubyGemDB Ephemeral Indexer Container

This container packages a clean Ruby 4.0.4 runtime equipped with `rubydex`, `decode`, and `bake` to extract AST symbol hierarchies and structured documentation from Ruby gem source trees without requiring complex host Ruby dependencies.

## Image Specification

- **Multi-Stage Build**:
  - **Stage 1 (`builder`)**: Uses `ruby:4.0.4-slim` with build tools (`build-essential`, `cmake`, `gcc`) and library development headers (`*-dev`) to compile native C-extensions for `rubydex`, `decode`, and `bake` into `/usr/local/bundle`.
  - **Stage 2 (`runner`)**: Lean runtime image containing document parsers, CLI utilities, and shared runtime libraries without build tools or header files. Pre-compiled gems are copied from the builder stage.
- **Base Image**: `docker.io/library/ruby:4.0.4-slim`
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
