# Release Branch Workflow

## Overview

During code freezes, we use release branches to stabilize staging while development continues on `main`. Bugfixes go to the release branch and are cherry-picked to `main`. New features go to `main` only.

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

The repo-level ruleset "Minimum required Branch Protection" only covers `main`. For release branches, add a classic branch protection rule:

1. Go to GitHub repo **Settings > Branches > Add classic branch protection rule**
2. Branch name pattern: the specific branch name (e.g., `release/2026-04-01`)
   - Wildcard patterns like `release/**` may trigger a "merge queue" warning that blocks creation; use the specific branch name instead
3. Enable: Require pull request before merging
4. Click **Create**

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

### 6. Deploy via app-interface

Get the commit SHA from the release branch build:

```bash
skopeo inspect \
  --override-os linux \
  --override-arch amd64 \
  docker://quay.io/redhat-user-workloads/rhel-lightspeed-tenant/okp-mcp:<commit-sha> \
  | jq -r '.Labels["vcs-ref"]'
```

Update `data/services/insights/rhel-lightspeed/cicd/saas.yml` in app-interface:

1. Change `ref` from `main` to `release/YYYY-MM-DD` for all three staging targets:
   - `lightspeed-stack-stage` (lscore-deploy templates)
   - `redhat-okp-stage` (lscore-deploy templates)
   - `okp-mcp-stage`
2. Update `IMAGE_TAG` to the release branch commit SHA
3. `IMAGE` stays the same (`quay.io/redhat-user-workloads/rhel-lightspeed-tenant/okp-mcp`)

**lscore-deploy** also needs a `release/YYYY-MM-DD` branch (it's just config, no CI changes needed):

```bash
cd ~/Documents/Development/lscore-deploy
git checkout main && git pull origin main
git checkout -b release/YYYY-MM-DD
git push origin release/YYYY-MM-DD
```

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
      - ref: release/YYYY-MM-DD (for okp-mcp, lightspeed-stack, redhat-okp)
      - IMAGE_TAG: <sha>
  → qontract-reconcile deploys to staging
```

## Gotchas

- **Konflux components are immutable** — you can't change the branch, you have to delete and recreate
- **GitLab mirror must include the branch** — if the mirror regex doesn't match, Konflux never sees the push. Use regex `(main|release\/.*)`, not "Mirror only protected branches"
- **The mirror URL must end in `.git`** — GitLab treats URLs with and without `.git` as different identities
- **The component name can't be reused immediately after deletion** — use a variation if needed
- **Use the GitLab URL for Konflux components**, not GitHub — the existing setup uses GitLab and the Tekton annotations reference GitLab URLs
- **Images go to the same quay repo as main** — the `okp-mcp` component's custom pipeline builds release branches too, so no `IMAGE` change is needed in app-interface
- **Konflux auto-commits `.tekton/` files** for new components — review the auto-generated commit on the release branch and ensure it doesn't conflict with existing custom pipelines
- **The auto-generated Konflux PR build may fail** on `sast-coverity-check` (image pull flakiness) — this doesn't block the push build
- **app-interface needs three `ref` changes** for a code freeze — okp-mcp, lightspeed-stack, and redhat-okp all need to point to the release branch
