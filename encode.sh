#!/usr/bin/env bash

# Encoding workflow

# create required directories
mkdir -p /app/videos/input \
  /app/videos/segments \
  /app/videos/encoded-segments \
  /app/videos/output

# segment the input video
ffmpeg -i /app/videos/input/input.mkv -c:v copy -an -map 0 -segment_time 00:03:00 -f segment -reset_timestamps 1 /app/videos/segments/%04d.mkv

# encode the segments
for i in segments/*.mkv; do
  ab-av1 auto-encode -e libsvtav1 --svt tune=0 --keyint 5s --min-vmaf 93 --preset 4 --vmaf n_threads=32:n_subsample=3 --samples 3 --enc fps_mode=passthrough --input "$i" --output /app/videos/encoded-segments/"$i"
done

# concatenate the encoded segments
ffmpeg -f concat -safe 0 -i <(for f in /app/videos/encoded-segments/*.mkv; do echo "file '$f'"; done) -c copy /app/videos/output/output.mkv