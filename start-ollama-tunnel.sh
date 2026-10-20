#!/usr/bin/env bash
# Retained as a safe failure for old instructions; no processes or services change.
cat >&2 <<'NOTICE'
The legacy public Ollama tunnel helper is disabled in the community release.
It exposed a local model and changed the founder's hosted deployment.
Use the local synthetic demo in README.md. Configure only infrastructure you
own; public model access needs authentication and a separate security review.
NOTICE
exit 1
