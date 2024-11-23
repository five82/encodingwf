#!/usr/bin/env bash

set -euo pipefail

# Constants
readonly DIRS=(
    input
    working
    output
    segments
    encoded_segments
    logs
)
readonly BASE_DIR="/app/videos"
readonly MIN_FILE_SIZE=1024  # 1KB minimum file size

# Configuration
config() {
    for dir in "${DIRS[@]}"; do
        declare -gr "${dir^^}_DIR"="$BASE_DIR/$dir"
    done
}

# Logging
setup_logging() {
    log "SETUP LOGGING"
    exec 1> >(tee -a "${LOGS_DIR}/encode_$(date +%Y%m%d_%H%M%S).log")
    exec 2>&1
}

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

error() {
    log "ERROR: $*" >&2
    exit 1
}

# Validation functions
validate_video_file() {
    log "VALIDATE VIDEO FILE"
    local file="$1"
    local step="$2"

    [[ ! -f "$file" ]] && error "$step: File not found: $file"

    local file_size
    file_size=$(stat -f%z "$file" 2>/dev/null || stat -c%s "$file")
    [[ $file_size -lt $MIN_FILE_SIZE ]] && error "$step: File too small (possibly corrupt): $file"

    # Check if file is valid video
    if ! ffprobe -v error "$file" 2>&1; then
        error "$step: Invalid video file: $file"
    fi
}

validate_segments() {
    log "VALIDATE SEGMENTS"
    local dir="$1"
    local min_segments=1

    local segment_count
    segment_count=$(find "$dir" -name "*.mkv" -type f | wc -l)

    [[ $segment_count -lt $min_segments ]] && error "No segments found in $dir"

    local invalid_segments=0
    while IFS= read -r segment; do
        if ! validate_video_file "$segment" "Segment validation" >/dev/null 2>&1; then
            log "Warning: Invalid segment found: $segment"
            ((invalid_segments++))
        fi
    done < <(find "$dir" -name "*.mkv" -type f)

    [[ $invalid_segments -gt 0 ]] && error "Found $invalid_segments invalid segments"

    log "Successfully validated $segment_count segments"
}

validate_audio_tracks() {
    log "VALIDATE AUDIO TRACKS"
    local base_path="$1"
    local expected_tracks="$2"

    for ((i=0; i<expected_tracks; i++)); do
        local audio_file="${base_path}/audio-${i}.mkv"

        [[ ! -f "$audio_file" ]] && error "Audio track $i missing: $audio_file"

        # Verify audio stream exists
        if ! ffprobe -v error -select_streams a -show_entries stream=codec_type -of csv=p=0 "$audio_file" | grep -q "audio"; then
            error "No audio stream found in track $i: $audio_file"
        fi
    done

    log "Successfully validated $expected_tracks audio tracks"
}

# Initialization
init() {
    # Create required directories
    echo "CREATING REQUIRED DIRECTORIES"
    for dir in "${DIRS[@]}"; do
        mkdir -p "${BASE_DIR}/${dir}"
    done

    # Find input file
    log "FINDING INPUT FILE"
    local input_path
    input_path=$(find "$INPUT_DIR" -type f | head -n 1)
    if [[ -z "$input_path" ]]; then
        error "No input file found in $INPUT_DIR"
    fi
    log "Found input file: $input_path"

    # Validate input file
    validate_video_file "$input_path" "Input validation"

    # Set global variables
    declare -gr INPUT_PATH="$input_path"
    declare -gr VID_FILE=$(basename "$input_path" .mkv)
    declare -gr NUM_AUDIO_TRACKS=$(ffprobe -v error -select_streams a \
        -show_entries stream=index -of csv=p=0 "$input_path" | wc -l)
}

# Video processing functions
segment_video() {
    log "Segmenting video..."
    ffmpeg -i "$INPUT_PATH" \
        -c:v copy \
        -an \
        -map 0 \
        -segment_time 00:02:00 \
        -f segment \
        -reset_timestamps 1 \
        "${SEGMENTS_DIR}/%04d.mkv"

    # Validate segments
    validate_segments "$SEGMENTS_DIR"
}

