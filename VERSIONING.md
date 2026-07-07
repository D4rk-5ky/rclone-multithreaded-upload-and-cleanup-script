# Versioning

The project uses a three-component version number and increments every created project version by `0.0.1`.

Rollover rule:

```text
0.0.98 -> 0.0.99 -> 0.1.0 -> 0.1.1
```

`0.0.100` is not used.

## 0.0.17

README remote-backup data-loss warning correction release.

### Code

- Incremented the application version from `0.0.16` to `0.0.17`.
- Did not move, redesign, or intentionally change cleanup, reservation, upload, trash, locking, result, backend-delete, configuration, or final-verification logic.
- Preserved the current modularization boundary: all seven dataclasses remain in `rclone_multithreaded_upload/models.py`, and all 72 application functions remain in `rclone-multithreaded-upload.py`.

### Documentation

- Added a project-specific `Data Loss Warning` directly after the required top disclaimer and before the project title.
- Explicitly warns that cleanup, age-based deletion, file-count limits, directory-size limits, and total remote-size enforcement can permanently delete remote files.
- Explicitly warns that incorrect paths, filters, age settings, or limits can delete valid online backup copies and leave the user without a usable online backup.
- States that a remote managed by this script should not be the user's only backup.
- Recommends testing with disposable data or a test remote, running `--validate-config`, reviewing effective remote paths and cleanup limits, and keeping a separate working backup the script cannot delete.
- Updated `README.md` to application version `0.0.17`.
- Updated `commented_code_map.md` to version `0.0.17` and recorded that no module extraction occurred in this documentation safety correction release.
- Kept `output.py` tracked as the next recommended low-risk module extraction.

### Configuration

- Preserved `config.json` unchanged from v0.0.16.
- Preserved `config.example.json` unchanged because no config option was added, removed, or renamed.

### Packaging and verification

- Preserved every required v0.0.16 project file.
- Re-ran compilation, CLI, config-validation, README-prefix/warning, structural parity, config identity, documentation coverage, and zip-manifest checks.

## 0.0.16

README disclaimer update release.

### Code

- Incremented the application version from `0.0.15` to `0.0.16`.
- Did not move, redesign, or intentionally change cleanup, reservation, upload, trash, locking, result, backend-delete, configuration, or final-verification logic.
- Preserved the current modularization boundary: all seven dataclasses remain in `rclone_multithreaded_upload/models.py`, and all 72 application functions remain in `rclone-multithreaded-upload.py`.

### Documentation

- Replaced the previous README disclaimer block with the updated Disclaimer & Responsibility and AI-assisted/vibe-coded disclaimer text requested for the project.
- Kept the disclaimer as the absolute first README content, before the project title.
- Removed the previous Btrfs-specific Data Loss Warning because Btrfs subvolume and snapshot deletion are not features of `rclone-multithreaded-upload`.
- Updated `README.md` to the current application version `0.0.16`.
- Updated `commented_code_map.md` to version `0.0.16` and recorded that no module extraction occurred in this documentation release.
- Kept `output.py` tracked as the next recommended low-risk module extraction.

### Configuration

- Preserved `config.json` unchanged from v0.0.15.
- Preserved `config.example.json` unchanged because no config option was added, removed, or renamed.

### Packaging and verification

- Preserved every required v0.0.15 project file.
- Re-ran compilation, CLI, config-validation, README-prefix, structural parity, config identity, documentation coverage, and zip-manifest checks.

## 0.0.15

README disclaimer/compliance release.

### Code

- Incremented the application version from `0.0.14` to `0.0.15`.
- Did not move, redesign, or intentionally change any cleanup, reservation, upload, trash, locking, result, backend-delete, configuration, or final-verification logic.
- Preserved the v0.0.14 modularization boundary: all seven dataclasses remain in `rclone_multithreaded_upload/models.py`, and all 72 application functions remain in `rclone-multithreaded-upload.py`.

### Documentation

- Added the required AI-assisted/vibe-coded experimental-software disclaimer verbatim at the absolute top of `README.md`, before the project title or any other README content.
- Updated `README.md` to the current application version `0.0.15`.
- Updated `commented_code_map.md` to version `0.0.15` and recorded that no module extraction occurred in this documentation-only release.
- Kept `output.py` tracked as the next recommended low-risk module extraction.

### Configuration

- Preserved `config.json` unchanged from v0.0.14.
- Preserved `config.example.json` unchanged because no config option was added, removed, or renamed.

### Packaging and verification

- Preserved every required v0.0.14 project file.
- Re-ran compilation, CLI, config-validation, README-prefix, structural parity, config identity, and zip-manifest checks.

## 0.0.14

First modularization release: safest shared-model extraction.

### Code

- Incremented the application version from `0.0.13` to `0.0.14`.
- Added the internal `rclone_multithreaded_upload` package.
- Added `rclone_multithreaded_upload/models.py`.
- Moved all seven existing dataclasses from `rclone-multithreaded-upload.py` into `models.py` without redesigning their fields or defaults.
- Updated the executable to import the seven shared model classes from `models.py`.
- Removed the now-unneeded `dataclass` and `field` imports from the executable.
- Kept all 72 top-level application functions in `rclone-multithreaded-upload.py`.
- Did not intentionally change cleanup, reservation, upload, trash, locking, result, backend-delete, or final-verification algorithms.

### Modularization tracking

Moved in this version:

