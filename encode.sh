#!/usr/bin/env bash

# encode.sh

# This is a work in progress.

# There are much better tools available for VMAF target quality chunked encoding
# such as Av1an. But I needed something faster.

# Credit to Reddit user asm-c for the idea and commands.
# https://www.reddit.com/r/ffmpeg/comments/14dk6zl/chunk_based_encoding_system_abav1/

# This script splits a video into chunks and uses ab-av1 to encode each chunk to a
# target quality value with VMAF. The chunks are reassembled and the audio tracks are
# encoded seperately and remuxed.


set -euo pipefail

#----------
# Constants
#----------
readonly START_TIME=$(date +%s)
readonly DIRS=(input working output segments encoded_segments logs)
readonly BASE_DIR="/app/videos"
readonly MIN_FILE_SIZE=1024  # 1KB minimum file size

#-----------------
# Global Variables
#-----------------
declare -g IS_DOLBY_VISION=false
declare -g CURRENT_FILE=""
declare -g INPUT_PATH=""
declare -g VID_FILE=""
declare -g NUM_AUDIO_TRACKS=0
declare -A video_start_times
declare -A video_end_times
declare -a processed_videos

#------------------
# Color Definitions
#------------------
source_colors() {
    declare -gr RED='\033[0;31m'
    declare -gr GREEN='\033[0;32m'
    declare -gr YELLOW='\033[1;33m'
    declare -gr BLUE='\033[0;34m'
    declare -gr LIGHTBLUE='\033[0;94m'
    declare -gr PURPLE='\033[0;35m'
    declare -gr CYAN='\033[0;36m'
    declare -gr NC='\033[0m'

    # Disable colors if not outputting to terminal
    if [[ ! -t 1 ]]; then
        declare -gr RED='' GREEN='' YELLOW='' BLUE=''
        declare -gr LIGHTBLUE='' PURPLE='' CYAN='' NC=''
    fi
}

#------------------
# Logging Functions
#------------------
setup_logging() {
    log "${PURPLE}Setting up logging${NC}"
    local log_file="${LOGS_DIR}/encode_$(date +%Y%m%d_%H%M%S).log"
    exec 1> >(tee -a "$log_file")
    exec 2> >(tee -a "$log_file" >&2)
    touch "$log_file"
    chmod 644 "$log_file"
}

log() {
    local file_info=""
    [[ -n "$CURRENT_FILE" ]] && file_info=" [${LIGHTBLUE}${CURRENT_FILE}${NC}]"
    echo -e "${BLUE}[$(date '+%Y-%m-%d %H:%M:%S')]${NC}${file_info} ${GREEN}$*${NC}"
}

error() {
    local file_info=""
    [[ -n "$CURRENT_FILE" ]] && file_info=" [${LIGHTBLUE}${CURRENT_FILE}${NC}]"
    echo -e "${BLUE}[$(date '+%Y-%m-%d %H:%M:%S')]${NC}${file_info} ${RED}ERROR: $*${NC}" >&2
    exit 1
}

warn() {
    local file_info=""
    [[ -n "$CURRENT_FILE" ]] && file_info=" [${LIGHTBLUE}${CURRENT_FILE}${NC}]"
    echo -e "${BLUE}[$(date '+%Y-%m-%d %H:%M:%S')]${NC}${file_info} ${YELLOW}WARNING: $*${NC}"
}

log_summary() {
    echo -e "${BLUE}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} ${GREEN}$*${NC}"
}

#---------------------
# Validation Functions
#---------------------
validate_video_file() {
    local file="$1"
    local step="$2"

    [[ ! -f "$file" ]] && error "$step: File not found: $file"

    local file_size
    file_size=$(stat -f%z "$file" 2>/dev/null || stat -c%s "$file")
    [[ $file_size -lt $MIN_FILE_SIZE ]] && error "$step: File too small: $file"

    ffprobe -v error "$file" 2>&1 || error "$step: Invalid video file: $file"
}

validate_segments() {
    log "${PURPLE}Validating segments${NC}"
    local dir="$1"
    local min_segments=1

    local segment_count
    segment_count=$(find "$dir" -name "*.mkv" -type f | wc -l)

    [[ $segment_count -lt $min_segments ]] && error "No segments found in $dir"

    local invalid_segments=0
    while IFS= read -r -d $'\0' segment; do
        if ! validate_video_file "$segment" "Segment validation" >/dev/null 2>&1; then
            log "${YELLOW}Warning: Invalid segment found: $segment${NC}"
            ((invalid_segments++))
        fi
    done < <(find "$dir" -name "*.mkv" -type f -print0)

    [[ $invalid_segments -gt 0 ]] && error "Found $invalid_segments invalid segments"

    log "${CYAN}Successfully validated $segment_count segments${NC}"
}

