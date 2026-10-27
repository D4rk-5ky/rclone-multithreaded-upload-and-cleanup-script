# Project maintenance workflow

These requirements come from the project owner and apply to this entire project.

- Inspect the complete source tree, configuration, documentation and test inventory before editing code. Understand the stage barriers, prerequisite checks, quotas, delete modes, lock ownership and reporting boundaries.
- Preserve every necessary original file. Do not remove files or change safety behavior without the owner's explicit request. Reuse existing functions/modules and avoid duplicate implementations.
- Increment every created version by 0.0.1: 0.0.99 rolls over to 0.1.0. Keep the package version, CLI output, examples and release documentation consistent.
- Keep README.md focused on current usage, with every supported command/flag and configuration option explained or linked to its detailed current guide. Keep the owner's disclaimer verbatim and ensure any names/links refer to this project.
- Record every release's code/documentation/config changes in VERSIONING.md. Keep commented_code_map.md complete for explicit functions, methods and commands, explaining what each does and why.
- Keep both config examples complete for canonical configurable options. Keep completion notifications and HA live preview independently configurable.
- Run compile/syntax checks, harmless CLI checks and relevant offline tests. Never run a real destructive job merely to validate a release. State what could not be tested.
- Package the complete project into a clean ZIP. Exclude __pycache__, .pyc, .pyo, build caches and temporary files. Include required original files and all intended new source/docs/tests/release metadata.
- Verify the final ZIP manifest against the supplied original project and previous release, and verify file hashes/ZIP integrity. Include a readable verification report and release manifest.
- Do not spawn subagents unless the user explicitly requests delegation.
