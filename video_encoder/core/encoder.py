#!/usr/bin/env python3

import logging
import shutil
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime
import time

from ..config import EncoderConfig
from ..utils.exceptions import VideoEncoderError, CleanupError
from ..utils.logging_config import ContextLogger
from .video import VideoProcessor
from .audio import AudioProcessor

@dataclass
class ProcessingStats:
    """Statistics for a single video processing job"""
    filename: str
    start_time: float
    end_time: Optional[float] = None
    input_size: Optional[int] = None
    output_size: Optional[int] = None
    segment_count: Optional[int] = None
    audio_tracks: Optional[int] = None

    @property
    def duration(self) -> float:
        """Get processing duration in seconds"""
        if self.end_time is None:
            return 0
        return self.end_time - self.start_time

    @property
    def compression_ratio(self) -> Optional[float]:
        """Get compression ratio"""
        if self.input_size and self.output_size:
            return self.input_size / self.output_size
        return None

class VideoEncoder:
    """Main encoder class that orchestrates the encoding process"""

    def __init__(self, config: EncoderConfig, logger: ContextLogger):
        self.config = config
        self.logger = logger
        self.video_processor = VideoProcessor(config, logger)
        self.audio_processor = AudioProcessor(config, logger)
        self.stats: Dict[str, ProcessingStats] = {}
        self.processed_videos: List[str] = []

    def prepare_directories(self) -> None:
        """Create required directories if they don't exist"""
        self.logger.info("Preparing working directories")
        try:
            for dir_path in self.config.get_all_dirs().values():
                dir_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise VideoEncoderError(f"Failed to create directories: {e}")

    def get_input_files(self) -> List[Path]:
        """Get list of video files to process"""
        input_dir = self.config.get_dir("input")
        files = []

        for fmt in self.config.SUPPORTED_FORMATS:
            files.extend(input_dir.glob(f"*.{fmt}"))

        if not files:
            raise VideoEncoderError(f"No input files found in {input_dir}")

        return sorted(files)

    def cleanup_working_dirs(self, vid_file: str) -> None:
        """Clean up temporary working directories"""
        try:
            for dir_name in ["segments", "encoded_segments", "working"]:
                dir_path = self.config.get_dir(dir_name)
                if dir_path.exists():
                    shutil.rmtree(dir_path)
                dir_path.mkdir(parents=True)
        except Exception as e:
            raise CleanupError(f"Failed to clean up directories: {e}")

    def process_video(self, input_path: Path, current_file: int, total_files: int) -> None:
        """
        Process a single video file

        Args:
            input_path: Path to input video file
            current_file: Current file number
            total_files: Total number of files to process
        """
        vid_file = input_path.stem
        self.logger.info(f"Processing file {current_file} of {total_files}: {input_path}")

        # Initialize stats
        self.stats[vid_file] = ProcessingStats(
            filename=vid_file,
            start_time=time.time(),
            input_size=input_path.stat().st_size
        )
        self.processed_videos.append(vid_file)

        # Set current file in logger context
        self.logger.set_current_file(input_path.name)

        try:
            # Prepare working directories
            self.cleanup_working_dirs(vid_file)

            # Get directory paths
            segments_dir = self.config.get_dir("segments")
            encoded_dir = self.config.get_dir("encoded_segments")
            working_dir = self.config.get_dir("working")
            output_dir = self.config.get_dir("output")

            # Process video
            self.logger.info("Starting video processing")

            # Detect Dolby Vision and segment video
            self.video_processor.detect_dolby_vision(input_path)
            self.video_processor.segment_video(input_path, segments_dir)

            # Update segment count in stats
            self.stats[vid_file].segment_count = len(list(segments_dir.glob("*.mkv")))

            # Encode video segments
            self.video_processor.encode_segments(segments_dir, encoded_dir, input_path.name)

            # Concatenate encoded segments
            video_output = working_dir / f"{vid_file}.mkv"
            self.video_processor.concatenate_segments(encoded_dir, video_output)

            # Process audio
            self.logger.info("Starting audio processing")
            audio_files = self.audio_processor.encode_audio_tracks(input_path, working_dir)
            self.stats[vid_file].audio_tracks = len(audio_files)

            # Remux video and audio
            final_output = output_dir / f"{vid_file}.mkv"
            self.audio_processor.remux_tracks(video_output, audio_files, final_output)

            # Update stats
            self.stats[vid_file].output_size = final_output.stat().st_size
            self.stats[vid_file].end_time = time.time()

            self.logger.info(f"Completed processing: {input_path}")

        except Exception as e:
            self.logger.error(f"Error processing {input_path}: {e}")
            # Update end time even if there's an error
            self.stats[vid_file].end_time = time.time()
            raise

        finally:
            # Clear current file from logger context
            self.logger.set_current_file(None)
            # Ensure cleanup happens even if there's an error
            try:
                self.cleanup_working_dirs(vid_file)
            except Exception as cleanup_error:
                self.logger.error(f"Error during cleanup: {cleanup_error}")

    def print_summary(self, start_time: float) -> None:
        """Print processing summary"""
        end_time = time.time()
        total_duration = end_time - start_time

        # Convert total duration to hours, minutes, seconds
        hours, remainder = divmod(int(total_duration), 3600)
        minutes, seconds = divmod(remainder, 60)

        # Print summary
        self.logger.info("\n=== Encoding Summary ===")
        self.logger.info("Overall Process:")
        self.logger.info(f"Start time: {datetime.fromtimestamp(start_time):%Y-%m-%d %H:%M:%S}")
        self.logger.info(f"End time: {datetime.fromtimestamp(end_time):%Y-%m-%d %H:%M:%S}")
        self.logger.info(f"Total Duration: {hours}h {minutes}m {seconds}s")

        self.logger.info("\nIndividual Video Processing Times:")
        for vid_file in self.processed_videos:
            stats = self.stats[vid_file]
            # Convert file duration to hours, minutes, seconds
            file_hours, remainder = divmod(int(stats.duration), 3600)
            file_minutes, file_seconds = divmod(remainder, 60)

            self.logger.info(f"\n{vid_file}:")
            self.logger.info(f"  Duration: {file_hours}h {file_minutes}m {file_seconds}s")
            self.logger.info(f"  Segments: {stats.segment_count}")
            self.logger.info(f"  Audio Tracks: {stats.audio_tracks}")

            if stats.compression_ratio:
                self.logger.info(f"  Compression Ratio: {stats.compression_ratio:.2f}x")
                self.logger.info(f"  Input Size: {stats.input_size / 1024 / 1024:.2f} MB")
                self.logger.info(f"  Output Size: {stats.output_size / 1024 / 1024:.2f} MB")

        self.logger.info("\nEncoding workflow complete")

    def run(self) -> None:
        """Main execution method"""
        start_time = time.time()
        self.logger.info("Starting video encoding workflow")

        try:
            # Prepare environment
            self.prepare_directories()

            # Get input files
            input_files = self.get_input_files()
            self.logger.info(f"Found {len(input_files)} files to process")

            # Process each file
            for i, input_file in enumerate(input_files, 1):
                try:
                    self.process_video(input_file, i, len(input_files))
                except Exception as e:
                    self.logger.error(f"Failed to process {input_file}: {e}")
                    # Continue with next file
                    continue

            # Print summary
            self.print_summary(start_time)

        except Exception as e:
            self.logger.error(f"Fatal error in encoding workflow: {e}")
            raise
        finally:
            # Always try to cleanup
            try:
                self.cleanup_working_dirs("final")
            except Exception as e:
                self.logger.error(f"Failed to cleanup: {e}")
