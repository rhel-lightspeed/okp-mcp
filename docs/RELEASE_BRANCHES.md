# Release Branch Workflow

## Overview

During code freezes, we use release branches to stabilize staging while development continues on `main`. Bugfixes go to the release branch and are cherry-picked to `main`. New features go to `main` only.

## Branch Naming

```
release/YYYY-MM-DD
```

Example: `release/2026-04-01`

## Creating a Release Branch

```bash
git checkout main
git pull origin main
git checkout -b release/YYYY-MM-DD
git push origin release/YYYY-MM-DD
```

## CI/CD Configuration

### GitHub Actions

Already configured to run on `release/**` branches (see `.github/workflows/build.yml`).

### Konflux (RHTAP)

Konflux requires a **separate Component** per branch. The CEL expressions in `.tekton/push.yaml` and `.tekton/pull_request.yaml` already include `release/` branches, but Konflux only watches the branch specified in the Component config.

**Two components exist in the `rhel-lightspeed-tenant` namespace:**

| Component | Branch | Purpose |
|-----------|--------|---------|
| `okp-mcp` | `main` | Development builds |
| `okp-mcp-release` | release branch | Staging/frozen builds |

**When cutting a new release branch**, update the `okp-mcp-release` component in the Konflux UI to point to the new branch:

1. Go to https://konflux-ui.apps.stone-prod-p02.hjvn.p1.openshiftapps.com/
2. Namespace: `rhel-lightspeed-tenant` > Applications > `okp-mcp` > Components
3. Edit the `okp-mcp-release` component's git reference to the new release branch

### app-interface

Update `data/services/insights/rhel-lightspeed/cicd/saas.yml` in app-interface to point `IMAGE_TAG` to a commit SHA from the release branch (built by the `okp-mcp-release` Konflux component).

## Branch Protection

Release branches use the same protection rules as `main` (require PR, restrict deletions, block force pushes). Configure via GitHub repo Settings > Rules > Rulesets.

## Deployment Flow

```
release branch commit
  → Konflux (okp-mcp-release component) builds image
  → image tagged with commit SHA in quay.io
  → update app-interface saas.yml IMAGE_TAG with that SHA
  → qontract-reconcile deploys to staging
```
