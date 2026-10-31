"""Start local development; optionally update a clean checkout from GitHub first."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
BRANCH = 'codex/proposal-fit-site'


def git_output(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def update_checkout(root=ROOT):
    if not shutil.which('git'):
        raise RuntimeError('Install Git for Windows: https://git-scm.com/download/win ; then reopen PowerShell.')
    try:
        top = Path(git_output(root, 'rev-parse', '--show-toplevel')).resolve()
    except subprocess.CalledProcessError:
        raise RuntimeError('This folder was downloaded as ZIP. Clone the repository once; see README.md.') from None
    if top != root.resolve():
        raise RuntimeError('The project must be its own Git checkout. See README.md.')
    if git_output(root, 'status', '--porcelain'):
        raise RuntimeError('Local changes found. Save them with Git before updating, or run without --update. No files were changed.')
    if git_output(root, 'branch', '--show-current') != BRANCH:
        raise RuntimeError(f'Updates are configured for {BRANCH}. Your branch was not changed. Run without --update to develop locally.')
    subprocess.run(['git', '-C', str(root), 'pull', '--ff-only', 'origin', BRANCH], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--update', action='store_true', help='Pull updates before startup; refuse local edits or diverged history.')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    if sys.version_info < (3, 12):
        parser.error('Python 3.12 or newer is required.')
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535.')
    if args.update:
        update_checkout()
        # Re-enter the updated launcher once; new setup logic takes effect immediately.
        subprocess.run([sys.executable, str(ROOT / 'dev.py'), '--port', str(args.port)], check=True)
        return
    venv = ROOT / '.venv'
    python = venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(ROOT / 'requirements.txt')], cwd=ROOT, check=True)
    print(f'Open http://127.0.0.1:{args.port} in your browser. Stop with Ctrl+C.', flush=True)
    print('Python files reload automatically. Refresh the browser after HTML/CSS/JS edits.', flush=True)
    subprocess.run([str(python), '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', str(args.port), '--reload', '--reload-dir', str(ROOT)], cwd=ROOT, check=True)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'Startup stopped: {exc}', file=sys.stderr)
        sys.exit(1)
