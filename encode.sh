#!/usr/bin/env bash

set -euo pipefail
readonly START_TIME=$(date +%s)

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

detect_dolby_vision() {
    log "Detecting Dolby Vision..."
    local file="$1"

    # Use mediainfo to detect Dolby Vision
    local is_dv
    is_dv=$(mediainfo "$file" | grep "Dolby Vision" || true)

    if [[ -n "$is_dv" ]]; then
        log "Dolby Vision detected"
        # Set DV variables only once at the end
        declare -gr IS_DOLBY_VISION=true
    else
        log "Dolby Vision not detected. Continuing with standard encoding..."
        # Set DV variables only once at the end
        declare -gr IS_DOLBY_VISION=false
    fi

    return 0
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

    # Detect Dolby Vision
    detect_dolby_vision "$INPUT_PATH"
}

# Video processing functions
segment_video() {
    log "Segmenting video..."
    ffmpeg -i "$INPUT_PATH" \
        -c:v copy \
        -an \
        -map 0 \
        -segment_time 00:01:00 \
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

        local dv_params=""
        # Disabling Dolby Vision support for now until there's a way to direcly copy Dolby
        # Vision profile 10 metadata from source to output. Encoding each chunk seperately
        # apparently causes metadata corruption.
        # if [[ "$IS_DOLBY_VISION" == true ]]; then
        #     dv_params="--enc dolbyvision=true"
        # fi

        ab-av1 auto-encode \
            -e libsvtav1 \
            --svt tune=3 \
            $dv_params \
            --keyint 10s \
            --min-vmaf 92 \
            --preset 6 \
            --vmaf n_subsample=8:pool=harmonic_mean \
            --samples 3 \
            --sample-duration 1sec \
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

        # Determine optimal bitrate based on channel count
        local bitrate
        case $num_channels in
            1)  # Mono
                bitrate=64
                ;;
            2)  # Stereo
                bitrate=128
                ;;
            6)  # 5.1
                bitrate=320
                ;;
            8)  # 7.1
                bitrate=448
                ;;
            *)  # Default fallback
                bitrate=$((num_channels * 48))
                ;;
        esac

        log "Encoding audio track $i with $num_channels channels at ${bitrate}k"
        ffmpeg -i "$INPUT_PATH" \
            -map "a:$i" \
            -c:a libopus \
            -af aformat=channel_layouts="7.1|5.1|stereo|mono" \
            -application audio \
            -vbr on \
            -compression_level 10 \
            -frame_duration 20 \
            -b:a "${bitrate}k" \
            -avoid_negative_ts make_zero \
            "${WORKING_DIR}/audio-${i}.mkv" || {
                log_error "Failed to encode audio track $i"
                return 1
            }
    done

    log "Audio encoding completed successfully"

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

    log "Start time: $(date -d @${START_TIME} '+%Y-%m-%d %H:%M:%S')"
    log "Starting video encoding workflow"

    segment_video
    encode_segments
    concatenate_segments
    encode_audio
    remux_tracks

    cleanup

    # Add timing information
        END_TIME=$(date +%s)
        DURATION=$((END_TIME - START_TIME))

        # Convert seconds to hours, minutes, seconds
        HOURS=$((DURATION / 3600))
        MINUTES=$(((DURATION % 3600) / 60))
        SECONDS=$((DURATION % 60))

        log "Encoding workflow complete"
        log "Start time: $(date -d @${START_TIME} '+%Y-%m-%d %H:%M:%S')"
        log "End time: $(date -d @${END_TIME} '+%Y-%m-%d %H:%M:%S')"
        log "Duration: ${HOURS}h ${MINUTES}m ${SECONDS}s"
}

# Run main function
main
