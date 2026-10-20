# Security

This is an experimental source release, not a production security certification.
Use local synthetic data while evaluating it. A passing test does not establish
that every dependency, integration or deployment is secure.

## Reporting a vulnerability

Use the repository's **Security → Report a vulnerability** option if available.
If private reporting is not enabled, use GitHub's private reporting/support
channel; do not place secrets, exploit details or personal data in public issues.
Routine dependency-update proposals can be public when they only reference
already published advisories. No response or fix-time commitment is offered.

## Deployment responsibilities

- Set unique secrets outside Git and rotate any previously exposed credentials.
- Configure authentication, TLS, explicit CORS origins and request limits.
- Review the roadmap and dependency advisories before hosting publicly.
- Do not bundle original service accounts, production data or deployment access.
- Review the repository's complete history before changing its visibility. The
  proposed community release uses a fresh source snapshot, not private history.
