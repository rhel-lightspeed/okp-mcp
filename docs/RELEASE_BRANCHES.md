# Release Branch Workflow

## Overview

There is always an active release branch. This is what runs in staging (and eventually production). Development continues on `main`. Bugfixes and approved changes go to the release branch first, then get cherry-picked to `main`. New features go to `main` only.

To cut a new release, either update the existing release branch or create a new one.

## Branch Naming

```
release/YYYY-MM-DD
```

Example: `release/2026-04-01`

## Step-by-Step: Setting Up a New Release Branch

### 1. Create the branch

```bash
git checkout main
git pull origin main
git checkout -b release/YYYY-MM-DD
git push origin release/YYYY-MM-DD
```

### 2. GitHub branch protection

A GitHub ruleset already protects all branches matching `release/**` with the same rules as `main` (require PR approval before merging, restrict deletions, block force pushes). No additional setup needed.

### 3. GitLab mirror

The GitLab mirror at `gitlab.cee.redhat.com/rhel-lightspeed/enhanced-shell/okp-mcp` pulls from GitHub. It must be configured to include release branches.

To update the mirror regex:

1. Go to GitLab repo **Settings > Repository > Mirroring repositories**
2. Delete the existing mirror rule (it can't be edited in-place)
3. Create a new Pull mirror with:
   - **Git repository URL**: `https://github.com/rhel-lightspeed/okp-mcp.git` (must end in `.git`)
   - **Mirror direction**: Pull
   - **Mirror branches**: Mirror specific branches
   - **Branch regex**: `(main|release\/.*)`
   - **Overwrite diverged branches**: checked
   - **Trigger pipelines for mirror updates**: checked
4. Click **Mirror repository**
5. Click the sync button (🔄) to trigger an immediate pull
6. Verify the release branch appears in the GitLab branch dropdown

**Note:** "Mirror only protected branches" does not reliably detect GitHub ruleset-based protection. Use "Mirror specific branches" with a regex instead.

### 4. Konflux component

Konflux watches one branch per Component. The `okp-mcp` component watches `main`. A separate component is needed for the release branch to ensure Konflux triggers builds on release branch pushes.

**First time setup (already done):**

1. Go to [Konflux UI](https://konflux-ui.apps.stone-prod-p02.hjvn.p1.openshiftapps.com/)
2. Namespace: `rhel-lightspeed-tenant` > Applications > `okp-mcp` > Components
3. Click **Add component**
4. Fill in:
   - **Git repository url**: `https://gitlab.cee.redhat.com/rhel-lightspeed/enhanced-shell/okp-mcp` (GitLab, not GitHub)
   - **Git reference** (under "Show advanced Git options"): `release/YYYY-MM-DD`
   - **Docker file**: `/Containerfile`
   - **Component name**: `okp-mcp-rel`
   - **Pipeline**: `docker-build-oci-ta`
   - **Context directory**: leave blank
5. Click **Add component**
6. Konflux will auto-commit Tekton pipeline files to the release branch for the new component — this is expected

**For subsequent release branches:**

Konflux components are immutable — you cannot edit the branch. You must delete the old `okp-mcp-rel` component and create a new one pointing to the new release branch. The component name must be unique; if the old name hasn't been freed yet, use a variation (e.g., `okp-mcp-rel-2`).

**Important:** The component must point to the **GitLab** mirror URL, not GitHub. The original `okp-mcp` component uses GitLab, and the Tekton pipeline annotations reference GitLab URLs.

### 5. Verify the build

1. Push a commit to the release branch on GitHub
2. Wait for GitLab mirror to sync (or manually trigger sync via the 🔄 button)
3. Check Konflux Activity tab — a build should appear for the release branch
4. The image will be tagged with the commit SHA in quay.io

**Note:** Both the `okp-mcp` and `okp-mcp-rel` components may build from the release branch. The `okp-mcp` component's custom pipeline (`.tekton/push.yaml`) has a CEL expression that matches `release/` branches, so it builds images to `quay.io/redhat-user-workloads/rhel-lightspeed-tenant/okp-mcp:<sha>`. This is the same quay repo as main — no `IMAGE` change is needed in app-interface.

### 6. Deploy to staging via app-interface

The saas-file schema only allows `ref` values matching `^([0-9a-f]{40}|master|main|internal|stable)$`. Branch names like `release/2026-04-01` are **not permitted**. Use commit SHAs instead.

Update `data/services/insights/rhel-lightspeed/cicd/saas.yml` in app-interface:

1. Set `ref` to the **commit SHA** from each release branch HEAD:
   - `okp-mcp-stage`: SHA from okp-mcp `release/YYYY-MM-DD`
   - `lightspeed-stack-stage`: SHA from lscore-deploy `release/YYYY-MM-DD`
   - `redhat-okp-stage`: SHA from lscore-deploy `release/YYYY-MM-DD`
2. For `okp-mcp-stage`: **remove `IMAGE_TAG`** — when `ref` is a SHA, qontract-reconcile auto-derives `IMAGE_TAG` from it. Konflux tags images with the commit SHA, so `ref` pins both the OpenShift template and the container image. Setting `IMAGE_TAG` to the same value as `ref` causes a validation error.
3. For `lightspeed-stack-stage` and `redhat-okp-stage`: `IMAGE_TAG` stays as-is — these use different image registries where the tag is independent of the `ref` SHA.

Get the SHAs:

```bash
# okp-mcp release branch HEAD
cd ~/Documents/Development/okp-mcp
git rev-parse release/YYYY-MM-DD

# lscore-deploy release branch HEAD
cd ~/Documents/Development/lscore-deploy
git rev-parse release/YYYY-MM-DD
```

**lscore-deploy** also needs a `release/YYYY-MM-DD` branch (it's just config, no CI changes needed):

```bash
cd ~/Documents/Development/lscore-deploy
git checkout main && git pull origin main
git checkout -b release/YYYY-MM-DD
git push origin release/YYYY-MM-DD
```

After merging the app-interface MR (auto-merges after `/lgtm`), qontract-reconcile deploys to staging automatically. Verify with:

```bash
oc get pods -n rhel-lightspeed-stage --sort-by=.metadata.creationTimestamp
```

### 7. Production

Production is only configured for `rlsapi` in the saas.yml (SHA-pinned, 10 replicas). `lightspeed-stack`, `redhat-okp`, and `okp-mcp` do **not** have production targets in app-interface — they are staging-only as of 2026-04-02.

## CI/CD Configuration

### GitHub Actions

Already configured to run on `release/**` branches (see `.github/workflows/build.yml`).

### Tekton / Pipelines-as-Code

The CEL expressions in `.tekton/push.yaml` and `.tekton/pull_request.yaml` already include `release/` branches. These were added to `main` in PR #136 and exist on all release branches created after that.

### Konflux Components

| Component | Watches | Quay Image |
|-----------|---------|------------|
| `okp-mcp` | `main` (but CEL matches `release/` too) | `quay.io/redhat-user-workloads/rhel-lightspeed-tenant/okp-mcp` |
| `okp-mcp-rel` | release branch | `quay.io/redhat-user-workloads/rhel-lightspeed-tenant/okp-mcp-rel` |

For deployment, use images from the `okp-mcp` quay repo (same as main). The `okp-mcp-rel` component is needed to ensure Konflux recognizes the release branch, but the deployable images come from the existing `okp-mcp` component's custom pipeline.

## Deployment Flow

```
release branch commit (GitHub)
  → GitLab mirror syncs (regex: main|release\/.*)
  → Konflux builds image (okp-mcp component, custom .tekton/push.yaml)
  → image tagged with commit SHA at quay.io/.../okp-mcp:<sha>
  → update app-interface saas.yml:
      - ref: <commit-sha> (for okp-mcp, lightspeed-stack, redhat-okp)
      - IMAGE_TAG removed for okp-mcp (auto-derived from ref)
  → /lgtm on the MR → App SRE bot auto-merges
  → qontract-reconcile deploys to staging
  → verify: oc get pods -n rhel-lightspeed-stage
```

## Gotchas

- **`ref` only accepts SHAs or `main`/`master`/`internal`/`stable`** — the saas-file schema rejects branch names like `release/2026-04-01`. Use commit SHAs to pin to a release branch
- **`IMAGE_TAG` must differ from `ref`** — when both are the same SHA, the saas-file-validator rejects it because IMAGE_TAG is auto-derived from ref. Remove IMAGE_TAG for okp-mcp when ref is a SHA
- **SHAs are static** — if you push a bugfix to the release branch, you must update the SHA in app-interface manually
- **Konflux components are immutable** — you can't change the branch, you have to delete and recreate
- **GitLab mirror must include the branch** — if the mirror regex doesn't match, Konflux never sees the push. Use regex `(main|release\/.*)`, not "Mirror only protected branches"
- **The mirror URL must end in `.git`** — GitLab treats URLs with and without `.git` as different identities
- **The component name can't be reused immediately after deletion** — use a variation if needed
- **Use the GitLab URL for Konflux components**, not GitHub — the existing setup uses GitLab and the Tekton annotations reference GitLab URLs
- **Images go to the same quay repo as main** — the `okp-mcp` component's custom pipeline builds release branches too, so no `IMAGE` change is needed in app-interface
- **Konflux auto-commits `.tekton/` files** for new components — review the auto-generated commit on the release branch and ensure it doesn't conflict with existing custom pipelines
- **The auto-generated Konflux PR build may fail** on `sast-coverity-check` (image pull flakiness) — this doesn't block the push build
- **Production is staging-only** — only `rlsapi` has a production target in app-interface. `lightspeed-stack`, `redhat-okp`, and `okp-mcp` are staging-only as of 2026-04-02
