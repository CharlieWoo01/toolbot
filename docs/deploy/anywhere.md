# Docker, Kubernetes & other CI

Anything that can run a container on a schedule and pass environment variables can run Toolbot. Outside GitLab CI, set the target explicitly.

## Plain Docker (cron, a VM)

```sh
docker run --rm \
  -e TOOLBOT_GITLAB_URL=https://gitlab.example.com \
  -e TOOLBOT_GITLAB_PROJECT=team/ci-tools \
  -e TOOLBOT_GITLAB_TOKEN \
  -e TOOLBOT_GITHUB_TOKEN \
  gitlab.example.com:5050/platform/toolbot:0.1.0 toolbot check
```

No checkout or volume is needed: Toolbot fetches the target branch itself.

## Jenkins

```groovy
pipeline {
  agent any
  triggers { cron('H 6 * * 1-5') }
  options { disableConcurrentBuilds() }
  environment {
    TOOLBOT_GITLAB_TOKEN = credentials('toolbot-gitlab-token')
    TOOLBOT_GITHUB_TOKEN = credentials('toolbot-github-readonly')
  }
  stages {
    stage('toolbot') {
      steps {
        sh '''
          docker run --rm \
            -e TOOLBOT_GITLAB_URL=https://gitlab.example.com -e TOOLBOT_GITLAB_PROJECT=team/ci-tools \
            -e TOOLBOT_GITLAB_TOKEN -e TOOLBOT_GITHUB_TOKEN \
            gitlab.example.com:5050/platform/toolbot:0.1.0 toolbot check
        '''
      }
    }
  }
}
```

## Kubernetes CronJob

```yaml
apiVersion: v1
kind: Secret
metadata: { name: toolbot }
stringData:
  TOOLBOT_GITLAB_TOKEN: "<project access token, api scope>"
  TOOLBOT_GITHUB_TOKEN: "<read-only github token>"
---
apiVersion: batch/v1
kind: CronJob
metadata: { name: toolbot }
spec:
  schedule: "0 6 * * 1-5"
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      backoffLimit: 0
      activeDeadlineSeconds: 3600
      template:
        spec:
          restartPolicy: Never
          securityContext: { runAsNonRoot: true, runAsUser: 10001 }
          containers:
            - name: toolbot
              image: gitlab.example.com:5050/platform/toolbot:0.1.0
              args: ["toolbot", "check"]
              envFrom: [{ secretRef: { name: toolbot } }]
              env:
                - { name: TOOLBOT_GITLAB_URL, value: https://gitlab.example.com }
                - { name: TOOLBOT_GITLAB_PROJECT, value: team/ci-tools }
              securityContext:
                allowPrivilegeEscalation: false
                readOnlyRootFilesystem: true
                capabilities: { drop: [ALL] }
              volumeMounts: [{ name: tmp, mountPath: /tmp }]
          volumes: [{ name: tmp, emptyDir: {} }]
```

Toolbot writes only to the temp directory (its private repository and downloads), so a read-only root filesystem with an `emptyDir` at `/tmp` works. Size it for the target branch plus the largest tool.

## Several projects

Each `toolbot check` run targets one project. For several projects, run one job per project (different `TOOLBOT_GITLAB_PROJECT`), each serialised on its own.
