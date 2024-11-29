#!/usr/bin/env python3

import subprocess
from pathlib import Path
from typing import List, Optional, Dict
import shutil
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from ..utils.logging_config import ContextLogger
from .exceptions import (
    ValidationError,
    FileSizeError,
    InvalidVideoError,
    FileNotFoundError,
    ProcessError
)
from ..config import EncoderConfig

class VideoValidator:
    """Handles validation of video files and segments"""

    def __init__(self, config: EncoderConfig, logger: ContextLogger):
        self.config = config
        self.logger = logger

    def check_ffmpeg_installed(self) -> None:
        """Verify ffmpeg and required tools are installed"""
        required_tools = ['ffmpeg', 'ffprobe', 'mediainfo']

        for tool in required_tools:
            if not shutil.which(tool):
                raise ValidationError(f"Required tool '{tool}' not found in PATH")

    def validate_video_file(self, file: Path, step: str) -> None:
        """
        Validate a single video file

        Args:
            file: Path to video file
            step: Current processing step (for error context)

        Raises:
            FileNotFoundError: If file doesn't exist
            FileSizeError: If file is too small
            InvalidVideoError: If file is not a valid video
        """
        self.logger.debug(f"Validating file: {file} (step: {step})")

        # Check file exists
        if not file.is_file():
            raise FileNotFoundError(f"{step}: File not found", file)

        # Check file size
        file_size = file.stat().st_size
        if file_size < self.config.MIN_FILE_SIZE:
            raise FileSizeError(file, file_size, self.config.MIN_FILE_SIZE)

        # Check file validity with ffprobe
        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'error', str(file)],
                capture_output=True,
                text=True,
                check=True
            )
        except subprocess.CalledProcessError as e:
            raise InvalidVideoError(
                f"{step}: Invalid video file - ffprobe output: {e.stderr}",
                file
            )

        self.logger.debug(f"Successfully validated: {file}")

    def validate_segments(self, directory: Path, min_segments: int = 1) -> None:
        """
        Validate all video segments in a directory

        Args:
            directory: Directory containing segments
            min_segments: Minimum number of expected segments

        Raises:
            ValidationError: If segment validation fails
        """
        self.logger.info("Validating segments")

        # Get all mkv files in directory
        segments = list(directory.glob("*.mkv"))

        if len(segments) < min_segments:
            raise ValidationError(
                f"Expected at least {min_segments} segments, found {len(segments)}"
            )

        # Validate segments in parallel
        with ThreadPoolExecutor() as executor:
            validate_func = partial(self.validate_video_file, step="Segment validation")
            results = list(executor.map(validate_func, segments))

        self.logger.info(f"Successfully validated {len(segments)} segments")

    def validate_audio_tracks(
        self,
        base_path: Path,
        expected_tracks: int,
        working_dir: Path
    ) -> None:
        """
        Validate audio tracks after encoding

        Args:
            base_path: Original input file path
            expected_tracks: Number of expected audio tracks
            working_dir: Directory containing encoded audio tracks

        Raises:
            ValidationError: If audio validation fails
        """
        self.logger.info("Validating audio tracks")

        for i in range(expected_tracks):
            audio_file = working_dir / f"audio-{i}.mkv"

            # Check file exists
            if not audio_file.is_file():
                raise FileNotFoundError(
                    f"Audio track {i} missing",
                    audio_file
                )

            # Verify audio stream exists
            try:
                result = subprocess.run(
                    [
                        'ffprobe',
                        '-v', 'error',
                        '-select_streams', 'a',
                        '-show_entries', 'stream=codec_type',
                        '-of', 'csv=p=0',
                        str(audio_file)
                    ],
                    capture_output=True,
                    text=True,
                    check=True
                )

                if 'audio' not in result.stdout:
                    raise ValidationError(
                        f"No audio stream found in track {i}",
                        audio_file
                    )

            except subprocess.CalledProcessError as e:
                raise ProcessError(
                    f"Failed to probe audio file",
                    e.cmd,
                    e.returncode,
                    e.stdout,
                    e.stderr
                )

        self.logger.info(f"Successfully validated {expected_tracks} audio tracks")

    def validate_final_output(
        self,
        output_file: Path,
        expected_audio_tracks: int
    ) -> None:
        """
        Validate final output file after remuxing

        Args:
            output_file: Path to final output file
            expected_audio_tracks: Expected number of audio tracks

        Raises:
            ValidationError: If final output validation fails
        """
        self.logger.info("Validating final output")

        # Validate video file
        self.validate_video_file(output_file, "Final output")

        # Check audio track count
        try:
            result = subprocess.run(
                [
                    'ffprobe',
                    '-v', 'error',
                    '-select_streams', 'a',
                    '-show_entries', 'stream=index',
                    '-of', 'csv=p=0',
                    str(output_file)
                ],
                capture_output=True,
                text=True,
                check=True
            )

            actual_tracks = len(result.stdout.splitlines())
            if actual_tracks != expected_audio_tracks:
                raise ValidationError(
                    f"Final output has {actual_tracks} audio tracks, "
                    f"expected {expected_audio_tracks}",
                    output_file
                )

        except subprocess.CalledProcessError as e:
            raise ProcessError(
                "Failed to probe final output",
                e.cmd,
                e.returncode,
                e.stdout,
                e.stderr
            )

        self.logger.info("Final output validation successful")

    def get_video_info(self, file: Path) -> Dict:
        """
        Get video file information using ffprobe

        Args:
            file: Path to video file

        Returns:
            Dict containing video information

        Raises:
            ProcessError: If ffprobe fails
        """
        try:
            result = subprocess.run(
                [
                    'ffprobe',
                    '-v', 'quiet',
                    '-print_format', 'json',
                    '-show_format',
                    '-show_streams',
                    str(file)
                ],
                capture_output=True,
                text=True,
                check=True
            )

            import json
            return json.loads(result.stdout)

        except subprocess.CalledProcessError as e:
            raise ProcessError(
                "Failed to get video information",
                e.cmd,
                e.returncode,
                e.stdout,
                e.stderr
            )

    def get_audio_track_count(self, file: Path) -> int:
        """Get number of audio tracks in video file"""
        try:
            result = subprocess.run(
                [
                    'ffprobe',
                    '-v', 'error',
                    '-select_streams', 'a',
                    '-show_entries', 'stream=index',
                    '-of', 'csv=p=0',
                    str(file)
                ],
                capture_output=True,
                text=True,
                check=True
            )

            return len(result.stdout.splitlines())

        except subprocess.CalledProcessError as e:
            raise ProcessError(
                "Failed to get audio track count",
                e.cmd,
                e.returncode,
                e.stdout,
                e.stderr
            )
