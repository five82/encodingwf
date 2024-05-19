# encodingwf

# Use five82/encodingtools for our base build image
FROM ghcr.io/five82/encodingtools:latest

# Set the working directory to /app
WORKDIR /app

# Copy the current directory contents into the container
ADD . /app

ENTRYPOINT ["/app/encode.sh"]