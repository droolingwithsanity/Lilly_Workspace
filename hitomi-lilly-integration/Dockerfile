# Stage 1: Build the APK
FROM eclipse-temurin:17-jdk AS builder

ENV ANDROID_SDK_ROOT /opt/android-sdk
ENV ANDROID_HOME /opt/android-sdk
ENV ANDROID_SDK_MANAGER ${ANDROID_SDK_ROOT}/cmdline-tools/latest/bin/sdkmanager
ENV GRADLE_VERSION 8.1.1

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget unzip xz-utils && \
    rm -rf /var/lib/apt/lists/*

# Install Gradle
RUN wget -q https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip \
    && unzip -q gradle-${GRADLE_VERSION}-bin.zip -d /opt \
    && rm gradle-${GRADLE_VERSION}-bin.zip
ENV PATH /opt/gradle-${GRADLE_VERSION}/bin:${PATH}

# Install Android SDK
RUN mkdir -p ${ANDROID_SDK_ROOT}/cmdline-tools && \
    wget -q https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip && \
    unzip -q commandlinetools-linux-11076708_latest.zip -d /tmp/cmdline-tools && \
    mkdir -p ${ANDROID_SDK_ROOT}/cmdline-tools/latest && \
    mv /tmp/cmdline-tools/cmdline-tools/* ${ANDROID_SDK_ROOT}/cmdline-tools/latest/ && \
    rm commandlinetools-linux-11076708_latest.zip && \
    rm -rf /tmp/cmdline-tools

# Accept licenses and install SDK components
RUN yes | ${ANDROID_SDK_MANAGER} --licenses > /dev/null 2>&1 || true && \
    yes | ${ANDROID_SDK_MANAGER} "platforms;android-34" "build-tools;34.0.0" \
    "platform-tools" "cmdline-tools;latest" > /dev/null 2>&1 || true

# Copy project
WORKDIR /build
COPY android/ .

# Build the release APK
RUN gradle assembleRelease --no-daemon || gradle assembleDebug --no-daemon

# Stage 2: Serve the APK
FROM python:3.11-alpine

RUN apk add --no-cache tini
RUN pip install --no-cache-dir fastapi uvicorn httpx pydantic
COPY server/app.py /app/server.py

# Copy APK from builder
COPY --from=builder /build/app/build/outputs/apk/ /tmp/apk-output/
RUN mkdir -p /app/apk && \
    if [ -f /tmp/apk-output/release/app-release.apk ]; then \
        cp /tmp/apk-output/release/app-release.apk /app/apk/LillyOverlay-v2.2.apk; \
    elif [ -f /tmp/apk-output/debug/app-debug.apk ]; then \
        cp /tmp/apk-output/debug/app-debug.apk /app/apk/LillyOverlay-v2.2.apk; \
    fi && \
    rm -rf /tmp/apk-output

EXPOSE 8098

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["python3", "/app/server.py"]
