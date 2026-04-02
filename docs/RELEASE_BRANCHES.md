# Release and Deployment Workflow

## Overview

Development happens on `main`. When ready to deploy, tag the commit with a semver version (e.g., `v3.0.1`), then pin that commit SHA in app-interface. There are no long-lived release branches.

Release branches are only created when you need to hotfix a deployed version while `main` has already moved on with incompatible changes.

## Versioning

We use semantic versioning: `MAJOR.MINOR.PATCH`.

- **MAJOR**: Incompatible changes (new LLM behavior, breaking tool API changes)
- **MINOR**: New features, intent rules, tool enhancements (backwards-compatible)
- **PATCH**: Bug fixes, config changes, test additions

Git tags use a `v` prefix: `v3.0.1`, `v3.1.0`, etc.

## Normal Deployment (no release branch)

### 1. Tag the release

```bash
git checkout main
git pull origin main
git tag -a v3.0.1 -m "v3.0.1: GFS2 intent rule, RAG-Fusion multi-query"
git push origin v3.0.1
```

### 2. Verify the image exists

Konflux builds an image for every push to `main`, tagged with the commit SHA.

```bash
# Get the SHA for the tag
git rev-parse v3.0.1

# Check if Konflux has built it
skopeo list-tags docker://quay.io/redhat-user-workloads/rhel-lightspeed-tenant/okp-mcp \
  | jq -r '.Tags[]' | grep <sha>
```

### 3. Deploy to staging via app-interface

Update `data/services/insights/rhel-lightspeed/cicd/saas.yml` in app-interface:

1. Set the `okp-mcp-stage` `ref` to the commit SHA
2. **Do not set `IMAGE_TAG`** — it is auto-derived from `ref` when `ref` is a SHA. Setting both causes a validation error.
3. Update the comment with the version and date

```yaml
- name: okp-mcp-stage
  ref: <commit-sha>
  parameters:
    IMAGE: quay.io/redhat-user-workloads/rhel-lightspeed-tenant/okp-mcp
    # v3.0.1 (2026-04-07)
    SOLR_BASE_URL: http://redhat-okp:8983
```

After merging the app-interface MR (auto-merges after `/lgtm`), qontract-reconcile deploys to staging automatically.

### 4. Verify

```bash
oc get pods -n rhel-lightspeed-stage --sort-by=.metadata.creationTimestamp
```

## When to Create a Release Branch

Create a release branch only when **all three** conditions are true:

1. A deployed version needs a hotfix (bug or security)
2. `main` has moved on with changes you do **not** want to deploy yet
3. Cherry-picking the fix onto main's HEAD is not sufficient (because you'd also deploy the unwanted changes)

If `main` is in a deployable state, just deploy from `main` — no branch needed.

## Creating a Release Branch (when needed)

### 1. Branch from the deployed SHA

```bash
# Find the SHA currently deployed in staging
grep -A5 'okp-mcp-stage' path/to/saas.yml | grep ref

# Create the branch from that SHA (use the version being hotfixed)
git checkout -b release/3.0.x <deployed-sha>
git push origin release/3.0.x
```

### 2. Apply the fix

Cherry-pick or commit the fix to the release branch. Push it. Konflux will build an image — the `.tekton/push.yaml` CEL expression matches `release/` branches.

### 3. Deploy

Update app-interface `ref` to the new release branch SHA. Same process as a normal deployment.

### 4. Forward-port the fix

Cherry-pick the fix to `main` so it isn't lost:

```bash
git checkout main
git cherry-pick <fix-sha>
```

### 5. Clean up

Once the next deployment goes out from `main` (which includes the fix), delete the release branch:

```bash
git push origin --delete release/3.0.x
git branch -d release/3.0.x
```

## Branch Protection

A GitHub ruleset protects all branches matching `release/**` with the same rules as `main` (require PR approval, restrict deletions, block force pushes).

## Infrastructure

### GitLab mirror

The GitLab mirror at `gitlab.cee.redhat.com/rhel-lightspeed/enhanced-shell/okp-mcp` pulls `main` and any branch matching `release/*` from GitHub. New release branches appear on GitLab after the next mirror sync (typically within minutes).

### Konflux

The `okp-mcp` Konflux component watches `main`, but its pipeline (`.tekton/push.yaml`) has a CEL expression that also matches `release/` branches. Every push triggers a build, pushing images to `quay.io/redhat-user-workloads/rhel-lightspeed-tenant/okp-mcp:<sha>`.

Konflux UI: https://konflux-ui.apps.stone-prod-p02.hjvn.p1.openshiftapps.com/ (namespace: `rhel-lightspeed-tenant`, application: `okp-mcp`)

## Gotchas

- **`ref` only accepts SHAs or `main`/`master`/`internal`/`stable`** — the saas-file schema rejects branch names. Always use commit SHAs.
- **`IMAGE_TAG` must not be set for okp-mcp** — when `ref` is a SHA, `IMAGE_TAG` is auto-derived. Setting both causes a validation error.
- **SHAs are static** — if you push a fix, you must update the SHA in app-interface manually.
- **GitLab mirror must include the branch** — the mirror regex is `(main|release\/.*)`. If it doesn't match, Konflux never sees the push.
