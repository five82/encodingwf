#!/usr/bin/env bash

# encoding workflow

# variables
input_dir="/app/videos/input"
working_dir="/app/videos/working"
output_dir="/app/videos/output"
segment_dir="/app/videos/segments"
encoded_segment_dir="/app/videos/encoded-segments"
log_dir="/app/videos/logs"

# Dynamically read the first filename in the input directory
input_path=$(find "$input_dir" -type f | head -n 1)
if [ -z "$input_path" ]; then
    echo "No input file found in $input_dir."
    exit 1
fi

# Extract the base filename without the directory and extension
vid_file=$(basename "$input_path" .mkv)

num_audio_tracks=$(ffprobe \
    -v error \
    -select_streams a \
    -show_entries stream=index \
    -of csv=p=0 \
    "$input_path" \
    | wc -l)

# functions
segment_video() {
    ffmpeg \
        -i "$input_path" \
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
            --vmaf n_subsample=4:pool=harmonic_mean \
            --samples 3 \
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
        -c copy "$working_dir/$vid_file"
}

encode_audio() {
    for ((i=0; i<num_audio_tracks; i++)); do
        num_audio_channels=$(ffprobe \
            -v error \
            -select_streams "a:$i" \
            -show_entries stream=channels \
            -of csv=p=0 \
            "$input_path")
        bitrate=$((num_audio_channels * 64))
        echo "Encoding audio track $i with $num_audio_channels channels at ${bitrate}k"
        ffmpeg \
            -i "$input_path" \
            -map "0:a:$i" \
            -c:a libopus \
            -af aformat=channel_layouts="7.1|5.1|stereo|mono" \
            -b:a "${bitrate}k" \
            "$working_dir/audio-$i.mkv"
    done
}

remux_tracks() {
    chapters_exist=$(ffprobe -i "$input_path" -show_chapters -v quiet -of csv=p=0 | wc -l)
    subtitles_exist=$(ffprobe -i "$input_path" -show_entries stream=index:stream_tags=language -select_streams s -v quiet -of csv=p=0 | wc -l)

    map_chapters=""
    if [ "$chapters_exist" -gt 0 ]; then
        map_chapters="-map 0:c"
    fi

    map_subtitles=""
    if [ "$subtitles_exist" -gt 0 ]; then
        for ((i=0; i<num_subtitle_tracks; i++)); do
            map_subtitles+=" -map $i:s"
        done
    fi

    input_files=("$working_dir/${vid_file}")
    for ((i=0; i<num_audio_tracks; i++)); do
        audio_file="${working_dir}/audio-${i}.mkv"
        if [ -f "$audio_file" ]; then
            input_files+=("$audio_file")
        else
            echo "Audio file $audio_file not found." >&2
            exit 1
        fi
    done

    ffmpeg_cmd=(ffmpeg)
    for vid_file in "${input_files[@]}"; do
        ffmpeg_cmd+=(-i "$vid_file")
    done
    for ((i=0; i<num_audio_tracks; i++)); do
        ffmpeg_cmd+=(-map "$i:a?")
    done
    ffmpeg_cmd+=(-c copy "${output_dir}/${vid_file}")

    "${ffmpeg_cmd[@]}"
}

# create required directories
mkdir -p \
    "$input_dir" \
    "$segment_dir" \
    "$working_dir" \
    "$encoded_segment_dir" \
    "$output_dir" \
    "$log_dir"

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