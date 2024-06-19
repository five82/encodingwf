#!/usr/bin/env bash

mkdir -p $HOME/videos/input
docker run --privileged -it --rm -v $HOME/videos:/app/videos encodingwf