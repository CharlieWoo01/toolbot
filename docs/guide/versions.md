# Versions, prereleases & ignoring

## How the latest version is chosen

Toolbot never trusts a platform's "latest release" flag. It lists the project's published releases and picks the highest version among those that pass every rule:

1. not a draft (GitHub) or upcoming release (GitLab);
2. the tag matches the tool's `tag` template, e.g. `v{version}`;
3. the version parses under the tool's `versioning` scheme;
4. it's stable, unless `prereleases: true`;
5. it isn't excluded by an `ignore` rule.

If that version is higher than `version` in `tool.yaml`, an update is available.

## Version schemes

| `versioning` | Library | Use for |
| --- | --- | --- |
| `semver` *(default)* | [python-semver](https://python-semver.readthedocs.io) | Almost everything: `1.2.3`, `1.2.3-rc.1`, `1.2` |
| `pep440` | [packaging](https://packaging.pypa.io) | Four-part or date-based versions: `2024.1.3.1` |

Ordering is numeric and follows each scheme's rules, never string comparison:

```text
1.9.0 < 1.10.0 < 2.0.0
1.0.0-alpha < 1.0.0-alpha.1 < 1.0.0-beta.2 < 1.0.0-beta.11 < 1.0.0-rc.1 < 1.0.0
```

A leading `v` is tolerated in `version`, but the tag prefix belongs in `tag`. Build metadata (`1.2.3+build.5`) doesn't affect ordering.

## Prereleases

Ignored by default. A release counts as a prerelease if upstream **marks** it as one (GitHub's prerelease flag) **or** its **version** has a prerelease part (`2.0.0-rc.1`, `2.0.0b1`).

```yaml
prereleases: true   # propose release candidates too
```

## Ignoring versions

```yaml
ignore:
  - "0.9.0"            # this exact version is broken
  - ">=2.0.0"          # stay on 1.x until we migrate
  - ">=1.4.0, <1.5.0"  # skip the whole 1.4 line
```

Each entry is an exact version or comma-separated conditions using `==`, `!=`, `>=`, `<=`, `>` and `<`; all conditions must hold. Ignored newer versions are reported by `toolbot outdated` and `toolbot check` (e.g. `ignored newer: 2.1.0`), so they stay visible.

## Rejected versions: closing a merge request

Closing a Toolbot merge request **without merging** tells Toolbot not to propose that version again. It's reported as *rejected* on later runs. Newer versions are still proposed. See [Update lifecycle](lifecycle.md#rejected-updates).

Use `ignore` when the decision should be explicit and reviewable in the repository; closing an MR is the quick option.
