# Tekton Pipeline Maintenance

## Pipeline Files

- `.tekton/pipeline-build-multiarch.yaml`: Konflux multi-arch build Pipeline with task references pinned to `quay.io/konflux-ci/tekton-catalog/<task>:<version>@sha256:<digest>`.
- `.tekton/pull_request.yaml`: PipelineRun triggered on PR events. Uses `Containerfile-source`.
- `.tekton/push.yaml`: PipelineRun triggered on push to main/release branches. Uses `Containerfile-source`.
- `.tekton/task-get-version.yaml`: Local Task (not from catalog, no version tracking needed).

Renovate tracks Tekton task updates automatically via [org-level inherited config](https://github.com/rhel-lightspeed/renovate-config) (weekends schedule, no automerge).

## Auditing Task Versions

To check whether pinned tasks are current:

1. Extract task references: `grep 'quay.io/konflux-ci/tekton-catalog/' .tekton/pipeline-build-multiarch.yaml`
2. For each task, list available version tags:
   ```bash
   skopeo list-tags docker://quay.io/konflux-ci/tekton-catalog/<task> \
     | jq -r '.Tags[]' | grep -E '^[0-9]+\.[0-9]+(\.[0-9]+)?$' | sort -V | tail -5
   ```
3. Get the latest digest for the current (or newer) version tag:
   ```bash
   skopeo inspect docker://quay.io/konflux-ci/tekton-catalog/<task>:<version> | jq -r '.Digest'
   ```
4. Compare the canonical upstream pipeline to detect missing/added tasks or structural changes:
   ```bash
   curl -sL https://raw.githubusercontent.com/konflux-ci/build-definitions/main/pipelines/docker-build-multi-platform-oci-ta/docker-build-multi-platform-oci-ta.yaml
   ```

**zsh gotcha**: The bash tool runs in zsh. Bash-only syntax like `declare -A` associative arrays will fail. Write the script to a temp file and run it with `bash /tmp/script.sh` instead.

## Known Gaps (as of 2026-06-30)

**Missing task**: `source-build-oci-ta:0.3` — builds a source container image. Present in the canonical pipeline but absent from ours. Runs after `build-image-index`, gated by a `build-source-image` param. Needs `BINARY_IMAGE`, `BINARY_IMAGE_DIGEST`, `SOURCE_ARTIFACT`, `CACHI2_ARTIFACT` params.

**Matrix strategy migrations**: The canonical pipeline uses `matrix.params` for per-platform execution on these tasks, but our pipeline does not:
- `clair-scan` (matrix on `image-platform`)
- `clamav-scan` (matrix on `image-arch`)
- `ecosystem-cert-preflight-checks` (matrix on `platform`)

Adopting matrix strategies requires adding `matrix.params` blocks and adjusting the task param wiring. This is a structural change, not just a version bump.

**Patch version divergence**: Our `clair-scan` and `clamav-scan` may use patch versions ahead of the canonical pipeline. These patch versions exist in the catalog and are valid, but may drift from canonical expectations.
