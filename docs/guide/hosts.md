# Hosts: GitHub, GitLab, public and internal

A **host** is one GitHub or GitLab instance: github.com, gitlab.com, your self-managed GitLab, GitHub Enterprise Server. Toolbot can use any number of them at once, for example tools released on github.com and on an internal GitLab, maintained in repositories on another GitLab.

## Built-in hosts

| Name | URL | Token variable |
| --- | --- | --- |
| `github` | `https://github.com` | `TOOLBOT_GITHUB_TOKEN` |
| `gitlab` | `https://gitlab.com` | `TOOLBOT_GITLAB_SOURCE_TOKEN` |

When Toolbot runs in GitLab CI, the instance it runs on is added automatically as `ci-gitlab` with `TOOLBOT_GITLAB_TOKEN`. Likewise in GitHub Actions on GitHub Enterprise, as `ci-github` with `TOOLBOT_GITHUB_TOKEN`. So projects on the same instance work with no configuration.

## Declaring hosts

In `toolbot.yaml` at the repository root (or the [control repository](../deploy/control-repository.md)):

```yaml
hosts:
  internal-gitlab:
    type: gitlab
    url: https://gitlab.example.com          # path prefixes work: https://example.com/gitlab
    token-env: TOOLBOT_GITLAB_TOKEN
  vendor-gitlab:
    type: gitlab
    url: https://git.vendor.example
    token-env: VENDOR_GITLAB_TOKEN
  ghe:
    type: github
    url: https://github.example.com          # API at /api/v3 is derived
    token-env: TOOLBOT_GHE_TOKEN
```

`token-env` is the **name** of an environment variable (a masked CI/CD variable or secret). Tokens never appear in configuration files.

## Using a host in a tool

```yaml
source:
  host: internal-gitlab          # provider (gitlab) comes from the host
  repository: platform/deploy-cli
```

`host` can also be a plain URL (`host: https://gitlab.example.com`); it then uses whatever token is registered for that URL.

## Tokens only go to their own host

Every request picks its token by URL: the host whose URL owns the request URL (same scheme, host, port and path prefix). That means:

- a GitLab release link pointing at another server, or a `url:` asset on a download site, is fetched **without** credentials;
- redirects to another origin (e.g. GitHub's signed CDN URLs) drop credentials;
- a token for `gitlab.example.com` is never sent to `gitlab.example.com.evil.test` or to `https://gitlab.example.com:8443`.

Tokens are never logged; error messages name the variable to set, not its value.
