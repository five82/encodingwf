#!/usr/bin/env python3

from pathlib import Path
from typing import Optional

class VideoEncoderError(Exception):
    """Base exception for all video encoder errors"""
    def __init__(self, message: str, file: Optional[Path] = None):
        self.file = file
        self.message = message
        super().__init__(self.formatted_message)

    @property
    def formatted_message(self) -> str:
        if self.file:
            return f"{self.message} (File: {self.file})"
        return self.message

class ValidationError(VideoEncoderError):
    """Raised when file validation fails"""
    pass

class FileNotFoundError(VideoEncoderError):
    """Raised when a required file is missing"""
    pass

class FileSizeError(ValidationError):
    """Raised when file size is below minimum"""
    def __init__(self, file: Path, size: int, min_size: int):
        message = f"File size ({size} bytes) is below minimum required size ({min_size} bytes)"
        super().__init__(message, file)

class InvalidVideoError(ValidationError):
    """Raised when video file is corrupted or invalid"""
    pass

class FFmpegError(VideoEncoderError):
    """Raised when FFmpeg command fails"""
    def __init__(self, message: str, command: str, output: Optional[str] = None):
        self.command = command
        self.output = output
        full_message = f"{message}\nCommand: {command}"
        if output:
            full_message += f"\nOutput: {output}"
        super().__init__(full_message)

class AudioError(VideoEncoderError):
    """Raised when audio processing fails"""
    pass

class SegmentationError(VideoEncoderError):
    """Raised when video segmentation fails"""
    pass

class EncodingError(VideoEncoderError):
    """Raised when video encoding fails"""
    pass

class ConcatenationError(VideoEncoderError):
    """Raised when segment concatenation fails"""
    pass

class RemuxError(VideoEncoderError):
    """Raised when remuxing video and audio fails"""
    pass

class ProcessError(VideoEncoderError):
    """Raised when subprocess execution fails"""
    def __init__(self, message: str, cmd: list, return_code: int, stdout: str, stderr: str):
        self.cmd = cmd
        self.return_code = return_code
        self.stdout = stdout
        self.stderr = stderr

        full_message = (
            f"{message}\n"
            f"Command: {' '.join(str(x) for x in cmd)}\n"
            f"Return code: {return_code}\n"
            f"stdout: {stdout}\n"
            f"stderr: {stderr}"
        )
        super().__init__(full_message)

class CleanupError(VideoEncoderError):
    """Raised when cleanup operations fail"""
    pass

def handle_ffmpeg_error(cmd: list, returncode: int, output: str) -> None:
    """Helper function to handle FFmpeg errors consistently"""
    error_msg = f"FFmpeg command failed with return code {returncode}"

    if "No such file or directory" in output:
        raise FileNotFoundError(error_msg, Path(cmd[cmd.index("-i") + 1]))
    elif "Invalid data found when processing input" in output:
        raise InvalidVideoError(error_msg, Path(cmd[cmd.index("-i") + 1]))
    else:
        raise FFmpegError(error_msg, " ".join(str(x) for x in cmd), output)
