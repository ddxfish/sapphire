# core/backup_openers.py — the files that sit BESIDE the backups so "pop in
# and restore" works with no Sapphire running: a shell script, a batch file,
# the standalone decryptor and a README. Written by Backup.ensure_openers()
# into the local backup folder; pushed to local-path and satellite targets
# by backup_targets. Pure ASCII, CRLF for the .bat (Windows cmd is cp1252).
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

README = """Sapphire backups

Each sapphire_*.tar.gz is a plain archive of Sapphire's user/ folder.
Each sapphire_*.sapphirebak is the same archive, encrypted with your backup password.

To open one:     ./open-backup.sh      (Linux / macOS, in a terminal)
                 open-backup.bat       (Windows, double-click)
It asks which backup, unpacks it into a folder beside it, and tells you where.
Encrypted backups ask for your password and need Python 3 with the
"cryptography" package (pip install cryptography).

To restore:      Sapphire > Settings > Backup > Restore from a file (upload the backup),
                 or stop Sapphire and copy the unpacked user/ over Sapphire's user/ folder.

Your password is never stored here. If it is lost, nobody can open the encrypted backups.
"""

SH = r"""#!/bin/sh
# Opens a Sapphire backup from this folder into a folder beside it.
# Plain .tar.gz needs only tar. Encrypted .sapphirebak needs python3, the
# "cryptography" package and your backup password (never stored here).
cd "$(dirname "$0")" || exit 1
f="$1"
if [ -z "$f" ]; then
  echo "Backups here:"
  i=0
  for x in sapphire_*.tar.gz sapphire_*.sapphirebak; do
    [ -e "$x" ] || continue
    i=$((i+1)); echo "  $i) $x"
  done
  [ "$i" -eq 0 ] && { echo "  none found"; exit 1; }
  printf "Which one? [1-%s] " "$i"; read -r n
  f=$(for x in sapphire_*.tar.gz sapphire_*.sapphirebak; do [ -e "$x" ] && echo "$x"; done | sed -n "${n}p")
fi
[ -f "$f" ] || { echo "No such file: $f"; exit 1; }
out="${f%.tar.gz}"; out="${out%.sapphirebak}"
mkdir -p "$out" || exit 1
case "$f" in
  *.sapphirebak)
    command -v python3 >/dev/null 2>&1 || { echo "python3 is needed for encrypted backups"; exit 1; }
    python3 decrypt_backup.py "$f" "$out/backup.tar.gz" || exit 1
    tar -xzf "$out/backup.tar.gz" -C "$out" || exit 1
    rm -f "$out/backup.tar.gz" ;;
  *)
    tar -xzf "$f" -C "$out" || exit 1 ;;
esac
echo "Opened into: $out/user"
echo "Restore: Sapphire > Settings > Backup > Restore from a file (upload the original),"
echo "or stop Sapphire and copy $out/user over Sapphire's user/ folder."
"""

BAT = r"""@echo off
rem Opens a Sapphire backup from this folder into a folder beside it.
rem Plain .tar.gz: Windows 10 or newer (tar is built in).
rem Encrypted .sapphirebak: also needs Python 3 with "pip install cryptography"
rem and your backup password (never stored here).
setlocal enabledelayedexpansion
cd /d "%~dp0"
set "f=%~1"
if not "%f%"=="" goto open
echo Backups here:
set i=0
for %%x in (sapphire_*.tar.gz sapphire_*.sapphirebak) do (
    set /a i+=1
    set "pick!i!=%%x"
    echo   !i!^) %%x
)
if %i%==0 echo   none found & pause & exit /b 1
set /p n="Which one? [1-%i%] "
set "f=!pick%n%!"
:open
if not exist "%f%" echo No such file: %f% & pause & exit /b 1
set "out=%f:.tar.gz=%"
set "out=%out:.sapphirebak=%"
if not exist "%out%" mkdir "%out%"
if /i "%f:~-12%"==".sapphirebak" goto enc
tar -xzf "%f%" -C "%out%" || goto fail
goto done
:enc
where python >nul 2>nul || (echo Python 3 is needed for encrypted backups & pause & exit /b 1)
python decrypt_backup.py "%f%" "%out%\backup.tar.gz" || goto fail
tar -xzf "%out%\backup.tar.gz" -C "%out%" || goto fail
del "%out%\backup.tar.gz"
:done
echo Opened into: %out%\user
echo Restore: Sapphire ^> Settings ^> Backup ^> Restore from a file (upload the original),
echo or stop Sapphire and copy %out%\user over Sapphire's user\ folder.
pause
exit /b 0
:fail
echo Failed.
pause
exit /b 1
"""


def _put(path: Path, text: str, newline: str = "\n", mode=None) -> bool:
    """Write when missing or different. Returns True when written."""
    data = text.replace("\n", newline).encode("ascii")
    try:
        if path.exists() and path.read_bytes() == data:
            return False
        path.write_bytes(data)
        if mode is not None:
            try:
                path.chmod(mode)
            except OSError:
                pass
        return True
    except OSError as e:
        logger.warning(f"Could not write {path.name}: {e}")
        return False


FILES = (
    ("README.txt", README, "\n", None),
    ("open-backup.sh", SH, "\n", 0o755),
    ("open-backup.bat", BAT, "\r\n", None),
)
NAMES = tuple(n for n, *_ in FILES) + ("decrypt_backup.py",)


def write_all(folder, decrypt_tool) -> int:
    """Write every opener into `folder` when missing or stale. Returns how
    many were written. decrypt_backup.py rides along verbatim from tools/
    (the standalone mirror of backup_crypto)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    n = sum(1 for name, text, nl, mode in FILES if _put(folder / name, text, nl, mode))
    dst = folder / "decrypt_backup.py"
    try:
        src_bytes = Path(decrypt_tool).read_bytes()
        if not dst.exists() or dst.read_bytes() != src_bytes:
            shutil.copyfile(decrypt_tool, dst)
            n += 1
    except OSError as e:
        logger.warning(f"Could not copy decrypt_backup.py: {e}")
    if n:
        logger.info(f"Backup openers written to {folder} ({n} file(s))")
    return n
