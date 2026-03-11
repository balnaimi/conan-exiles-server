FROM debian:trixie-slim

LABEL maintainer="BuRashid"
LABEL description="Conan Exiles Dedicated Server"

ENV DEBIAN_FRONTEND=noninteractive
ENV WINEPREFIX=/wine
ENV WINEARCH=win64
ENV DISPLAY=:99

# Install dependencies
RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        wget \
        gnupg2 \
        xvfb \
        xauth \
        lib32gcc-s1 \
        curl \
        procps \
        locales \
    && sed -i 's/# en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8

# Install Wine from WineHQ
RUN mkdir -pm755 /etc/apt/keyrings && \
    wget -O /etc/apt/keyrings/winehq-archive.key https://dl.winehq.org/wine-builds/winehq.key && \
    wget -NP /etc/apt/sources.list.d/ https://dl.winehq.org/wine-builds/debian/dists/trixie/winehq-trixie.sources && \
    apt-get update && \
    apt-get install -y --install-recommends winehq-stable && \
    rm -rf /var/lib/apt/lists/*

# Install SteamCMD
RUN mkdir -p /steamcmd && \
    wget -qO- https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz | tar xz -C /steamcmd && \
    /steamcmd/steamcmd.sh +quit || true

# Create directories
RUN mkdir -p /conanexiles /config /scripts

# Copy entrypoint
COPY entrypoint.sh /scripts/entrypoint.sh
RUN chmod +x /scripts/entrypoint.sh

# Game ports
EXPOSE 7777/udp 7778/udp 27015/udp
# RCON port
EXPOSE 25575/tcp

# Persistent data
VOLUME ["/conanexiles", "/config"]

ENTRYPOINT ["/scripts/entrypoint.sh"]
