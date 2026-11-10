# Cross-compile the Windows executable without installing .NET on the host.
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src
COPY desktop/Core desktop/Core
COPY windows/Core windows/Core
COPY windows/Tray windows/Tray
RUN dotnet publish windows/Tray/OrderManager.csproj -c Release -r win-x64 --self-contained true \
    -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -p:DebugType=None -o /out

FROM scratch AS launcher
COPY --from=build /out/OrderManager.exe /

FROM mcr.microsoft.com/dotnet/sdk:10.0 AS test
WORKDIR /src
COPY desktop/Core desktop/Core
COPY windows/Core windows/Core
COPY windows/Tests windows/Tests
RUN dotnet run --project windows/Tests/OrderManager.Tests.csproj -c Release
