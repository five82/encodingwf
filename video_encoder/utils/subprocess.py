#!/usr/bin/env python3

import subprocess
import logging
import os
import sys
import select
from typing import List, Optional, Tuple
from pathlib import Path

logger = logging.getLogger()

def run_command(
    cmd: List[str],
    desc: str,
    check: bool = True,
    show_output: bool = True
) -> Tuple[int, str, str]:
    """
    Run a command with real-time output logging using select for non-blocking reads

    Args:
        cmd: Command to run as list of strings
        desc: Description of the command for logging
        check: Whether to raise exception on non-zero return code
        show_output: Whether to show output in real-time

    Returns:
        Tuple of (return_code, stdout, stderr)

    Raises:
        subprocess.CalledProcessError: If check=True and return code is non-zero
    """
    logger.info(f"Running {desc}: {' '.join(cmd)}")

    # Create process with unbuffered pipes
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,  # Unbuffered
        universal_newlines=True,
        env=dict(os.environ, PYTHONUNBUFFERED="1")  # Force Python unbuffered output
    )

    # Get file descriptors for select
    stdout_fd = process.stdout.fileno()
    stderr_fd = process.stderr.fileno()

    # Collection for output
    stdout_lines = []
    stderr_lines = []

    # Read until process completes
    while True:
        reads = [stdout_fd, stderr_fd]
        ret = select.select(reads, [], [])

        for fd in ret[0]:
            if fd == stdout_fd:
                line = process.stdout.readline()
                if line:
                    stdout_lines.append(line)
                    if show_output:
                        logger.info(line.rstrip())
            if fd == stderr_fd:
                line = process.stderr.readline()
                if line:
                    stderr_lines.append(line)
                    if show_output:
                        logger.warning(line.rstrip())

        # Check if process has finished
        if process.poll() is not None:
            # Read any remaining output
            for line in process.stdout:
                stdout_lines.append(line)
                if show_output:
                    logger.info(line.rstrip())
            for line in process.stderr:
                stderr_lines.append(line)
                if show_output:
                    logger.warning(line.rstrip())
            break

    return_code = process.wait()
    stdout = ''.join(stdout_lines)
    stderr = ''.join(stderr_lines)

    if check and return_code != 0:
        raise subprocess.CalledProcessError(
            return_code,
            cmd,
            stdout,
            stderr
        )

    return return_code, stdout, stderr
