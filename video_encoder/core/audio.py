#!/usr/bin/env python3

from ..utils.logging_config import ContextLogger

import subprocess
import logging
from pathlib import Path
from typing import List, Dict, Optional
import json

from ..config import EncoderConfig
from ..utils.exceptions import (
    AudioError,
    ProcessError,
    RemuxError,
)
from ..utils.validation import VideoValidator
from ..utils.subprocess import run_command

logger = logging.getLogger()

class AudioProcessor:
    """Handles all audio-specific processing operations"""

    def __init__(self, config: EncoderConfig, logger: ContextLogger):
            self.config = config
            self.logger = logger
            self.validator = VideoValidator(config, logger)

    def get_audio_info(self, input_path: Path) -> List[Dict]:
        """
        Get detailed information about audio streams

        Args:
            input_path: Path to input video file

        Returns:
            List of dictionaries containing audio stream information

        Raises:
            ProcessError: If ffprobe fails
        """
        try:
            cmd = [
                'ffprobe',
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_streams',
                '-select_streams', 'a',
                str(input_path)
            ]

            _, stdout, stderr = run_command(
                cmd,
                "Getting audio stream information",
                show_output=False
            )

            data = json.loads(stdout)
            return data.get('streams', [])

        except subprocess.CalledProcessError as e:
            raise ProcessError(
                "Failed to get audio information",
                cmd,
                e.returncode,
                e.stdout,
                e.stderr
            )
        except json.JSONDecodeError as e:
            raise AudioError(f"Failed to parse audio information: {e}")

    def encode_audio_track(
        self,
        input_path: Path,
        track_index: int,
        channels: int,
        working_dir: Path
    ) -> Path:
        """
        Encode a single audio track

        Args:
            input_path: Path to input video file
            track_index: Index of audio track to encode
            channels: Number of channels in the track
            working_dir: Directory for output

        Returns:
            Path to encoded audio file

        Raises:
            AudioError: If encoding fails
        """
        output_file = working_dir / f"audio-{track_index}.mkv"
        bitrate = self.config.get_audio_bitrate(channels)

        logger.info(
            f"Encoding audio track {track_index} "
            f"with {channels} channels at {bitrate}k"
        )

        try:
            cmd = [
                'ffmpeg',
                '-v', 'info',  # More verbose output
                '-stats',      # Show encoding stats
                '-i', str(input_path),
                '-map', f'0:a:{track_index}',
                '-c:a', 'libopus',
                '-af', 'aformat=channel_layouts=7.1|5.1|stereo|mono',
                '-application', 'audio',
                '-vbr', 'on',
                '-compression_level', '10',
                '-frame_duration', '20',
                '-b:a', f'{bitrate}k',
                '-avoid_negative_ts', 'make_zero',
                str(output_file)
            ]

            run_command(
                cmd,
                f"Encoding audio track {track_index}",
                show_output=True
            )

            return output_file

        except subprocess.CalledProcessError as e:
            raise AudioError(
                f"Failed to encode audio track {track_index}: {e.stderr}",
                input_path
            )

    def encode_audio_tracks(
        self,
        input_path: Path,
        working_dir: Path
    ) -> List[Path]:
        """
        Encode all audio tracks from input video

        Args:
            input_path: Path to input video file
            working_dir: Directory for encoded audio files

        Returns:
            List of paths to encoded audio files

        Raises:
            AudioError: If encoding fails
        """
        # Get audio stream information
        audio_streams = self.get_audio_info(input_path)

        if not audio_streams:
            logger.warning("No audio streams found in input file")
            return []

        logger.info(f"Found {len(audio_streams)} audio tracks to encode")
        encoded_files = []

        for i, stream in enumerate(audio_streams):
            channels = int(stream.get('channels', 2))
            logger.info(f"Processing audio track {i} ({stream.get('codec_name', 'unknown')} - {channels} channels)")

            output_file = self.encode_audio_track(
                input_path,
                i,
                channels,
                working_dir
            )
            encoded_files.append(output_file)

        # Validate all audio tracks
        self.validator.validate_audio_tracks(
            input_path,
            len(audio_streams),
            working_dir
        )

        return encoded_files

    def remux_tracks(
        self,
        video_file: Path,
        audio_files: List[Path],
        output_file: Path
    ) -> None:
        """
        Remux video and audio tracks into final output

        Args:
            video_file: Path to encoded video file
            audio_files: List of paths to encoded audio files
            output_file: Path for final output file

        Raises:
            RemuxError: If remuxing fails
        """
        logger.info("Remuxing tracks")
        logger.info(f"Video file: {video_file}")
        for i, audio in enumerate(audio_files):
            logger.info(f"Audio track {i}: {audio}")

        try:
            # Build ffmpeg command
            cmd = [
                'ffmpeg',
                '-v', 'info',  # More verbose output
                '-stats',      # Show progress
                '-i', str(video_file)
            ]

            # Add audio inputs
            for audio_file in audio_files:
                cmd.extend(['-i', str(audio_file)])

            # Add mapping
            cmd.extend(['-map', '0:v'])  # Map video from first input
            for i in range(len(audio_files)):
                cmd.extend(['-map', f'{i+1}:a'])  # Map audio from subsequent inputs

            # Add output file
            cmd.extend([
                '-c', 'copy',
                '-movflags', '+faststart',  # Optimize for streaming
                str(output_file)
            ])

            run_command(
                cmd,
                "Remuxing tracks",
                show_output=True
            )

            # Validate final output
            self.validator.validate_final_output(
                output_file,
                len(audio_files)
            )

            logger.info(f"Successfully created output file: {output_file}")

        except subprocess.CalledProcessError as e:
            raise RemuxError(
                f"Failed to remux tracks: {e.stderr}",
                output_file
            )
        except Exception as e:
            raise RemuxError(str(e), output_file)

    def get_audio_metadata(self, file: Path) -> Dict:
        """
        Get detailed metadata about audio streams

        Args:
            file: Path to audio file

        Returns:
            Dictionary containing audio metadata

        Raises:
            ProcessError: If mediainfo fails
        """
        try:
            cmd = [
                'mediainfo',
                '--Output=JSON',
                str(file)
            ]

            _, stdout, stderr = run_command(
                cmd,
                "Getting audio metadata",
                show_output=False
            )

            return json.loads(stdout)

        except subprocess.CalledProcessError as e:
            raise ProcessError(
                "Failed to get audio metadata",
                cmd,
                e.returncode,
                e.stdout,
                e.stderr
            )
        except json.JSONDecodeError as e:
            raise AudioError(f"Failed to parse audio metadata: {e}")

    def print_audio_info(self, input_path: Path) -> None:
        """
        Print detailed information about audio tracks

        Args:
            input_path: Path to input file
        """
        try:
            audio_info = self.get_audio_info(input_path)

            logger.info(f"\nAudio Track Information for {input_path.name}:")
            for i, stream in enumerate(audio_info):
                logger.info(f"\nTrack {i}:")
                logger.info(f"  Codec: {stream.get('codec_name', 'unknown')}")
                logger.info(f"  Channels: {stream.get('channels', 'unknown')}")
                logger.info(f"  Sample Rate: {stream.get('sample_rate', 'unknown')} Hz")
                logger.info(f"  Bit Rate: {int(stream.get('bit_rate', 0)) // 1000} kbps")
                if 'tags' in stream:
                    logger.info("  Tags:")
                    for key, value in stream['tags'].items():
                        logger.info(f"    {key}: {value}")

        except (ProcessError, AudioError) as e:
            logger.error(f"Failed to get audio information: {e}")
