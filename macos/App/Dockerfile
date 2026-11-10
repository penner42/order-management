FROM swift:6.0
WORKDIR /app
COPY Package.swift ./
COPY Sources Sources
COPY Tests Tests
RUN swift test
