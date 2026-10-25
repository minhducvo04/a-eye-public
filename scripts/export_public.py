"""Export tracked working-tree contents only; never copy or rewrite Git history."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

try:
    from .check_public import tracked_files
except ImportError:
    from check_public import tracked_files

# Internal receipts, screenshots and personal handoffs are not publication content.
PRIVATE_DIRS = {'.git', '.claude', '.codex', '.agents', 'data', 'private', 'notes',
                'results', 'screenshots', 'ui-parity', 'ui-polish', 'clips', 'frames', 'recordings'}
PUBLIC_DOCS = {'README.md', 'api.md', 'data-export.md', 'device.md', 'features.md',
               'headset-demo.md', 'home-setup.md', 'privacy.md', 'wiring.md', 'public-export.md'}
PRIVATE_FILES = {'RESULT.md', 'CLAUDE.md', 'ios-dad-handoff.md', 'devpost-draft.md'}
MEDIA = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.pdf', '.mp4', '.mov', '.wav', '.m4a'}


def excluded(path):
    return ((path.parts[0] == 'docs' and (len(path.parts) != 2 or path.name not in PUBLIC_DOCS))
            or bool(set(path.parts) & PRIVATE_DIRS) or path.name in PRIVATE_FILES
            or (path.name.startswith('.env') and path.name != '.env.example')
            or path.suffix.lower() in MEDIA or path.suffix.lower() in {'.pem', '.key', '.log'}
            or any(word in path.name.lower() for word in ('private', 'handoff', 'personal-note'))
            and path.suffix.lower() in {'.md', '.txt'})


def export(source, output):
    source, output = source.resolve(), output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('Output must not already exist')
    if output.resolve().is_relative_to(source):
        raise ValueError('Output must be outside the source tree')
    paths = tracked_files(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with tempfile.TemporaryDirectory(prefix='.public-export-', dir=output.parent) as temp:
        stage = Path(temp) / 'tree'
        stage.mkdir()
        for relative in paths:
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Unsafe tracked path')
            if excluded(relative):
                continue
            src = source / relative
            if src.is_symlink() or not src.resolve().is_relative_to(source):
                raise ValueError('Export refuses symlinks')
            if not src.exists():
                continue
            raw = src.read_bytes()
            try:
                raw.decode('utf-8')
            except UnicodeError:
                raise ValueError('Unreviewed binary file in export') from None
            if b'\0' in raw:
                raise ValueError('Unreviewed binary file in export')
            dest = stage / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
            dest.chmod(src.stat().st_mode & 0o777)
            count += 1
        checker = Path(__file__).with_name('check_public.py')
        subprocess.run([sys.executable, str(checker), '--tree', str(stage)], check=True)
        stage.rename(output)
    print(f'export_public: PASS ({count} files; no Git history, data, private notes or media)')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    try:
        export(Path(__file__).resolve().parents[1], args.output)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        # Do not print subprocess/environment values or file contents.
        parser.exit(1, f'export_public: FAIL ({type(exc).__name__}); output not published\n')


if __name__ == '__main__':
    main()
