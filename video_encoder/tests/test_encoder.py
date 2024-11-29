#!/usr/bin/env python3

import pytest
from pathlib import Path
import shutil
import tempfile
from unittest.mock import Mock, patch, call
import subprocess

from ..config import EncoderConfig
from ..core.encoder import VideoEncoder, ProcessingStats
from ..utils.exceptions import VideoEncoderError, CleanupError

@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing"""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)

@pytest.fixture
def config(temp_dir):
    """Create a test configuration"""
    config = EncoderConfig()
    config.BASE_DIR = temp_dir
    return config

@pytest.fixture
def encoder(config):
    """Create a test encoder instance"""
    return VideoEncoder(config)

@pytest.fixture
def mock_video_processor():
    """Create a mock video processor"""
    with patch('core.encoder.VideoProcessor') as mock:
        yield mock

@pytest.fixture
def mock_audio_processor():
    """Create a mock audio processor"""
    with patch('core.encoder.AudioProcessor') as mock:
        yield mock

class TestVideoEncoder:
    def test_prepare_directories(self, encoder, temp_dir):
        """Test directory preparation"""
        encoder.prepare_directories()

        # Verify all directories were created
        for dir_name in encoder.config.DIRS:
            assert (temp_dir / dir_name).is_dir()

    def test_prepare_directories_failure(self, encoder):
        """Test directory preparation failure"""
        with patch('pathlib.Path.mkdir') as mock_mkdir:
            mock_mkdir.side_effect = PermissionError("Access denied")
            with pytest.raises(VideoEncoderError, match="Failed to create directories"):
                encoder.prepare_directories()

    def test_get_input_files(self, encoder, temp_dir):
        """Test input file detection"""
        # Create test files
        input_dir = temp_dir / "input"
        input_dir.mkdir(parents=True)

        test_files = [
            input_dir / "video1.mkv",
            input_dir / "video2.mkv",
            input_dir / "ignore.txt"
        ]

        for file in test_files:
            file.touch()

        files = encoder.get_input_files()
        assert len(files) == 2
        assert all(f.suffix == '.mkv' for f in files)

    def test_get_input_files_empty(self, encoder):
        """Test empty input directory"""
        with pytest.raises(VideoEncoderError, match="No input files found"):
            encoder.get_input_files()

    @pytest.mark.parametrize("exception_type", [
        PermissionError,
        OSError,
        Exception
    ])
    def test_cleanup_working_dirs_failure(self, encoder, exception_type):
        """Test cleanup failure scenarios"""
        with patch('shutil.rmtree') as mock_rmtree:
            mock_rmtree.side_effect = exception_type("Test error")
            with pytest.raises(CleanupError, match="Failed to clean up directories"):
                encoder.cleanup_working_dirs("test")

    def test_process_video(self, encoder, mock_video_processor, mock_audio_processor, temp_dir):
        """Test video processing workflow"""
        # Setup test file
        input_file = temp_dir / "input" / "test.mkv"
        input_file.parent.mkdir(parents=True)
        input_file.touch()

        # Setup mocks
        mock_video = mock_video_processor.return_value
        mock_audio = mock_audio_processor.return_value
        mock_audio.encode_audio_tracks.return_value = [Path("audio1.mkv"), Path("audio2.mkv")]

        # Process video
        encoder.process_video(input_file, 1, 1)

        # Verify processing steps
        assert mock_video.detect_dolby_vision.called
        assert mock_video.segment_video.called
        assert mock_video.encode_segments.called
        assert mock_video.concatenate_segments.called
        assert mock_audio.encode_audio_tracks.called
        assert mock_audio.remux_tracks.called

    def test_process_video_error_handling(self, encoder, mock_video_processor):
        """Test error handling during video processing"""
        mock_video = mock_video_processor.return_value
        mock_video.segment_video.side_effect = Exception("Test error")

        input_file = Path("test.mkv")

        with pytest.raises(Exception, match="Test error"):
            encoder.process_video(input_file, 1, 1)

        # Verify stats were updated even after error
        assert encoder.stats["test"].end_time is not None

    def test_run_complete_workflow(self, encoder, temp_dir):
        """Test complete encoding workflow"""
        # Setup test files
        input_dir = temp_dir / "input"
        input_dir.mkdir(parents=True)

        test_files = [
            input_dir / "video1.mkv",
            input_dir / "video2.mkv"
        ]

        for file in test_files:
            file.touch()

        # Mock processing to avoid actual encoding
        with patch.object(encoder, 'process_video') as mock_process:
            encoder.run()

            assert mock_process.call_count == 2
            assert len(encoder.processed_videos) == 2

    @pytest.mark.parametrize("input_size,output_size,expected_ratio", [
        (1000, 500, 2.0),
        (1000, 1000, 1.0),
        (500, 1000, 0.5),
    ])
    def test_processing_stats(self, input_size, output_size, expected_ratio):
        """Test processing statistics calculations"""
        stats = ProcessingStats(
            filename="test.mkv",
            start_time=100.0,
            end_time=200.0,
            input_size=input_size,
            output_size=output_size
        )

        assert stats.duration == 100.0
        assert stats.compression_ratio == expected_ratio

    def test_summary_generation(self, encoder):
        """Test summary generation with mock data"""
        # Add some test stats
        encoder.stats["video1"] = ProcessingStats(
            filename="video1",
            start_time=100.0,
            end_time=200.0,
            input_size=1000,
            output_size=500,
            segment_count=5,
            audio_tracks=2
        )

        encoder.processed_videos = ["video1"]

        with patch('logging.info') as mock_log:
            encoder.print_summary(100.0)

            # Verify summary logging
            assert any("Encoding Summary" in call.args[0]
                     for call in mock_log.call_args_list)
            assert any("video1" in call.args[0]
                     for call in mock_log.call_args_list)
