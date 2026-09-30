# Proxies & internal networks

## Outbound hosts

Allow HTTPS (443) to:

| Host | Why |
| --- | --- |
| your GitLab (`gitlab.example.com`) | Target: API and git push; GitLab-hosted sources |
| `api.github.com` | GitHub release discovery |
| `release-assets.githubusercontent.com`, `objects.githubusercontent.com` | GitHub asset downloads (redirect targets) |
| `gitlab.com` | Only if tools are hosted there |
| hosts that GitLab release links point to | Only for those tools |
| your registry | Pulling the image |

## HTTP proxy

Set the standard variables on the job, schedule or pod:

```yaml
variables:
  HTTPS_PROXY: http://proxy.example.com:3128
  HTTP_PROXY: http://proxy.example.com:3128
  NO_PROXY: gitlab.example.com,.example.com,localhost
```

Both Toolbot's HTTP client (httpx) and `git` honour them.

## TLS inspection / internal CAs

Point both Python and git at your CA bundle:

```yaml
variables:
  SSL_CERT_FILE: $CORP_CA_BUNDLE     # a GitLab "File" variable: its value is a path
  GIT_SSL_CAINFO: $CORP_CA_BUNDLE
```

## Air-gapped environments

Toolbot tracks upstream releases, so it must reach the upstream hosts, or an internal mirror of the upstream projects (GitHub Enterprise or self-managed GitLab). Point tool definitions at the mirror with `source.host`.
