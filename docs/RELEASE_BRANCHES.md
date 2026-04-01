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

The GitLab mirror at `gitlab.cee.redhat.com/rhel-lightspeed/enhanced-shell/okp-mcp` pulls from GitHub. By default it only mirrors specific branches.

To add release branches to the mirror:

1. Go to GitLab repo **Settings > Repository > Mirroring repositories**
2. Delete the existing mirror rule (it can't be edited in-place)
3. Create a new Pull mirror with:
   - **Git repository URL**: `https://github.com/rhel-lightspeed/okp-mcp.git`
   - **Mirror direction**: Pull
   - **Mirror branches**: Mirror specific branches
   - **Branch regex**: `(main|release\/.*)`
   - **Overwrite diverged branches**: checked
   - **Trigger pipelines for mirror updates**: checked
4. Click **Mirror repository**
5. Click the sync button (🔄) to trigger an immediate pull
6. Verify the release branch appears in the GitLab branch dropdown

### 4. Konflux component

Konflux watches one branch per Component. The `okp-mcp` component watches `main`. A separate component is needed for the release branch.

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

**For subsequent release branches:**

Konflux components are immutable — you cannot edit the branch. You must delete the old `okp-mcp-rel` component and create a new one pointing to the new release branch. The component name must be unique; if the old name hasn't been freed yet, use a variation (e.g., `okp-mcp-rel-2`).

**Important:** The component must point to the **GitLab** mirror URL, not GitHub. The original `okp-mcp` component uses GitLab, and the Tekton pipeline annotations reference GitLab URLs.

### 5. Verify the build

1. Push a commit (or empty commit) to the release branch on GitHub
2. Wait for GitLab mirror to sync (or manually trigger sync)
3. Check Konflux Activity tab — a build should appear for the release branch
4. Once the build succeeds, the image will be at: `quay.io/redhat-user-workloads/rhel-lightspeed-tenant/okp-mcp-rel:<commit-sha>`

### 6. Deploy via app-interface

Get the commit SHA from the release branch build:

```bash
skopeo inspect \
  --override-os linux \
  --override-arch amd64 \
  docker://quay.io/redhat-user-workloads/rhel-lightspeed-tenant/okp-mcp-rel:latest \
  | jq -r '.Labels["vcs-ref"]'
```

Update `data/services/insights/rhel-lightspeed/cicd/saas.yml` in app-interface with the new `IMAGE_TAG` and `IMAGE` (note: the release component pushes to a different quay repo than the main component).

## CI/CD Configuration

### GitHub Actions

Already configured to run on `release/**` branches (see `.github/workflows/build.yml`).

### Tekton / Pipelines-as-Code

The CEL expressions in `.tekton/push.yaml` and `.tekton/pull_request.yaml` already include `release/` branches. These were added to `main` in PR #136 and exist on all release branches created after that.

### Konflux Components

| Component | Branch | Quay Image |
|-----------|--------|------------|
| `okp-mcp` | `main` | `quay.io/redhat-user-workloads/rhel-lightspeed-tenant/okp-mcp` |
| `okp-mcp-rel` | release branch | `quay.io/redhat-user-workloads/rhel-lightspeed-tenant/okp-mcp-rel` |

## Deployment Flow

```
release branch commit (GitHub)
  → GitLab mirror syncs (regex: main|release\/.*)
  → Konflux (okp-mcp-rel component) builds image
  → image tagged with commit SHA in quay.io
  → update app-interface saas.yml IMAGE_TAG with that SHA
  → qontract-reconcile deploys to staging
```

## Gotchas

- **Konflux components are immutable** — you can't change the branch, you have to delete and recreate
- **GitLab mirror must include the branch** — if the mirror regex doesn't match, Konflux never sees the push
- **The component name can't be reused immediately after deletion** — use a variation if needed
- **Use the GitLab URL for Konflux components**, not GitHub — that's what the existing setup uses
- **The release component pushes to a different quay repo** (`okp-mcp-rel` vs `okp-mcp`) — update the `IMAGE` field in app-interface saas.yml accordingly
