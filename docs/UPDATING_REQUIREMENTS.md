# Updating Python Dependencies

To update project dependencies and generate requirements files run `make freeze`.

This resolves dependencies and regenerates the requirements files in a single step. Commit the resulting changes to `uv.lock` and `.konflux/` together.

`make freeze` runs two targets in sequence:

1. **`make lock`** -- Runs `uv lock` to resolve all dependency constraints in `pyproject.toml` and update `uv.lock`.
2. **`make konflux-requirements`** -- Runs `scripts/konflux_requirements.py` to regenerate the requirements files in `.konflux/` from the updated `uv.lock`.

These can be run independently if needed.

## CI Verification

CI runs `make check-konflux-requirements` to verify that the `.konflux/` manifests match `uv.lock`. If you update `uv.lock` without regenerating the manifests, CI will fail with:

```
FAIL: .konflux manifests are stale. Run 'make konflux-requirements' and commit.
```

Using `make freeze` avoids this by keeping both artifacts in sync.

## RPM Dependencies

If you are changing build-toolchain RPM packages (in `rpms.in.yaml`), that has a separate workflow:

```bash
make rpm-lock
```

This regenerates `rpms.lock.yaml` from `rpms.in.yaml` using the builder image. See [CONTAINER_BUILD.md](CONTAINER_BUILD.md) for details on the hermetic build process.
