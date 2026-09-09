# CAD to SimReady Preflight

- Status: `blocked`
- Platform: `linux`
- Manifest: `/home/descfly/humanoid-computer-agent/reports/simready/p0_p1_workstation/preflight.json`

## Runtimes

- `asset_validator`: `blocked` - omni_asset_validate CLI and omni.asset_validator module are unavailable
- `content_agents`: `skipped` - Content Agents preflight was explicitly skipped
- `git_lfs`: `ready` - Git LFS is available
- `openusd_python`: `ready` - OpenUSD Python APIs are importable
- `repo_python`: `ready` - repo Python environment sync was not run
- `request`: `ready` - request inputs are ready
- `simready_validate`: `blocked` - SimReady Foundation checkout is missing

## Services


## Blockers

- asset_validator: omni_asset_validate CLI and omni.asset_validator module are unavailable
- simready_validate: SimReady Foundation checkout is missing
