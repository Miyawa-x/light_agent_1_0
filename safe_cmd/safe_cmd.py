import subprocess
import re
from typing import Optional, List
from pathlib import Path

# Basic Windows CMD dangerous command patterns (case-insensitive).
# This is a conservative blocklist, not a complete security solution.
DEFAULT_PATTERNS: List[str] = [
    r'\bformat\b',                     # Format disk
    r'\bdiskpart\b',                   # Disk partition tool
    r'\bshutdown\b',                   # Shutdown/reboot
    r'\bdel\b\s+.+\s+/s',              # Recursive delete
    r'\bdel\b\s+.+\s+/q',              # Quiet delete (often paired with /s)
    r'\brd\b\s+.+\s+/s',               # Recursive remove dir
    r'\brmdir\b\s+.+\s+/s',            # Recursive remove dir
    r'\bdelete\b\s+.*\bshadow',        # Shadow copy deletion (vssadmin)
    r'\bvssadmin\b\s+delete',          # Delete volume shadow copies
    r'\bwbadmin\b\s+delete',           # Delete backups
    r'\bbcdedit\b',                    # Boot config changes
    r'\bbootrec\b',                    # Boot record changes
    r'\bcipher\b\s+/w',                # Wipe free space
]


def _load_patterns_from_file(path: Path) -> List[str]:
    patterns: List[str] = []
    if not path.exists():
        return patterns
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


_RULES_PATH = Path(__file__).resolve().parent / "rules.txt"
_FILE_PATTERNS = _load_patterns_from_file(_RULES_PATH)
DANGEROUS_PATTERNS = _FILE_PATTERNS if _FILE_PATTERNS else DEFAULT_PATTERNS

COMPILED_PATTERNS = [re.compile(pat, re.IGNORECASE) for pat in DANGEROUS_PATTERNS]


def is_command_safe(command: str) -> bool:
    for pattern in COMPILED_PATTERNS:
        if pattern.search(command):
            return False
    return True


def run_safe_cmd(
    command: str,
    timeout: Optional[int] = None,
    use_cmd: bool = True,
    cwd: Optional[str] = None,
) -> subprocess.CompletedProcess:
    """
    Safely execute a Windows command string with a basic blocklist.

    Args:
        command: Command string to execute.
        timeout: Timeout in seconds.
        use_cmd: If True, runs via "cmd.exe /c" with shell=False.
    """
    if not isinstance(command, str):
        raise TypeError("Command must be a string.")

    if not is_command_safe(command):
        raise ValueError(f"Unsafe command detected and blocked: {command}")

    if use_cmd:
        args = ["cmd.exe", "/c", command]
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            check=False,
        )

    return subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
        check=False,
    )
