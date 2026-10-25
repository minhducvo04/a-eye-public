# Prepare a public snapshot

Never publish this private repository or its history. Review the working-tree diff, then track the new
publication scripts, tests and documentation in the private index (a commit is not required). The exporter
uses `git ls-files`: untracked additions are deliberately not exported. Deleted files stay deleted.

```sh
python3 scripts/check_public.py
python3 scripts/export_public.py /tmp/a-eye-public
```

The output path must not exist and must be outside this checkout. The exporter copies current tracked file
contents into a temporary directory, checks every exported file without Git, and only then publishes the
output directory. It does not create a repository, commit, push, or rewrite history. The owner can review this
folder and create a fresh repository with one first commit.

Excluded: Git metadata, agent configuration, all directories named `data`, private notes and result receipts,
media/images, keys, logs and environment files except `.env.example`. Only the explicit public documentation
allowlist in `scripts/export_public.py` is included. Symlinks and unreviewed binary files fail export.
Consequently, some historical documentation links point to omitted notes. Board tables, evaluation/training
datasets and media fixtures must be supplied locally before running workflows that depend on them; this is
an intentionally data-free source export, not a runnable copy of every private test fixture.

The checker detects known identities, home paths, tailnet addresses/hosts, emails/SSH targets, phone numbers,
Cloudflare team domains and audience tags, and Apple signing team IDs. Diagnostics omit matched values.
Reserved example identities, the standard tailnet network range and three exact synthetic cloud-meter test
addresses are allowed. It scans binary files neither visually nor with OCR; the exporter excludes media and
rejects other binary content. It cannot recognize every possible human name or arbitrary private note.
Review new prose and expand its patterns/allowlist when adding new infrastructure.

Set `OLLAMA_URL`, `BOX_URL` and optionally `ROUTER_OLLAMA_URL` in the environment or private `.env`.
Runtime services default to loopback, never a team machine. Benchmark entry points require their service
URLs explicitly. `boxsvc/run_box.ps1` requires `PC_TAILSCALE_IP`, `BOX_VENV`, `STT_MODEL`, `YOLO_WEIGHTS` and
`KOKORO_DIR`; it refuses startup if any is missing. Overnight scripts require the paths named in
`.env.example`. They must be exported in the invoking shell; the scripts do not source `.env` themselves.

The generic device system unit uses the dedicated `aeye` account and `/opt/a-eye`. Create that account and
install there, or configure the unit locally before enabling it. Xcode signing belongs in ignored
`Local.xcconfig` or command-line build settings. The sample bundle identifier is `com.example.aeye`; use
your own identifier for device provisioning. Changing the identifier/keychain service means previously saved
app tokens must be entered again.