encode_segments() {
    log "Encoding segments..."
    local segment_count=0
    local total_segments=$(find "${SEGMENTS_DIR}" -name "*.mkv" | wc -l)
    log "Total segments to encode: $total_segments"
    log "Segments directory: $SEGMENTS_DIR"

    cd "$SEGMENTS_DIR"
    # Check if any .mkv files exist first
    shopt -s nullglob  # Handle no matches gracefully
    files=(*.mkv)
    if [ ${#files[@]} -eq 0 ]; then
        error "No mkv files found in $SEGMENTS_DIR"
        exit 1
    fi

    for f in *.mkv; do
        log "Found file - $f"
        segment_count=$((segment_count + 1)) || { log "Error: Failed to increment segment_count"; exit 1; }
        log "Encoding segment $segment_count of $total_segments: $f"

        ab-av1 auto-encode \
            -e libsvtav1 \
            --svt tune=0 \
            --keyint 5s \
            --min-vmaf 93 \
            --preset 4 \
            --vmaf n_subsample=4:pool=harmonic_mean \
            --samples 9 \
            --sample-duration 1sec \
            --enc fps_mode=passthrough \
            --input "$f" \
            --output "${ENCODED_SEGMENTS_DIR}/$(basename "$f")"

        # Validate encoded segment
        validate_video_file "${ENCODED_SEGMENTS_DIR}/$(basename "$f")" "Segment encoding"
    done

    # Validate all encoded segments
    validate_segments "$ENCODED_SEGMENTS_DIR"
}

concatenate_segments() {
    log "Concatenating segments..."
    ffmpeg -f concat -safe 0 \
        -i <(for f in "${ENCODED_SEGMENTS_DIR}"/*.mkv; do echo "file '$f'"; done) \
        -c copy "${WORKING_DIR}/${VID_FILE}.mkv"

    # Validate concatenated file
    log "Validate concatenated file"
    validate_video_file "${WORKING_DIR}/${VID_FILE}.mkv" "Concatenation"
}

encode_audio() {
    log "Encoding audio tracks..."
    for ((i=0; i<NUM_AUDIO_TRACKS; i++)); do
        local num_channels
        num_channels=$(ffprobe -v error -select_streams "a:$i" \
            -show_entries stream=channels -of csv=p=0 "$INPUT_PATH")
        local bitrate=$((num_channels * 64))

        log "Encoding audio track $i with $num_channels channels at ${bitrate}k"
        ffmpeg -i "$INPUT_PATH" \
            -map "a:$i" \
            -c:a libopus \
            -af aformat=channel_layouts="7.1|5.1|stereo|mono" \
            -b:a "${bitrate}k" \
            "${WORKING_DIR}/audio-${i}.mkv"
    done

    # Validate all audio tracks
    validate_audio_tracks "$WORKING_DIR" "$NUM_AUDIO_TRACKS"
}

remux_tracks() {
    log "Remuxing tracks..."
    local -a input_files=("${WORKING_DIR}/${VID_FILE}.mkv")
    local -a ffmpeg_cmd=(ffmpeg)

    # Add audio files to input array
    for ((i=0; i<NUM_AUDIO_TRACKS; i++)); do
        local audio_file="${WORKING_DIR}/audio-${i}.mkv"
        if [[ ! -f "$audio_file" ]]; then
            error "Audio file $audio_file not found"
        fi
        input_files+=("$audio_file")
    done

    # Build ffmpeg command
    for file in "${input_files[@]}"; do
        ffmpeg_cmd+=(-i "$file")
    done

    # Add mapping
    for ((i=0; i<NUM_AUDIO_TRACKS; i++)); do
        ffmpeg_cmd+=(-map "$i:a?")
    done

    # Add output file
    ffmpeg_cmd+=(-c copy "${OUTPUT_DIR}/${VID_FILE}.mkv")

    # Execute command
    "${ffmpeg_cmd[@]}"

    # Validate final output
    validate_video_file "${OUTPUT_DIR}/${VID_FILE}.mkv" "Final output"

    # Validate audio tracks in final output
    local final_audio_tracks
    final_audio_tracks=$(ffprobe -v error -select_streams a \
        -show_entries stream=index -of csv=p=0 "${OUTPUT_DIR}/${VID_FILE}.mkv" | wc -l)

    if [[ $final_audio_tracks -ne $NUM_AUDIO_TRACKS ]]; then
        error "Final output has $final_audio_tracks audio tracks, expected $NUM_AUDIO_TRACKS"
    fi
}

cleanup() {
    log "Cleaning up temporary files..."
    rm -rf "$SEGMENTS_DIR" "$ENCODED_SEGMENTS_DIR" "$WORKING_DIR"
}

main() {
    config
    setup_logging
    init

    log "Starting video encoding workflow"

    segment_video
    encode_segments
    concatenate_segments
    encode_audio
    remux_tracks

    cleanup

    log "Encoding workflow complete"
}

# Run main function
main
