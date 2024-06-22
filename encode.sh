#!/usr/bin/env bash

# encoding workflow

# variables
input_dir="/app/videos/input"
input_file="input.mkv"
working_dir="/app/videos/working"
working_file="working.mkv"
output_dir="/app/videos/output"
output_file="output.mkv"
segment_dir="/app/videos/segments"
encoded_segment_dir="/app/videos/encoded-segments"

# functions
segment_video() {
    ffmpeg \
        -i "$input_dir/$input_file" \
        -c:v copy \
        -an \
        -map 0 \
        -segment_time 00:02:00 \
        -f segment \
        -reset_timestamps 1 \
        "$segment_dir"/%04d.mkv
}

encode_segments() {
    cd "$segment_dir"
    for f in *.mkv; do
        ab-av1 \
            auto-encode \
            -e libsvtav1 \
            --svt tune=0 \
            --keyint 5s \
            --min-vmaf 93 \
            --preset 4 \
            --vmaf n_subsample=4 \
            --sample-every "1m" \
            --enc fps_mode=passthrough \
            --input "$f" \
            --output "$encoded_segment_dir"/"$(basename "$f")"
    done
}

concatenate_segments() {
    ffmpeg \
        -f concat \
        -safe 0 \
        -i <(for f in "$encoded_segment_dir"/*.mkv; do echo "file '$f'"; done) \
        -c copy "$working_dir/$working_file"
}

encode_audio() {
    num_tracks=$(ffprobe \
        -v error \
        -select_streams a \
        -show_entries stream=index \
        -of csv=p=0 \
        "$input_dir/$input_file" \
        | wc -l)
    for ((i=0; i<num_tracks; i++)); do
        num_channels=$(ffprobe \
            -v error \
            -select_streams "a:$i" \
            -show_entries stream=channels \
            -of csv=p=0 \
            "$input_dir/$input_file")
        bitrate=$((num_channels * 64))
        ffmpeg \
            -i "$input_dir/$input_file" \
            -map "0:a:$i" \
            -c:a libopus \
            -af aformat=channel_layouts="7.1|5.1|stereo|mono" \
            -b:a "${bitrate}k" \
            "$working_dir/$working_file-audio-$i.mkv"
    done
}

remux_tracks() {
    ffmpeg \
        -i "$working_dir/$working_file" \
            "$(for ((i=0; i<num_tracks; i++)); \
                do echo "-i $working_dir/$working_file-audio-$i.mkv"; \
                done)" \
            "$(for ((i=0; i<num_tracks; i++)); \
                do echo "-map $i:a"; \
                done)" \
            "$(if ffprobe -i "$input_dir/$input_file" \
                -show_chapters \
                -v quiet \
                -of csv=p=0; \
                then echo "-map 0:c"; \
            fi)" \
            "$(if ffprobe -i "$input_dir/$input_file" \
                -show_entries stream=index:stream_tags=language \
                -select_streams s \
                -v quiet \
                -of csv=p=0; \
                then echo "-map 0:s"; \
            fi)" \
        -c copy \
        "$output_dir/$output_file"
}

# create required directories
mkdir -p \
    "$input_dir" \
    "$segment_dir" \
    "$working_dir" \
    "$encoded_segment_dir" \
    "$output_dir"

# segment, encode, and remux
echo "Begin segmenting video"
segment_video
echo "Begin encoding segments"
encode_segments
echo "Begin concatenating segments"
concatenate_segments
echo "Begin encoding audio"
encode_audio
echo "Begin remuxing tracks"
remux_tracks
echo "Encoding complete"

# cleanup
rm -rf \
    "$segment_dir" \
    "$encoded_segment_dir" \
    "$working_dir"