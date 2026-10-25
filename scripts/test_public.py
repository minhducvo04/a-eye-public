"""Publication boundary tests use only constructed, synthetic identifiers."""
from pathlib import Path
import subprocess

import pytest

from scripts.check_public import findings, main
from scripts.export_public import export


@pytest.mark.parametrize('value', [
    '100.' + '99.88.77', '/Users/' + 'sample', '/home/' + 'sample',
    'person' + '@private.invalid', 'ssh operator' + '@host',
    'tenant' + '.cloudflareaccess.com', 'pc.tail123.ts.' + 'net',
    '415-' + '555-0123', 'CF_ACCESS_AUD=' + 'a' * 64,
    'DEVELOPMENT_TEAM = ' + 'A' * 10, 'Ki' + 'etPC', 'Du' + 'c',
])
def test_detects_identifiers(value):
    assert list(findings(value))


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)


def test_tracked_scan_and_git_free_export(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    git(source, 'init')
    (source / 'README.md').write_text('the team: <PC_NAME> <PC_USER> <PC_TAILSCALE_IP> <YOUR_DOMAIN> $HOME\n')
    (source / '.env.example').write_text('PC_TAILSCALE_IP=\n')
    for relative in ['data/private.txt', 'router/data/private.txt', 'docs/notes/note.md',
                     'docs/results/run.md', '.claude/settings.json', '.env', 'photo.png']:
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('private fixture')
    git(source, 'add', '.')
    (source / 'untracked.txt').write_text('not exported')
    output = tmp_path / 'public'
    export(source, output)
    assert sorted(p.name for p in output.iterdir()) == ['.env.example', 'README.md']
    assert main(['--tree', str(output)]) == 0
    # A leak in modified tracked content is detected; failed exports leave no output.
    (source / 'README.md').write_text('100.' + '99.88.77')
    assert main([str(source)]) == 1
    with pytest.raises(subprocess.CalledProcessError):
        export(source, tmp_path / 'rejected')
    assert not (tmp_path / 'rejected').exists()
    with pytest.raises(ValueError):
        export(source, output)


def test_symlink_never_copies_external_content(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    git(source, 'init')
    (source / 'link').symlink_to(tmp_path / 'outside')
    git(source, 'add', 'link')
    with pytest.raises(ValueError, match='symlink'):
        export(source, tmp_path / 'public')


def test_unreviewed_binary_fails_closed(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    git(source, 'init')
    (source / 'opaque.bin').write_bytes(bytes([0, 255]))
    git(source, 'add', '.')
    with pytest.raises(ValueError, match='binary'):
        export(source, tmp_path / 'public')
    assert not (tmp_path / 'public').exists()


def test_guard_sources_scan_clean():
    from scripts.check_public import scan
    root = Path(__file__).resolve().parents[1]
    paths = [Path('scripts') / name for name in
             ('check_public.py', 'export_public.py', 'test_public.py')]
    assert scan(root, paths) == ([], 0)


def test_offline_table_needs_no_service_but_network_run_does(monkeypatch):
    from eval.backends import run
    monkeypatch.delenv('OLLAMA_URL', raising=False)
    monkeypatch.delenv('BOX_URL', raising=False)
    monkeypatch.setattr(run, 'summarize', lambda: [])
    monkeypatch.setattr('sys.argv', ['benchmark', '--table'])
    run.main()
    monkeypatch.setattr('sys.argv', ['benchmark', '--backends', 'ollama:fixture'])
    with pytest.raises(SystemExit, match='Set OLLAMA_URL'):
        run.main()
    monkeypatch.setenv('OLLAMA_URL', 'http://localhost:11434')
    called = []
    monkeypatch.setattr(run, 'run', lambda *args: called.append(args))
    run.main()
    assert called[0][0] == ['ollama:fixture']
