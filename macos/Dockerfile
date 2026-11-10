FROM mcr.microsoft.com/dotnet/sdk:10.0 AS source
WORKDIR /src
COPY desktop/Core desktop/Core
COPY macos/Helper macos/Helper
COPY macos/Tests macos/Tests
FROM source AS test
RUN dotnet run --project macos/Tests/OrderManager.Mac.Tests.csproj -c Release
FROM source AS build
RUN dotnet publish macos/Helper/OrderManager.Mac.csproj -c Release -r osx-arm64 --self-contained true -p:DebugType=None -o /out
FROM scratch AS helper
COPY --from=build /out/ /

FROM python:3.12-slim AS metadata-test
WORKDIR /src
COPY macos/release.py macos/release-settings.json macos/
COPY macos/Tests/package-check.py macos/Tests/package-check.py
RUN python macos/Tests/package-check.py
