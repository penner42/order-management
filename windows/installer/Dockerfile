# Inno Setup runs under Wine solely to package the Windows binary.
FROM ubuntu:24.04
ARG INNO_VERSION=6.4.3
ENV DEBIAN_FRONTEND=noninteractive WINEDEBUG=-all WINEPREFIX=/opt/wine
RUN dpkg --add-architecture i386 && apt-get update && apt-get install -y --no-install-recommends \
    wine wine32 wine64 xvfb xauth curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN curl -fL "https://github.com/jrsoftware/issrc/releases/download/is-6_4_3/innosetup-${INNO_VERSION}.exe" -o /tmp/inno.exe \
    && xvfb-run -a wine /tmp/inno.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /DIR=C:\\Inno \
    && rm /tmp/inno.exe
WORKDIR /work
ENTRYPOINT ["xvfb-run", "-a", "wine", "/opt/wine/drive_c/Inno/ISCC.exe"]
CMD ["OrderManager.iss"]
