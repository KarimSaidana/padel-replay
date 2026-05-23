#!/bin/bash
# Build FFmpeg Lambda Layer
# Usage: ./build_ffmpeg_layer.sh

set -e

echo "Building FFmpeg Lambda Layer..."

# Create temporary directory structure
mkdir -p layer/bin
mkdir -p layer_package

# Download FFmpeg binary for Amazon Linux 2
# Using pre-built binary from johnvansickle.com (popular trusted source)
cd layer/bin
wget -q https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz || {
    echo "Error: Could not download FFmpeg. Check your internet connection."
    exit 1
}

# Extract
tar xf ffmpeg-release-amd64-static.tar.xz
rm ffmpeg-release-amd64-static.tar.xz

# Find the ffmpeg binary and copy to layer/bin
find . -name "ffmpeg" -type f -executable -exec cp {} . \;
rm -rf ffmpeg-*

# Verify
if [ ! -f ffmpeg ]; then
    echo "Error: FFmpeg binary not found after extraction"
    exit 1
fi

chmod +x ffmpeg
echo "✓ FFmpeg binary ready at layer/bin/ffmpeg"

# Create layer zip
cd ..
zip -r ../layer_package/ffmpeg-layer.zip .
cd ..

# Count size
SIZE_KB=$(du -sk layer_package/ffmpeg-layer.zip | cut -f1)
echo "✓ Layer package created: $SIZE_KB KB"
echo "✓ Ready to deploy: aws lambda publish-layer-version --layer-name padel-ffmpeg --zip-file fileb://layer_package/ffmpeg-layer.zip --compatible-runtimes python3.11"

# Cleanup
rm -rf layer
