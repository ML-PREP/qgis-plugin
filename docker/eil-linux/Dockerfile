# Linux runtime for the headless EIL pipeline (scripts/run_eil_pipeline.py).
#
# linux/amd64 on purpose: the plugin ships its TauDEM tools, routing engine
# and Go zonal engine prebuilt for bin/linux_amd64 only, so this image runs
# exactly those artefacts. On Apple Silicon, Docker Desktop runs it under
# Rosetta, which exposes AVX/AVX2 -- enough for the stock TensorFlow wheels.
#
# The plugin repo is NOT copied in: it is bind-mounted at /plugin, so every run
# executes the working tree as it is. The EIL virtualenv (~/.mlprep_envs) lives
# in a named volume and is provisioned on the first run by the plugin's own
# env_setup code, the same way the plugin's first-run setup does it.
#
# Build and run with docker/eil-linux/eil-docker.sh.
FROM --platform=linux/amd64 ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# qgis + python3-qgis : the plugin's in-process side (Qt, QgsVectorLayer, and
#                       Processing for the seam cleanup)
# grass-core          : r.slopeunits runs inside it (the addon itself is
#                       installed at runtime from the plugin's vendor/ copy)
# gdal-bin            : gdalwarp/ogrinfo, required by the Go zonal engine
# openmpi-bin         : mpiexec; the shipped binaries deliberately do not
#                       bundle libmpi and use the host's (see Dockerfile.linux)
# python3-venv        : the plugin creates ~/.mlprep_envs/venv with it
RUN apt-get update && apt-get install -y --no-install-recommends \
        qgis python3-qgis qgis-providers \
        grass-core \
        gdal-bin python3-gdal \
        python3-numpy python3-matplotlib \
        python3-venv python3-pip \
        openmpi-bin libopenmpi-dev \
        ca-certificates procps file \
    && rm -rf /var/lib/apt/lists/*

# QT_QPA_PLATFORM      : no display; the plugin code still needs a Qt app
# OMPI_ALLOW_RUN_AS_*  : the container runs as root, which OpenMPI refuses
# ..._oversubscribe    : the plugin always launches `mpiexec -n 4`, even when
#                        Docker is given fewer CPUs
# ..._single_copy_...  : CMA is blocked inside containers; without this vader
#                        prints "Read -1, expected ..." and can hang
# PIP_CACHE_DIR        : inside the env volume, so a venv rebuild (changed
#                        requirements_eil.txt) does not re-download TensorFlow
ENV QT_QPA_PLATFORM=offscreen \
    PIP_CACHE_DIR=/root/.mlprep_envs/.pip-cache \
    XDG_RUNTIME_DIR=/tmp/runtime-root \
    OMPI_ALLOW_RUN_AS_ROOT=1 \
    OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1 \
    OMPI_MCA_rmaps_base_oversubscribe=1 \
    OMPI_MCA_btl_vader_single_copy_mechanism=none \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN mkdir -p /tmp/runtime-root && chmod 700 /tmp/runtime-root

WORKDIR /plugin
ENTRYPOINT ["python3", "/plugin/scripts/run_eil_pipeline.py"]
