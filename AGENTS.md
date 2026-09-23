# Gen agent instructions

Each subdirectory containing a `generate-project.py` is one project
generator. The root `gen` executable dispatches `gen <name> [args...]` to
it; keep that dispatch generic so new generators need no registration. Keep
the root to the dispatcher plus docs; all template content lives in the
generator directories.
