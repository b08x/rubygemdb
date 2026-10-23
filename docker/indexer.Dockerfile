FROM docker.io/library/ruby:4.0.4-slim

# Install build dependencies
RUN apt-get update && apt-get install -y -V --no-install-recommends \
  build-essential \
  cmake \
  gcc \
  git \
  git-lfs \
  imagemagick \
  link-grammar \
  liblink-grammar-dev \
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
  libmagick++-dev \
  libmysqlclient-dev \
  libncurses5-dev \
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
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

# Install indexing and analysis gems
RUN gem install --no-document rubydex decode bake

# Install runtime dependencies only
RUN apt-get update && apt-get install -y -V --no-install-recommends \
  curl \
  exiftool \
  fzf \
  ghostscript \
  git \
  git-lfs \
  gnuplot \
  graphicsmagick \
  graphviz \
  less \
  link-grammar \
  libzmq3-dev \
  minisat \
  mysql-client \
  neovim \
  openjdk-17-jdk-headless \
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
  texlive-xetex \
  texlive-fonts-recommended \
  texlive-plain-generic \
  tidy \
  tzdata \
  xclip \
  zip && \
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
  apt-get update && \
  apt-get install -y nodejs && \
  apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY extract_graph.rb /app/extract_graph.rb
RUN chmod +x /app/extract_graph.rb

ENTRYPOINT ["ruby", "/app/extract_graph.rb"]