validate_audio_tracks() {
    log "${PURPLE}Validating audio tracks${NC}"
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

    log "${CYAN}Successfully validated $expected_tracks audio tracks${NC}"
}

#---------------------
# Processing Functions
#---------------------
detect_dolby_vision() {
    log "${PURPLE}Detecting Dolby Vision...${NC}"
    local file="$1"

    # Use mediainfo to detect Dolby Vision
    local is_dv
    is_dv=$(mediainfo "$file" | grep "Dolby Vision" || true)

    if [[ -n "$is_dv" ]]; then
        log "${LIGHTBLUE}Dolby Vision detected${NC}"
        IS_DOLBY_VISION=true
    else
        log "${LIGHTBLUE}Dolby Vision not detected. Continuing with standard encoding...${NC}"
        IS_DOLBY_VISION=false
    fi

    return 0
}

segment_video() {
    log "${PURPLE}Segmenting video...${NC}"
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
    log "${PURPLE}Encoding segments...${NC}"
    local segment_count=0
    local total_segments
    total_segments=$(find "${SEGMENTS_DIR}" -name "*.mkv" -type f | wc -l)
    log "${PURPLE}Total segments to encode: $total_segments${NC}"
    log "${PURPLE}Segments directory: $SEGMENTS_DIR${NC}"

    cd "$SEGMENTS_DIR"
    # Check if any .mkv files exist first
    shopt -s nullglob  # Handle no matches gracefully
    local files=()
    while IFS= read -r -d $'\0'; do
        files+=("$REPLY")
    done < <(find . -maxdepth 1 -name "*.mkv" -print0)

    if [ ${#files[@]} -eq 0 ]; then
        error "No mkv files found in $SEGMENTS_DIR"
        exit 1
    fi

    for f in "${files[@]}"; do
        log "${LIGHTBLUE}Found file - $f${NC}"
        segment_count=$((segment_count + 1)) || { log "${RED}Error: Failed to increment segment_count${NC}"; exit 1; }
        log "${PURPLE}Encoding segment $segment_count of $total_segments: $f${NC}"

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
            --svt film-grain=8 \
            --svt film-grain-denoise=1 \
            --svt adaptive-film-grain=1 \
            $dv_params \
            --keyint 10s \
            --min-vmaf 92 \
            --preset 6 \
            --vmaf n_subsample=8:pool=harmonic_mean \
            --samples 3 \
            --sample-duration 1sec \
            --verbose \
            --input "$f" \
            --output "${ENCODED_SEGMENTS_DIR}/$(basename "$f")"

        # Validate encoded segment
        validate_video_file "${ENCODED_SEGMENTS_DIR}/$(basename "$f")" "Segment encoding"
    done

    # Validate all encoded segments
    validate_segments "$ENCODED_SEGMENTS_DIR"
}

encode_audio() {
    log "${PURPLE}Encoding audio tracks${NC}"
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
                bitrate=256
                ;;
            8)  # 7.1
                bitrate=384
                ;;
            *)  # Default fallback
                bitrate=$((num_channels * 48))
                ;;
        esac

        local output_file="${WORKING_DIR}/audio-${i}.mkv"
        log "${PURPLE}Encoding audio track $i with $num_channels channels at ${bitrate}k${NC}"
        ffmpeg -i "$INPUT_PATH" \
            -map "a:$i" \
            -c:a libopus \
            -af "aformat=channel_layouts=7.1|5.1|stereo|mono" \
            -application audio \
            -vbr on \
            -compression_level 10 \
            -frame_duration 20 \
            -b:a "${bitrate}k" \
            -avoid_negative_ts make_zero \
            "$output_file" || {
                error "Failed to encode audio track $i"
                return 1
            }
    done

    log "${LIGHTBLUE}Audio encoding completed successfully${NC}"

    # Validate all audio tracks
    validate_audio_tracks "$WORKING_DIR" "$NUM_AUDIO_TRACKS"
}

concatenate_segments() {
    log "${PURPLE}Concatenating segments${NC}"

    # Create concat file separately without logging
    local concat_file="${WORKING_DIR}/concat.txt"

    # Use find with -print0 and while read to handle spaces in filenames
    while IFS= read -r -d $'\0' f; do
        printf "file '%s'\n" "$f" >> "$concat_file"
    done < <(find "${ENCODED_SEGMENTS_DIR}" -name "*.mkv" -print0 | sort -z)

    # Use the concat file instead of inline generation
    ffmpeg -f concat -safe 0 \
        -i "$concat_file" \
        -c copy "${WORKING_DIR}/${VID_FILE}.mkv"

    # Clean up concat file
    rm -f "$concat_file"

    # Validate concatenated file
    log "${PURPLE}Validate concatenated file${NC}"
    validate_video_file "${WORKING_DIR}/${VID_FILE}.mkv" "Concatenation"
}

