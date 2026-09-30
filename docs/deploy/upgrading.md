# Upgrading

## Upgrading consumers to a new Toolbot release

Bump both pins together:

```yaml
include:
  - project: platform/toolbot
    ref: v0.2.0                                                     # template
    file: /templates/toolbot.gitlab-ci.yml
variables:
  TOOLBOT_IMAGE: gitlab.example.com:5050/platform/toolbot:0.2.0     # image
```

Then run the schedule once with `TOOLBOT_CHECK_ARGS=--dry-run`.

## How the Toolbot project stays current

Toolbot manages standalone tools, not its own Python dependencies or images. Keep those current with your normal dependency tooling, and update `requirements.lock` (see its header), the pinned image digests and action SHAs deliberately.
