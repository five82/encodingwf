#!/usr/bin/env python3

import pytest
import json
from pathlib import Path
import tempfile
import subprocess
from unittest.mock import Mock, patch, call

from ..config import EncoderConfig
from ..core.audio import AudioProcessor
from ..utils.exceptions import (
    AudioError,
    ProcessError,
    RemuxError
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
    """Create a test audio processor"""
    return AudioProcessor(config)

@pytest.fixture
def mock_audio_info():
    """Sample audio stream information"""
    return {
        "streams": [
            {
                "index": 0,
                "codec_name": "aac",
                "codec_type": "audio",
                "channels": 2,
                "sample_rate": "48000",
                "bit_rate": "128000"
            },
            {
                "index": 1,
                "codec_name": "ac3",
                "codec_type": "audio",
                "channels": 6,
                "sample_rate": "48000",
                "bit_rate": "384000"
            }
        ]
    }

class TestAudioProcessor:
    def test_get_audio_info_success(self, processor, mock_audio_info):
        """Test successful audio info retrieval"""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value.stdout = json.dumps(mock_audio_info)
            mock_run.return_value.check_returncode.return_value = None

            info = processor.get_audio_info(Path("test.mkv"))

            assert len(info) == 2
            assert info[0]["channels"] == 2
            assert info[1]["channels"] == 6

    def test_get_audio_info_failure(self, processor):
        """Test audio info retrieval failure"""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, [], "Error")

            with pytest.raises(ProcessError):
                processor.get_audio_info(Path("test.mkv"))

    def test_get_audio_info_invalid_json(self, processor):
        """Test handling of invalid JSON response"""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value.stdout = "Invalid JSON"
            mock_run.return_value.check_returncode.return_value = None

            with pytest.raises(AudioError, match="Failed to parse audio information"):
                processor.get_audio_info(Path("test.mkv"))

    @pytest.mark.parametrize("channels,expected_bitrate", [
        (1, 64),   # Mono
        (2, 128),  # Stereo
        (6, 256),  # 5.1
        (8, 384),  # 7.1
        (4, 192),  # Custom (4 * 48)
    ])
    def test_encode_audio_track_bitrates(self, processor, temp_dir, channels, expected_bitrate):
        """Test audio track encoding with different channel counts"""
        input_path = Path("test.mkv")
        working_dir = temp_dir / "working"
        working_dir.mkdir()

        with patch('subprocess.run') as mock_run:
            mock_run.return_value.check_returncode.return_value = None

            processor.encode_audio_track(input_path, 0, channels, working_dir)

            cmd = mock_run.call_args[0][0]
            assert f"{expected_bitrate}k" in cmd
            assert "libopus" in cmd

    def test_encode_audio_track_failure(self, processor, temp_dir):
        """Test audio track encoding failure"""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, [], "Error")

            with pytest.raises(AudioError):
                processor.encode_audio_track(
                    Path("test.mkv"),
                    0,
                    2,
                    temp_dir
                )

    def test_encode_audio_tracks_success(self, processor, temp_dir, mock_audio_info):
        """Test encoding of multiple audio tracks"""
        input_path = Path("test.mkv")
        working_dir = temp_dir / "working"
        working_dir.mkdir()

        with patch('subprocess.run') as mock_run:
            # Mock audio info retrieval
            mock_run.return_value.stdout = json.dumps(mock_audio_info)
            mock_run.return_value.check_returncode.return_value = None

            encoded_files = processor.encode_audio_tracks(input_path, working_dir)

            assert len(encoded_files) == 2
            assert all(f.parent == working_dir for f in encoded_files)
            assert mock_run.call_count > 0

    def test_encode_audio_tracks_no_streams(self, processor, temp_dir):
        """Test handling of files with no audio streams"""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value.stdout = json.dumps({"streams": []})
            mock_run.return_value.check_returncode.return_value = None

            result = processor.encode_audio_tracks(Path("test.mkv"), temp_dir)
            assert result == []

    def test_remux_tracks_success(self, processor, temp_dir):
        """Test successful track remuxing"""
        video_file = temp_dir / "video.mkv"
        video_file.touch()

        audio_files = [
            temp_dir / "audio-0.mkv",
            temp_dir / "audio-1.mkv"
        ]
        for f in audio_files:
            f.touch()

        output_file = temp_dir / "output.mkv"

        with patch('subprocess.run') as mock_run:
            mock_run.return_value.check_returncode.return_value = None

            processor.remux_tracks(video_file, audio_files, output_file)

            assert mock_run.called
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == 'ffmpeg'
            assert str(video_file) in cmd
            assert all(str(f) in cmd for f in audio_files)

    def test_remux_tracks_failure(self, processor, temp_dir):
        """Test remuxing failure"""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, [], "Error")

            with pytest.raises(RemuxError):
                processor.remux_tracks(
                    Path("video.mkv"),
                    [Path("audio.mkv")],
                    Path("output.mkv")
                )

    def test_get_audio_metadata_success(self, processor):
        """Test successful audio metadata retrieval"""
        sample_metadata = {
            "media": {
                "track": [
                    {
                        "type": "Audio",
                        "Format": "AAC",
                        "Channels": "2"
                    }
                ]
            }
        }

        with patch('subprocess.run') as mock_run:
            mock_run.return_value.stdout = json.dumps(sample_metadata)
            mock_run.return_value.check_returncode.return_value = None

            metadata = processor.get_audio_metadata(Path("test.mkv"))
            assert "media" in metadata
            assert "track" in metadata["media"]

    def test_get_audio_metadata_failure(self, processor):
        """Test audio metadata retrieval failure"""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, [], "Error")

            with pytest.raises(ProcessError):
                processor.get_audio_metadata(Path("test.mkv"))

    def test_print_audio_info(self, processor, mock_audio_info, caplog):
        """Test audio information printing"""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value.stdout = json.dumps(mock_audio_info)
            mock_run.return_value.check_returncode.return_value = None

            processor.print_audio_info(Path("test.mkv"))

            log_text = caplog.text
            assert "Audio Track Information" in log_text
            assert "Codec:" in log_text
            assert "Channels:" in log_text
            assert "Sample Rate:" in log_text

    @pytest.mark.parametrize("error_type", [
        ProcessError("Command failed"),
        AudioError("Invalid audio data")
    ])
    def test_print_audio_info_error_handling(self, processor, error_type, caplog):
        """Test error handling in audio info printing"""
        with patch.object(processor, 'get_audio_info', side_effect=error_type):
            processor.print_audio_info(Path("test.mkv"))
            assert "Failed to get audio information" in caplog.text
