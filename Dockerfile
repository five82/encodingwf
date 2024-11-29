# encodingwf

# Use five82/encodingtools-multiarch for our base build image
FROM ghcr.io/five82/encodingtools-multiarch:latest

WORKDIR /app

# Create a virtual environment
RUN python3 -m venv /opt/venv

# Make sure we use the virtualenv
ENV PATH="/opt/venv/bin:$PATH"

# Copy the current directory contents into the container
COPY . /app

# Install dependencies in the virtual environment
RUN pip install --no-cache-dir -r requirements.txt

ENTRYPOINT ["/app/encode.sh"]
