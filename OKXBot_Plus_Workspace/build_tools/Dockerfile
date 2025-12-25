FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install pyinstaller

# Setup build environment
# Copy source code to /app/src
COPY src/ src/
# Copy spec file to /app/build_tools/
COPY build_tools/linux_build.spec build_tools/

# Build command
# We run from /app/build_tools so the relative paths in spec file (../src) work
WORKDIR /app/build_tools
ENTRYPOINT ["pyinstaller", "--clean", "--distpath", "/dist", "--workpath", "/build", "linux_build.spec"]
