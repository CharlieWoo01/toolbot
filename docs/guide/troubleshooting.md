# Troubleshooting

## First steps

1. Run `toolbot check --dry-run` (locally or in CI with `TOOLBOT_CHECK_ARGS=--dry-run`). It shows the decision for each tool without changing anything.
2. Read the job log: progress lines start with `toolbot:`, failures with `error [CODE]:`.
3. `toolbot outdated` shows what upstream offers, including ignored versions.

## Tool statuses

| Status | Meaning | What to do |
| --- | --- | --- |
| `up-to-date` | Nothing newer is acceptable | Nothing. The note shows ignored newer versions. |
| `ahead` | Current version is newer than any acceptable release | Check `tag`, `prereleases` and `ignore`. |
| `no-releases` | No release matched the rules | The tag template is probably wrong, or everything is a prerelease. |
| `created` / `superseded` | An MR was opened (and older ones closed) | Review it. |
| `exists` | The MR for this version is already open | Nothing. |
| `rejected` | The MR for this version was closed without merging | Reopen it, or add the version to `ignore`. |
| `reverted` | The MR was merged but the base branch doesn't have the version | Intentional revert? Add it to `ignore` to silence this. |
| `blocked` | A Toolbot branch has someone else's commits | Merge or close that MR, or delete the branch. |
| `failed` | Error for this tool (others continued) | See the message and code below. |

## Error codes

| Code | Typical cause / fix |
| --- | --- |
| `CONFIG_INVALID` | Fix the listed problems in `tool.yaml`. |
| `RELEASE_NOT_FOUND` | Wrong `tag` template, a tag without a *release*, or a private repo and missing token. |
| `ASSET_NOT_FOUND` | Upstream renamed or dropped an asset. The error lists the available names. |
| `ASSET_AMBIGUOUS` | A `pattern` matches several assets. Make it more specific or use `name`. |
| `CHECKSUM_UNAVAILABLE` | Checksum file missing from the release, or it doesn't list the asset. |
| `CHECKSUM_MISMATCH` | **Stop.** Investigate upstream before doing anything else. |
| `AUTH_FAILED` | Token missing, expired or without access (HTTP 401/403). See [Tokens](../deploy/tokens.md). |
| `RATE_LIMITED` | Set `TOOLBOT_GITHUB_TOKEN`: anonymous GitHub API access is 60 requests/hour. |
| `HTTP_ERROR` | Network or proxy problems ([Networking](../deploy/networking.md)), 5xx after retries, size limit. |
| `GIT_ERROR` | Push rejected: token role too low, push rules (author email!), protected branch patterns. |
| `TARGET_ERROR` | GitLab API refused the MR. The branch may already be pushed; the next run retries. |
| `USAGE` | Missing configuration, e.g. `TOOLBOT_GITLAB_TOKEN`. |

## Common problems

| Symptom | Fix |
| --- | --- |
| `GitLab target not configured: set TOOLBOT_GITLAB_TOKEN` | Add the CI/CD variable (masked, protected), and run on a protected branch so protected variables are exposed. |
| `git push failed: … pre-receive hook declined` | Project push rules, e.g. *commit author email must be verified*. Set `TOOLBOT_GIT_AUTHOR_EMAIL` to the bot user's email. |
| Labels missing on MRs, with a warning in the log | The token can't create labels. Create `toolbot` and `tool-update` once by hand, or set `TOOLBOT_MR_LABELS`. |
| `version: must be a quoted string` | Write `version: "1.10"`. YAML would read `1.10` as `1.1`. |
| `values starting with "{" must be quoted` | Write `tag: "{version}"`. |
| Job never runs on the schedule | The schedule needs the variable `TOOLBOT_RUN=true`. |
| `unable to get local issuer certificate` | TLS inspection: see [Networking](../deploy/networking.md). |