- `DirectoryCleanupRule`
- `UploadDirectory`
- `CleanupTarget`
- `RemoteFile`
- `RemoteQuotaFile`
- `StageRunResult`
- `RemoteRunResult`

Next recommended module extraction:

- `output.py` for `OUTPUT_LOCK`, `OUTPUT_SEPARATOR`, `print_step()`, `print_error()`, and `print_job_block()`.
- Then `results.py` for per-remote stage/result state and final summary handling.
- Keep `config.py` as an early target, but extract it after resolving its shared dependencies on size parsing, upload-command validation, and runtime-global updates.

### Configuration

- Preserved `config.json` unchanged from v0.0.13.
- Preserved `config.example.json` unchanged because no config option was added, removed, or renamed.

### Documentation

- Updated `README.md` for the current modular project file layout.
- Updated `commented_code_map.md` to map `models.py`, list every moved class, explain why it was the safest first extraction, and track the next module target.
- Updated the modularization roadmap with `models.py` marked complete and `output.py` marked as the next lowest-risk extraction after dependency review.

### Packaging and verification

- Preserved every required v0.0.13 project file.
- Added the package marker and models module to the release manifest.
- Re-ran compile, import, CLI, config-validation, structural inventory, config identity, documentation coverage, and zip-manifest checks.

## 0.0.13

Project identity correction release.

### Code

- Incremented the application version from `0.0.12` to `0.0.13`.
- Restored the original project/application name `rclone-multithreaded-upload`.
- Renamed the executable from `rclone_cctv_cleanup.py` to `rclone-multithreaded-upload.py`.
- Updated the source header to use the correct project name.
- No cleanup, reservation, upload, trash, verification, locking, config, or backend-delete algorithm was intentionally changed.

### Configuration

- Preserved `config.json` unchanged from v0.0.12, including the `12G` GDrive and Mega total/root cleanup limits.
- Preserved `config.example.json` unchanged because no config option was added, removed, or renamed.

### Documentation

- Updated `README.md` to use the correct project name and executable commands.
- Updated `commented_code_map.md` to map the correctly named executable and future package namespace.
- Kept earlier naming mistakes documented in historical version entries rather than rewriting version history.

### Packaging and verification

- Renamed the release directory and archive to `rclone-multithreaded-upload-v0.0.13`.
- Re-ran compile, CLI, config-validation, AST parity, config identity, documentation coverage, and zip-manifest checks.

## 0.0.12

Unused-helper cleanup and production quota configuration update.

### Code

- Incremented the application version from `0.0.11` to `0.0.12`.
- Removed the unused `run_upload_reservation_phase()` helper after confirming it had no call sites or name references outside its own definition.
- Removed the unused `run_upload_phase()` helper after confirming it had no call sites or name references outside its own definition.
- Preserved the active `run_reservation_and_upload_phase()` and `reserve_and_upload_one_remote()` execution path.
- No cleanup, reservation, upload, trash, verification, locking, or backend-delete algorithm was intentionally changed.

### Configuration

- Changed GDrive `max_total_size` from `10G` to `12G`.
- Changed the GDrive root cleanup rule `max_size` from `10G` to `12G`.
- Changed Mega `max_total_size` from `6G` to `12G`.
- Changed the Mega root cleanup rule `max_size` from `10G` to `12G`.
- Left the OneDrive limits unchanged.

### Documentation

- Updated `README.md` for version `0.0.12` and current project-file wording.
- Removed the two deleted helper functions from `commented_code_map.md`.
- Updated `commented_code_map.md` to describe the current `0.0.12` single-module implementation.
- `config.example.json` remains complete because no supported configuration option was added, removed, or renamed.

### Packaging and verification

- Re-ran Python compilation, CLI, config validation, function-reference, documentation coverage, JSON, and package-manifest checks.
- Verified the release archive contains all required project files and no Python bytecode/cache or temporary files.

## 0.0.11

Workspace-compliance release based on the uploaded 0.0.10 project.

### Code

- Incremented the application version from `0.0.10` to `0.0.11`.
- Preserved cleanup, reservation, upload, trash, verification, locking, backend-detection, and final-result behavior.
- No cleanup or upload algorithm was intentionally changed in this release.
- No existing top-level functions were removed, including the currently non-main-path `run_upload_reservation_phase()` and `run_upload_phase()` helpers.

### Documentation

- Added `README.md` documenting only the current application behavior and all current CLI/configuration options.
- Added `commented_code_map.md` mapping every dataclass, top-level function, main execution phase, and external rclone command used by the code.
- Added `VERSIONING.md` as the maintained project change log.
- Added `config.example.json` with neutral paths/remotes and every supported root, thread, upload, and cleanup-rule configuration field.
- Preserved the uploaded `config.json` baseline configuration as an original project file.

### Packaging and verification

- Standardized the main script filename inside the project package as `rclone_cctv_cleanup.py`.
- Excluded Python bytecode/cache and temporary build/test files from the release zip.
- Verified Python compilation.
- Verified JSON parsing for `config.json` and `config.example.json`.
- Verified `--help`, `--version`, and `--validate-config` against both configurations.
- Verified the final zip manifest and confirmed that the two baseline file roles remain represented: the Python application and the original project configuration.

## 0.0.10

Imported baseline supplied for this workspace.

The baseline already contained the current multi-remote cleanup, size reservation, upload, backend-aware deletion, post-upload cleanup, final quota verification, and per-remote result logic.

Detailed earlier version history was not supplied with the baseline, so this file does not invent changes for versions before 0.0.10.
