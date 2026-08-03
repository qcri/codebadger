# Joern CLI container for CPG generation and caching.
# NOTE: base image is noble (Ubuntu 24.04, glibc 2.39), NOT jammy (22.04,
# glibc 2.35). rust2cpg's native astgen binary (rust_ast_gen-linux) is linked
# against GLIBC_2.39 and fails on jammy with "version `GLIBC_2.39' not found",
# silently yielding an empty Rust CPG. glibc is backward-compatible, so every
# other frontend's native astgen keeps working on noble.
FROM eclipse-temurin:21-jdk-noble

RUN apt-get update && apt-get install -y \
    curl \
    wget \
    unzip \
    && rm -rf /var/lib/apt/lists/*

ENV JOERN_VERSION=4.0.594
ENV JOERN_HOME=/opt/joern

RUN set -eux; \
    case "$(uname -m)" in \
      x86_64)        joern_platform=linux-x86_64 ;; \
      aarch64|arm64) joern_platform=linux-arm64 ;; \
      *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;; \
    esac; \
    joern_zip="joern-cli-${joern_platform}.zip"; \
    base_url="https://github.com/joernio/joern/releases/download/v${JOERN_VERSION}"; \
    mkdir -p ${JOERN_HOME}; \
    cd /tmp; \
    wget -q "${base_url}/${joern_zip}"; \
    wget -q "${base_url}/${joern_zip}.sha512"; \
    echo "$(cut -d' ' -f1 "${joern_zip}.sha512")  ${joern_zip}" | sha512sum -c -; \
    unzip -q -d ${JOERN_HOME} "${joern_zip}"; \
    test -x ${JOERN_HOME}/joern-cli/joern; \
    rm -f "${joern_zip}" "${joern_zip}.sha512"

ENV PATH="${JOERN_HOME}/joern-cli:${JOERN_HOME}/joern-cli/bin:${PATH}"

# Rust toolchain — rust2cpg's native astgen loads the crate by shelling out to
# `cargo`/`rustc` (it errors "Are `cargo` and `rustc` on your PATH?" otherwise),
# so without them every Rust CPG comes out empty. The minimal profile installs
# just rustc + cargo (no docs/clippy/rustfmt) to keep the layer small.
ENV RUSTUP_HOME=/opt/rustup \
    CARGO_HOME=/opt/cargo
RUN curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable
ENV PATH="/opt/cargo/bin:${PATH}"

RUN mkdir -p /playground

RUN joern --help && rustc --version && cargo --version

RUN echo '#!/bin/bash\n\
set -e\n\
tail -f /dev/null\n\
' > /entrypoint.sh && chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
