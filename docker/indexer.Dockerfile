FROM docker.io/library/ruby:4.0.4-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install indexing and analysis gems
RUN gem install --no-document rubydex decode bake

WORKDIR /app
COPY extract_graph.rb /app/extract_graph.rb
RUN chmod +x /app/extract_graph.rb

ENTRYPOINT ["ruby", "/app/extract_graph.rb"]