remux_tracks() {
    log "${PURPLE}Remuxing tracks${NC}"
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

#------------------
# Utility Functions
#------------------
config() {
    for dir in "${DIRS[@]}"; do
        declare -gr "${dir^^}_DIR"="$BASE_DIR/$dir"
    done
}

prepare_directories() {
    log "${PURPLE}Preparing working directories for new source video${NC}"
    mkdir -p "$SEGMENTS_DIR" "$ENCODED_SEGMENTS_DIR" "$WORKING_DIR"
}

cleanup() {
    log "${PURPLE}Cleaning up temporary files${NC}"
    rm -rf "$SEGMENTS_DIR" "$ENCODED_SEGMENTS_DIR" "$WORKING_DIR"
}

#------------------
# Summary Functions
#------------------
print_summary() {
    local END_TIME=$(date +%s)
    local DURATION=$((END_TIME - START_TIME))
    local HOURS=$((DURATION / 3600))
    local MINUTES=$(((DURATION % 3600) / 60))
    local SECONDS=$((DURATION % 60))

    # Print summary
    log_summary "${CYAN}=== Encoding Summary ===${NC}"
    log_summary "${YELLOW}Overall Process${NC}"
    log_summary "${CYAN}Start time: $(date -d @${START_TIME} '+%Y-%m-%d %H:%M:%S')${NC}"
    log_summary "${CYAN}End time: $(date -d @${END_TIME} '+%Y-%m-%d %H:%M:%S')${NC}"
    log_summary "${CYAN}Total Duration: ${HOURS}h ${MINUTES}m ${SECONDS}s${NC}"

    log_summary "${YELLOW}Individual Video Processing Times:${NC}"
    for video in "${processed_videos[@]}"; do
        local vid_start=${video_start_times[$video]}
        local vid_end=${video_end_times[$video]}
        local vid_duration=$((vid_end - vid_start))

        # Convert video duration to hours, minutes, seconds
        local vid_hours=$((vid_duration / 3600))
        local vid_minutes=$(((vid_duration % 3600) / 60))
        local vid_seconds=$((vid_duration % 60))

        log_summary "${LIGHTBLUE}$video:${NC}"
        log_summary "  ${CYAN}Start: $(date -d @${vid_start} '+%Y-%m-%d %H:%M:%S')${NC}"
        log_summary "  ${CYAN}End: $(date -d @${vid_end} '+%Y-%m-%d %H:%M:%S')${NC}"
        log_summary "  ${CYAN}Duration: ${vid_hours}h ${vid_minutes}m ${vid_seconds}s${NC}"
    done

    log_summary "${YELLOW}Encoding workflow complete${NC}"
}

#-----------------------
# Main Process Functions
#-----------------------
process_single_file() {
    local input_path="$1"
    local current_file="$2"
    local total_files="$3"

    # Set global variables
    INPUT_PATH="$input_path"
    VID_FILE=$(basename "$input_path" .mkv)
    CURRENT_FILE="$VID_FILE"
    NUM_AUDIO_TRACKS=$(ffprobe -v error -select_streams a \
        -show_entries stream=index -of csv=p=0 "$input_path" | wc -l)

    video_start_times["$VID_FILE"]=$(date +%s)
    processed_videos+=("$VID_FILE")

    log "${CYAN}Processing file $current_file of $total_files: $input_path${NC}"

    # Process steps
    prepare_directories
    detect_dolby_vision "$INPUT_PATH"
    segment_video
    encode_segments
    concatenate_segments
    encode_audio
    remux_tracks
    cleanup

    video_end_times["$VID_FILE"]=$(date +%s)
    log "${GREEN}Completed processing: $input_path${NC}"
}

init() {
    source_colors
    config
    setup_logging

    # Create required directories
    log "${PURPLE}Creating required directories${NC}"
    for dir in "${DIRS[@]}"; do
        mkdir -p "${BASE_DIR}/${dir}"
    done
}

main() {
    init

    log "${CYAN}Start time: $(date -d @${START_TIME} '+%Y-%m-%d %H:%M:%S')${NC}"
    log "${PURPLE}Starting video encoding workflow${NC}"

    # Find and process files
    local input_files=()
    while IFS= read -r -d $'\0'; do
        input_files+=("$REPLY")
    done < <(find "${INPUT_DIR}" -maxdepth 1 -name "*.mkv" -print0)

    local total_files=${#input_files[@]}
    [[ $total_files -eq 0 ]] && error "No input files found in $INPUT_DIR"

    log "${CYAN}Found $total_files files to process${NC}"

    local current_file=0
    for input_path in "${input_files[@]}"; do
        current_file=$((current_file + 1))
        process_single_file "$input_path" "$current_file" "$total_files"
    done

    print_summary
}

# Execute main function
main
