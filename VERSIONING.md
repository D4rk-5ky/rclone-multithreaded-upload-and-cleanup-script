# Versioning

Version format: `MAJOR.MINOR.PATCH`.

Patch increments by `0.0.1` for each created version.

Rollover rule:

```text
0.0.99 → 0.1.0
```

Do not create `0.0.100`.

## 0.0.2

Current created version.

Changes:

- Added `.gitignore` with safe ignore patterns for private config files, TOML/config files, environment files, runtime logs, Python caches, build/test caches, and temporary editor files.
- Kept example config files trackable with explicit negation rules, including `rclone-cctv-config.example.json`.
- Updated `README.md` so the current project file list includes `.gitignore`.
- Updated `commented_code_map.md` with the repository hygiene purpose of `.gitignore`.
- Bumped script version from `0.0.1` to `0.0.2`.

## 0.0.1

Current created version.

Changes:

- Moved hard-coded runtime settings out of `rclone-multithreaded-upload.py` and into an external JSON config file.
- Added required `--config` / `-c` CLI option.
- Added `--validate-config` for safe config loading and startup-summary checking without creating the lock file or running rclone commands.
- Added `--version`.
- Added `rclone-cctv-config.example.json` with all currently available config options.
- Kept the original cleanup-before-upload execution order.
- Kept the existing upload command allow-list: `copy`, `sync`, and `move`.
- Kept direct-delete versus trash/default delete behavior.
- Kept remote-wide quota cleanup behavior.
- Kept missing remote folder handling as non-fatal.
- Kept unsupported `rclone cleanup` handling as non-fatal.
- Updated `README.md` for current external-config behavior only.
- Added `commented_code_map.md` explaining each function/command and why it exists.
