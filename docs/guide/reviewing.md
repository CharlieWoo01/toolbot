# Reviewing update MRs

A Toolbot merge request is titled like `chore(tools): update pact-broker-cli to 0.8.8`, carries the configured labels (`toolbot`, `tool-update` by default), and contains exactly:

```text
tools/pact-broker-cli/tool.yaml                                   version: "0.8.6" → "0.8.8"
tools/pact-broker-cli/tool.lock.json                              tag, upstream name, size, sha256
tools/pact-broker-cli/pact-broker-cli-x86_64-pc-windows-msvc.zip  binary change
```

The description shows the tool, previous and new version, a link to the upstream project and release, every asset with its verification, and a short excerpt of the upstream release notes. The excerpt is fenced as plain text, so upstream `@mentions` or `#123` references can't notify people or link issues in your GitLab.

## What to check

1. **Pipeline is green.** `toolbot-verify` proves the committed binaries match the lock file for the new version.
2. **Verification** column: ✅ for every asset, unless the tool is deliberately configured with `checksum.type: none` (⚠️).
3. **Release notes:** breaking changes, removed flags, new runtime requirements, security notices.
4. **Major versions:** test in a consuming pipeline before merging. To stay on a major line, add `ignore: [">=N.0.0"]`.

## Your options

| You want to… | Do this |
| --- | --- |
| Accept | Merge. The branch is deleted automatically. |
| Reject this version | Close without merging. Toolbot won't propose it again, but will propose newer versions. |
| Reject permanently / a range | Add it to `ignore` in `tool.yaml` (and close the MR). |
| Adjust something for the new version | Push a commit to the branch. Toolbot then leaves this MR alone (reported as *blocked* if a newer version appears) until you merge or close it. |
| Get a fresh branch | Close the MR and delete its branch, then run the schedule. Or reopen it if it was rejected. |

## Automerge

Toolbot doesn't automerge. If you want it for specific low-risk tools, use GitLab's *auto-merge when pipeline succeeds* on the MR, and keep *Pipelines must succeed* enabled.
