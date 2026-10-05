# whitebox-studio production image (Linux x86_64).
#
# Build:   docker build -f docker/Dockerfile -t whitebox-studio:0.1.0 .
# Run:     docker run --rm --gpus all -v /data/wbs:/workspace whitebox-studio:0.1.0 doctor
#
# Rendering uses Blender Workbench/EEVEE, which needs OpenGL: on GPU hosts run with the NVIDIA
# container toolkit (--gpus all); without a GPU, Mesa llvmpipe (installed below) renders in software
# (slow, but correct). Everything is downloaded from public release pages and checksum-verified.

FROM python:3.11-slim-bookworm

ARG BLENDER_VERSION=5.1.2
ARG YUNET_SHA256=8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg curl xz-utils ca-certificates fonts-noto-cjk \
        libegl1 libgl1 libgl1-mesa-dri libglu1-mesa libxi6 libxxf86vm1 libxfixes3 libxrender1 libxkbcommon0 \
        libsm6 libice6 libx11-6 libxext6 libxrandr2 libxinerama1 libxcursor1 libwayland-client0 \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    minor="${BLENDER_VERSION%.*}"; \
    name="blender-${BLENDER_VERSION}-linux-x64"; \
    curl -fsSL "https://download.blender.org/release/Blender${minor}/blender-${BLENDER_VERSION}.sha256" -o /tmp/blender.sha256; \
    curl -fL --retry 3 "https://download.blender.org/release/Blender${minor}/${name}.tar.xz" -o "/tmp/${name}.tar.xz"; \
    (cd /tmp && grep " ${name}.tar.xz\$" blender.sha256 | sha256sum -c -); \
    tar -xJf "/tmp/${name}.tar.xz" -C /opt; \
    ln -s "/opt/${name}" /opt/blender; \
    rm -f /tmp/*.tar.xz /tmp/blender.sha256

RUN set -eux; mkdir -p /opt/models; \
    curl -fL --retry 3 "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx" \
        -o /opt/models/face_detection_yunet_2023mar.onnx; \
    echo "${YUNET_SHA256}  /opt/models/face_detection_yunet_2023mar.onnx" | sha256sum -c -

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY configs ./configs
COPY prompts ./prompts
COPY taxonomy ./taxonomy
COPY schemas ./schemas
COPY skills ./skills
RUN pip install --no-cache-dir .

ENV WBS_BLENDER=/opt/blender/blender \
    WBS_FFMPEG=/usr/bin/ffmpeg \
    WBS_FFPROBE=/usr/bin/ffprobe \
    WBS_FACE_MODEL=/opt/models/face_detection_yunet_2023mar.onnx \
    WBS_WORKSPACE=/workspace \
    PYTHONUTF8=1

RUN useradd --create-home --uid 10001 wbs && mkdir -p /workspace && chown wbs /workspace
USER wbs
VOLUME ["/workspace"]
ENTRYPOINT ["wbs"]
CMD ["doctor"]
