# encodingwf

# Use five82/encodingtools-multiarch for our base build image
FROM ghcr.io/five82/encodingtools-multiarch:latest

# Set the working directory to /app
WORKDIR /app

# Copy the current directory contents into the container
ADD . /app

ENTRYPOINT ["/app/encode.sh"]
