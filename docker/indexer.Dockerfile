# ==============================================================================
# Stage 1: Builder
# Compiles indexing gems with C-extensions and development headers
# ==============================================================================
FROM docker.io/library/ruby:4.0.4-slim AS builder

# Install build-time compiler tools and library development headers
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    cmake \
    default-libmysqlclient-dev \
    gcc \
    git \
    git-lfs \
    libcairo2-dev \
    libczmq-dev \
    libffi-dev \
    libfftw3-dev \
    libgdbm-dev \
    libgirepository1.0-dev \
    libglib2.0-dev \
    libgmp-dev \
    libgsl-dev \
    liblapacke-dev \
    liblink-grammar-dev \
    libmagick++-dev \
    libncurses-dev \
    libopenblas-dev \
    libopenslide-dev \
    libpoppler-glib-dev \
    libpq-dev \
    libreadline-dev \
    libsqlite3-dev \
    libssl-dev \
    libvips-dev \
    libxml2-dev \
    libxslt1-dev \
    libyaml-dev \
    libzmq3-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Install indexing and analysis gems (pre-compiling C-extensions into GEM_HOME)
RUN gem install --no-document rubydex decode bake

# ==============================================================================
# Stage 2: Runner / Runtime
# Clean runtime environment with CLI tools, shared libraries, and entrypoint
# ==============================================================================
FROM docker.io/library/ruby:4.0.4-slim AS runner

# Install runtime utilities, document tools, and shared libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    default-jdk-headless \
    default-mysql-client \
    exiftool \
    fzf \
    ghostscript \
    git \
    git-lfs \
    gnuplot \
    graphicsmagick \
    graphviz \
    imagemagick \
    less \
    libcairo2 \
    libczmq4 \
    libffi8 \
    libglib2.0-0t64 \
    libgmp10 \
    liblapacke \
    libncurses6 \
    libopenblas0 \
    libopenslide0 \
    libpq5 \
    libsqlite3-0 \
    libvips42t64 \
    libxml2 \
    libxslt1.1 \
    libyaml-0-2 \
    libzmq5 \
    link-grammar \
    minisat \
    neovim \
    nodejs \
    openssh-client \
    pandoc \
    pdftk \
    plotutils \
    poppler-utils \
    postgresql-client \
    python3-link-grammar \
    ripgrep \
    rsync \
    sqlite3 \
    tesseract-ocr \
    texlive-fonts-recommended \
    texlive-plain-generic \
    texlive-xetex \
    tidy \
    tzdata \
    xclip \
    zip \
    zlib1g \
    && rm -rf /var/lib/apt/lists/*

# Copy pre-compiled gems and executables from builder stage
COPY --from=builder /usr/local/bundle /usr/local/bundle

WORKDIR /app
COPY extract_graph.rb /app/extract_graph.rb
RUN chmod +x /app/extract_graph.rb

ENTRYPOINT ["ruby", "/app/extract_graph.rb"]
