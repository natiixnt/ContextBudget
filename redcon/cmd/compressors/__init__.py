"""Per-command compressors. Each compressor parses one command's output."""

from redcon.cmd.compressors.base import Compressor, CompressorContext

__all__ = ["Compressor", "CompressorContext"]
