#!/usr/bin/env python3

from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict
from enum import Enum

class EncodePreset(Enum):
    FASTER = 5
    FAST = 6
    MEDIUM = 7
    SLOW = 8
    SLOWER = 9

@dataclass
class EncoderConfig:
    """Configuration settings for video encoder"""
    # Directory settings
    BASE_DIR: Path = Path("/app/videos")
    DIRS: List[str] = field(default_factory=lambda: [
        "input", "working", "output", "segments", "encoded_segments", "logs"
    ])

    # File settings
    MIN_FILE_SIZE: int = 1024  # 1KB minimum file size
    SUPPORTED_FORMATS: List[str] = field(default_factory=lambda: ["mkv", "mp4"])

    # Encoding settings
    SEGMENT_DURATION: str = "00:01:00"
    MIN_VMAF: int = 92
    PRESET: EncodePreset = EncodePreset.FAST
    KEYINT: str = "10s"

    # Audio settings
    AUDIO_BITRATES: Dict[int, int] = field(default_factory=lambda: {
        1: 64,   # Mono
        2: 128,  # Stereo
        6: 256,  # 5.1
        8: 384,  # 7.1
    })

    def get_dir(self, name: str) -> Path:
        """Get full path for a directory"""
        return self.BASE_DIR / name

    def get_all_dirs(self) -> Dict[str, Path]:
        """Get dictionary of all directory paths"""
        return {name: self.get_dir(name) for name in self.DIRS}

    def get_audio_bitrate(self, channels: int) -> int:
        """Get appropriate audio bitrate for number of channels"""
        return self.AUDIO_BITRATES.get(channels, channels * 48)
