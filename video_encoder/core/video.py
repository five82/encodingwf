#!/usr/bin/env python3

from ..utils.logging_config import ContextLogger

import subprocess
import logging
from pathlib import Path
from typing import Optional, List
import shutil
from datetime import datetime

from ..config import EncoderConfig
from ..utils.exceptions import (
    ProcessError,
    SegmentationError,
    EncodingError,
    ConcatenationError
)
from ..utils.validation import VideoValidator
from ..utils.subprocess import run_command

logger = logging.getLogger()

class VideoProcessor:
    """Handles all video-specific processing operations"""

    def __init__(self, config: EncoderConfig, logger: ContextLogger):
        self.config = config
        self.logger = logger
        self.validator = VideoValidator(config, logger)  # Pass logger here
        self.is_dolby_vision = False

    def detect_dolby_vision(self, file: Path) -> None:
        """
        Detect if video has Dolby Vision

        Args:
            file: Path to input video file
        """
        logger.info("Detecting Dolby Vision...")

        try:
            cmd = [
                'mediainfo',
                '--Output=JSON',
                str(file)
            ]

            _, stdout, _ = run_command(
                cmd,
                "Mediainfo detection",
                show_output=False
            )

            # Check for Dolby Vision in detailed output
            self.is_dolby_vision = 'Dolby Vision' in stdout

            if self.is_dolby_vision:
                logger.info("Dolby Vision detected in input file")
                logger.warning("Note: Dolby Vision encoding is currently disabled to prevent metadata corruption")
            else:
                logger.info("No Dolby Vision detected, continuing with standard encoding")

        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to detect Dolby Vision: {e.stderr}")
            logger.warning("Assuming no Dolby Vision present")
            self.is_dolby_vision = False

    def segment_video(self, input_path: Path, segments_dir: Path) -> None:
        """
        Split video into segments for parallel processing

        Args:
            input_path: Path to input video file
            segments_dir: Directory to store segments

        Raises:
            SegmentationError: If segmentation fails
        """
        logger.info(f"Segmenting video: {input_path}")
        logger.info(f"Segment duration: {self.config.SEGMENT_DURATION}")
        logger.info(f"Output directory: {segments_dir}")

        try:
            cmd = [
                'ffmpeg',
                '-v', 'info',      # Verbose output
                '-stats',          # Show progress stats
                '-i', str(input_path),
                '-c:v', 'copy',    # Copy video stream
                '-an',             # No audio
                '-map', '0:v',     # Map only video streams
                '-segment_time', self.config.SEGMENT_DURATION,
                '-reset_timestamps', '1',
                '-f', 'segment',
                str(segments_dir / '%04d.mkv')
            ]

            run_command(
                cmd,
                "Segmenting video",
                show_output=True
            )

            # Count and validate segments
            segments = list(segments_dir.glob("*.mkv"))
            total_segments = len(segments)
            logger.info(f"Created {total_segments} segments")

            # Validate segments
            self.validator.validate_segments(segments_dir)

        except subprocess.CalledProcessError as e:
            raise SegmentationError(
                f"Failed to segment video: {e.stderr}",
                input_path
            )
        except Exception as e:
            raise SegmentationError(str(e), input_path)

    def encode_segment(self, segment: Path, output_dir: Path, segment_num: int, total_segments: int, input_file: str) -> None:
        """
        Encode a single video segment using ab-av1

        Args:
            segment: Path to input segment
            output_dir: Directory for encoded output
            segment_num: Current segment number
            total_segments: Total number of segments
            input_file: Name of original input file

        Raises:
            EncodingError: If encoding fails
        """
        output_file = output_dir / segment.name
        self.logger.info(f"{'='*30}")
        self.logger.info(f"Encoding segment {segment_num}/{total_segments}")
        self.logger.info(f"Input: {segment}")
        self.logger.info(f"Output: {output_file}")
        self.logger.debug(f"Segment size: {segment.stat().st_size / (1024*1024):.2f} MB")
        
        try:
            cmd = [
                'ab-av1', 'auto-encode',
                '-e', 'libsvtav1',
                '--svt', 'tune=3',
                '--svt', 'film-grain=8',
                '--svt', 'film-grain-denoise=1',
                '--svt', 'adaptive-film-grain=1',
                '--keyint', self.config.KEYINT,
                '--min-vmaf', str(self.config.MIN_VMAF),
                '--preset', str(self.config.PRESET.value),
                '--vmaf', 'n_subsample=8:pool=harmonic_mean',
                '--samples', '3',
                '--sample-duration', '1sec',
                '--verbose',
                '--input', str(segment),
                '--output', str(output_file)
            ]

            if self.is_dolby_vision:
                # Note: Disabled for now as it needs proper metadata handling
                # cmd.extend(['--enc', 'dolbyvision=true'])
                pass

            start_time = datetime.now()
            run_command(
                cmd,
                f"Encoding segment {segment_num}/{total_segments}",
                show_output=True
            )
            duration = datetime.now() - start_time

            # Get segment sizes for logging
            input_size = segment.stat().st_size / (1024 * 1024)  # MB
            output_size = output_file.stat().st_size / (1024 * 1024)  # MB
            compression = input_size / output_size if output_size > 0 else 0

            logger.info(f"Segment {segment_num}/{total_segments} encoding completed:")
            logger.info(f"  Duration: {duration}")
            logger.info(f"  Input size: {input_size:.2f} MB")
            logger.info(f"  Output size: {output_size:.2f} MB")
            logger.info(f"  Compression ratio: {compression:.2f}x")

            # Validate encoded segment
            self.validator.validate_video_file(output_file, "Segment encoding")

            self.logger.info(f"Successfully encoded segment {segment_num}/{total_segments}")
            self.logger.debug(f"Output size: {output_file.stat().st_size / (1024*1024):.2f} MB")
        except Exception as e:
            self.logger.error(f"Failed to encode segment {segment_num}", exc_info=True)
            raise
        finally:
            self.logger.info(f"{'='*30}")

    def encode_segments(self, segments_dir: Path, encoded_dir: Path, input_file: str = "") -> None:
        """
        Encode all video segments

        Args:
            segments_dir: Directory containing input segments
            encoded_dir: Directory for encoded outputs
            input_file: Name of original input file

        Raises:
            EncodingError: If encoding fails
        """
        segments = sorted(segments_dir.glob('*.mkv'))
        total_segments = len(segments)
        logger.info(f"Starting encoding of {total_segments} segments")
        logger.info(f"Encoding preset: {self.config.PRESET.name}")
        logger.info(f"Target minimum VMAF: {self.config.MIN_VMAF}")

        start_time = datetime.now()
        total_input_size = 0
        total_output_size = 0

        for i, segment in enumerate(segments, 1):
            # Track sizes for statistics
            total_input_size += segment.stat().st_size

            try:
                self.encode_segment(segment, encoded_dir, i, total_segments, input_file)
                total_output_size += (encoded_dir / segment.name).stat().st_size
            except EncodingError as e:
                logger.error(f"Failed to encode segment {i}/{total_segments}: {e}")
                raise

        # Calculate and log overall statistics
        duration = datetime.now() - start_time
        avg_size_reduction = (1 - (total_output_size / total_input_size)) * 100 if total_input_size > 0 else 0

        logger.info("\nEncoding Summary:")
        logger.info(f"Total duration: {duration}")
        logger.info(f"Total input size: {total_input_size / (1024*1024):.2f} MB")
        logger.info(f"Total output size: {total_output_size / (1024*1024):.2f} MB")
        logger.info(f"Average size reduction: {avg_size_reduction:.1f}%")

        # Validate all encoded segments
        self.validator.validate_segments(encoded_dir, min_segments=total_segments)

    def concatenate_segments(self, encoded_dir: Path, output_file: Path) -> None:
        """
        Concatenate encoded segments into final video

        Args:
            encoded_dir: Directory containing encoded segments
            output_file: Path for final concatenated output

        Raises:
            ConcatenationError: If concatenation fails
        """
        logger.info("Concatenating encoded segments")
        logger.info(f"Output file: {output_file}")

        try:
            # Create concat file
            concat_file = encoded_dir / "concat.txt"
            segments = sorted(encoded_dir.glob('*.mkv'))

            logger.info(f"Writing concat file with {len(segments)} segments")
            with open(concat_file, 'w') as f:
                for segment in segments:
                    # Properly escape single quotes in filename
                    escaped_path = str(segment).replace("'", "'\\''")
                    f.write(f"file '{escaped_path}'\n")

            # Concatenate segments
            cmd = [
                'ffmpeg',
                '-v', 'info',          # Verbose output
                '-stats',              # Show progress
                '-f', 'concat',
                '-safe', '0',
                '-i', str(concat_file),
                '-c', 'copy',          # Stream copy
                '-movflags', '+faststart',  # Optimize for streaming
                str(output_file)
            ]

            run_command(
                cmd,
                "Concatenating segments",
                show_output=True
            )

            # Clean up concat file
            concat_file.unlink()

            # Get final size
            final_size = output_file.stat().st_size / (1024 * 1024)  # MB
            logger.info(f"Concatenation complete. Final size: {final_size:.2f} MB")

            # Validate concatenated file
            logger.info("Validating concatenated file")
            self.validator.validate_video_file(output_file, "Concatenation")

        except subprocess.CalledProcessError as e:
            raise ConcatenationError(
                f"Failed to concatenate segments: {e.stderr}",
                output_file
            )
        except Exception as e:
            raise ConcatenationError(str(e), output_file)

    def cleanup_segments(self, *directories: Path) -> None:
        """
        Clean up temporary segment directories

        Args:
            *directories: Directories to clean up
        """
        for directory in directories:
            if directory.exists():
                logger.info(f"Cleaning up directory: {directory}")
                try:
                    shutil.rmtree(directory)
                    logger.debug(f"Successfully removed {directory}")
                except Exception as e:
                    logger.warning(f"Failed to remove directory {directory}: {e}")
