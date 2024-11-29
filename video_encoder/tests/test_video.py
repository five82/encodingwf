#!/usr/bin/env python3

import pytest
from pathlib import Path
import tempfile
import subprocess
from unittest.mock import Mock, patch, call

from ..config import EncoderConfig
from ..core.video import VideoProcessor
from ..utils.exceptions import (
    SegmentationError,
    EncodingError,
    ConcatenationError
)

@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)

@pytest.fixture
def config(temp_dir):
    """Create a test configuration"""
    config = EncoderConfig()
    config.BASE_DIR = temp_dir
    return config

@pytest.fixture
def processor(config):
    """Create a test video processor"""
    return VideoProcessor(config)

class TestVideoProcessor:
    def test_detect_dolby_vision_success(self, processor):
        """Test successful Dolby Vision detection"""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value.stdout = "Dolby Vision"
            mock_run.return_value.check_returncode.return_value = None

            processor.detect_dolby_vision(Path("test.mkv"))
            assert processor.is_dolby_vision is True

    def test_detect_dolby_vision_not_present(self, processor):
        """Test when Dolby Vision is not present"""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value.stdout = "HDR10"
            mock_run.return_value.check_returncode.return_value = None

            processor.detect_dolby_vision(Path("test.mkv"))
            assert processor.is_dolby_vision is False

    def test_detect_dolby_vision_error(self, processor):
        """Test Dolby Vision detection error"""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, [], "Error")

            processor.detect_dolby_vision(Path("test.mkv"))
            assert processor.is_dolby_vision is False

    def test_segment_video_success(self, processor, temp_dir):
        """Test successful video segmentation"""
        input_path = Path("test.mkv")
        segments_dir = temp_dir / "segments"
        segments_dir.mkdir()

        with patch('subprocess.run') as mock_run:
            mock_run.return_value.check_returncode.return_value = None

            # Create mock segments
            for i in range(3):
                (segments_dir / f"{i:04d}.mkv").touch()

            processor.segment_video(input_path, segments_dir)

            assert mock_run.called
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == 'ffmpeg'
            assert '-segment_time' in cmd

    def test_segment_video_failure(self, processor, temp_dir):
        """Test video segmentation failure"""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, [], "Error")

            with pytest.raises(SegmentationError):
                processor.segment_video(Path("test.mkv"), temp_dir)

    @pytest.mark.parametrize("segment_name", ["0000.mkv", "0001.mkv"])
    def test_encode_segment_success(self, processor, temp_dir, segment_name):
        """Test successful segment encoding"""
        segment = temp_dir / segment_name
        segment.touch()
        output_dir = temp_dir / "encoded"
        output_dir.mkdir()

        with patch('subprocess.run') as mock_run:
            mock_run.return_value.check_returncode.return_value = None

            processor.encode_segment(segment, output_dir)

            assert mock_run.called
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == 'ab-av1'
            assert str(segment) in cmd
            assert str(output_dir / segment_name) in cmd

    def test_encode_segment_failure(self, processor, temp_dir):
        """Test segment encoding failure"""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, [], "Error")

            with pytest.raises(EncodingError):
                processor.encode_segment(
                    Path("test.mkv"),
                    temp_dir
                )

    def test_concatenate_segments_success(self, processor, temp_dir):
        """Test successful segment concatenation"""
        encoded_dir = temp_dir / "encoded"
        encoded_dir.mkdir()
        output_file = temp_dir / "output.mkv"

        # Create mock segments
        for i in range(3):
            (encoded_dir / f"{i:04d}.mkv").touch()

        with patch('subprocess.run') as mock_run:
            mock_run.return_value.check_returncode.return_value = None

            processor.concatenate_segments(encoded_dir, output_file)

            assert mock_run.called
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == 'ffmpeg'
            assert '-f' in cmd
            assert 'concat' in cmd

    def test_concatenate_segments_failure(self, processor, temp_dir):
        """Test segment concatenation failure"""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, [], "Error")

            with pytest.raises(ConcatenationError):
                processor.concatenate_segments(
                    temp_dir,
                    temp_dir / "output.mkv"
                )

    def test_cleanup_segments(self, processor, temp_dir):
        """Test segment cleanup"""
        test_dirs = [
            temp_dir / "dir1",
            temp_dir / "dir2"
        ]

        for d in test_dirs:
            d.mkdir()
            (d / "test.mkv").touch()

        processor.cleanup_segments(*test_dirs)

        for d in test_dirs:
            assert not d.exists()
