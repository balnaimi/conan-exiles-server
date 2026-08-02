FROM debian:bookworm-slim@sha256:63a496b5d3b99214b39f5ed70eb71a61e590a77979c79cbee4faf991f8c0783e

LABEL maintainer="BuRashid"
LABEL org.opencontainers.image.title="Conan Exiles Enhanced Dedicated Server — Wine Stable"
LABEL org.opencontainers.image.description="Project stable/default Wine runtime; Native Linux is a separate experimental image"
LABEL org.opencontainers.image.source="https://github.com/balnaimi/conan-exiles-server"
LABEL org.opencontainers.image.licenses="GPL-3.0-only"
LABEL com.balnaimi.conan.runtime="wine"
LABEL com.balnaimi.conan.support-tier="stable"
LABEL com.balnaimi.conan.default="true"

ENV DEBIAN_FRONTEND=noninteractive
ENV WINEPREFIX=/wine
ENV WINEARCH=win64
ENV DISPLAY=:99
ENV WINEDLLOVERRIDES="mscoree,mshtml="

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
        winbind \
        cabextract \
        libegl1 \
        libegl1:i386 \
        libgl1 \
        libgl1:i386 \
        libvulkan1 \
        libvulkan1:i386 \
        mesa-vulkan-drivers \
        mesa-vulkan-drivers:i386 \
    && sed -i 's/# en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8

# Install Wine from WineHQ
RUN mkdir -pm755 /etc/apt/keyrings && \
    wget -O /etc/apt/keyrings/winehq-archive.key https://dl.winehq.org/wine-builds/winehq.key && \
    wget -NP /etc/apt/sources.list.d/ https://dl.winehq.org/wine-builds/debian/dists/bookworm/winehq-bookworm.sources && \
    apt-get update && \
    apt-get install -y --install-recommends winehq-staging && \
    rm -rf /var/lib/apt/lists/*

# Install vc_redist vcrun2022 needed for UE5 
RUN mkdir -p $WINEPREFIX && \
    wget -q https://aka.ms/vs/17/release/vc_redist.x64.exe && \
    xvfb-run -a sh -c "wineboot --init && wineserver -w" && \
    xvfb-run -a sh -c "wine vc_redist.x64.exe /install /quiet /norestart && wineserver -w" && \
    rm vc_redist.x64.exe

# Install SteamCMD
RUN mkdir -p /steamcmd && \
    wget -qO- https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz | tar xz -C /steamcmd && \
    /steamcmd/steamcmd.sh +quit || true

# Create directories
RUN mkdir -p /conanexiles /config /scripts

# Copy entrypoint and shared runtime helpers
COPY entrypoint.sh /scripts/entrypoint.sh
COPY scripts/runtime/ /scripts/runtime/
RUN chmod +x /scripts/entrypoint.sh /scripts/runtime/*.sh

# Game ports
EXPOSE 7777/udp 7778/udp 27015/udp
# RCON port
EXPOSE 25575/tcp

# Persistent data
VOLUME ["/conanexiles", "/config"]

ENTRYPOINT ["/scripts/entrypoint.sh"]
