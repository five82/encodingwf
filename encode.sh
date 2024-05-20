#!/usr/bin/env bash

# encoding workflow


# functions
segment_video() {
    ffmpeg \
        -i /app/videos/input/input.mkv \
        -c:v copy \
        -an \
        -map 0 \
        -segment_time 00:01:00 \
        -f segment \
        -reset_timestamps 1 \
        /app/videos/segments/%04d.mkv
}

encode_segments() {
    cd /app/videos/segments
    for f in *.mkv; do
        /app/ab-av1 \
            auto-encode \
            -e libsvtav1 \
            --svt tune=0 \
            --keyint 5s \
            --min-vmaf 93 \
            --preset 4 \
            --vmaf n_threads=32:n_subsample=3 \
            --samples 3 \
            --enc fps_mode=passthrough \
            --input "$f" \
            --output /app/videos/encoded-segments/"$(basename "$f")"
    done
}

concatenate_segments() {
    ffmpeg \
        -f concat \
        -safe 0 \
        -i <(for f in /app/videos/encoded-segments/*.mkv; do echo "file '$f'"; done) \
        -c copy /app/videos/output/output.mkv
}

encode_audio() {
    input_file="/app/videos/input/input.mkv"
    num_tracks=$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$input_file" | wc -l)
    for ((i=0; i<num_tracks; i++)); do
        num_channels=$(ffprobe -v error -select_streams "a:$i" -show_entries stream=channels -of csv=p=0 "$input_file")
        bitrate=$((num_channels * 64))
        ffmpeg \
            -i "$input_file" \
            -map "0:a:$i" \
            -c:a libopus \
            -af aformat=channel_layouts="7.1|5.1|stereo|mono" \
            -b:a "${bitrate}k" \
            "/app/videos/output/output-audio-$i.mkv"
    done
}

remux_tracks() {
    ffmpeg \
        -i /app/videos/output/output.mkv \
        $(for ((i=0; i<num_tracks; i++)); do echo "-i /app/videos/output/output-audio-$i.mkv"; done) \
        $(for ((i=0; i<num_tracks; i++)); do echo "-map $i:a"; done) \
        $(if ffprobe -i /app/videos/input/input.mkv -show_chapters -v quiet -of csv=p=0; then echo "-map 0:c"; fi) \
        $(if ffprobe -i /app/videos/input/input.mkv -show_entries stream=index:stream_tags=language -select_streams s -v quiet -of csv=p=0; then echo "-map 0:s"; fi) \
        -c copy \
        /app/videos/output/finaloutput.mkv
        rm -rf /app/videos/output/output-audio-*.mkv
        rm -rf /app/videos/output/output.mkv
}


# create required directories
mkdir -p /app/videos/input \
  /app/videos/segments \
  /app/videos/encoded-segments \
  /app/videos/output

# segment, encode, and remux
segment_video
encode_segments
concatenate_segments
encode_audio
remux_tracks

# cleanup
rm -rf /app/videos/segments \
  /app/videos/encoded-segments