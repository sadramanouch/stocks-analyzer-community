# Community charter

Anyone can use, fork and contribute to this project. The goal is useful software
that people can inspect, improve and maintain together. These are contribution
rules, not extra restrictions on the MIT license.

## Eight working rules

1. **Open door, reviewed main.** Anyone may propose an issue or a fork-based PR.
   Upstream write access is not public. Changes to main require a PR, passing
   required checks and resolved review conversations. A maintainer decides whether
   to merge; stars, votes and elapsed time never grant merge permission.
2. **Small proof beats a large promise.** Each PR states the problem, one clear
   outcome, how it was verified and its limits. Use synthetic fixtures. Separate
   unrelated changes so someone else can review and continue the work.
3. **Credit travels with the code.** Retain the MIT copyright and license notice
   in copies or substantial portions. Credit contributors in commits and release
   notes. Keep third-party notices. Do not imply another person's endorsement.
4. **Reuse is welcome.** MIT permits modification, redistribution and commercial
   use, including closed-source products. Forks are permitted; this charter does
   not promise exclusivity or prevent copying. Data, model and service rights
   must be checked separately. Do not upload work you lack permission to share.
5. **Access is earned and explicit.** Start with reviewed contributions. Prospective
   co-maintainers propose a component and a small milestone in a public issue.
   The owner approves the narrowest access needed after reviewing the record.
   Contribution counts alone do not award access; production access is separate.
6. **Important decisions leave a trail.** Discuss changes to architecture, licensing,
   billing, data handling or governance in a public proposal before implementation.
   Record the decision and trade-offs. Report security details privately; publish
   an appropriate summary after a fix. Previously granted license rights remain.
7. **No personal data or hidden bills.** Never commit secrets, private databases,
   journal text or payment records. Tests must not call paid AI/payment services
   or target the founder's infrastructure. Deployments require an operator's own
   configuration and a separate decision.
8. **Make the next contribution easier.** A changed workflow needs usable docs.
   Welcome questions and small fixes. Label reproducible beginner tasks, explain
   review decisions and pass context to the next contributor. No response-time
   or automatic-maintenance promise is implied.

## Current stewardship and enforcement

The initial steward is [Sadra Khorvash](https://github.com/sadramanouch).
Only the owner currently has upstream write access. Main has active PR, CI,
resolved-conversation, force-push and deletion safeguards with no bypass actors.
GitHub administrators can change those settings; they are not immutable laws.

With one maintainer, the required approving-review count is zero: the owner's
merge decision is the review gate, alongside required CI. CODEOWNERS requests
review but does not independently block merging. Add a second trusted maintainer
and require an independent approval before describing that as enforced policy.

Anyone may suggest changes to this charter in a PR. Concerns about conduct follow
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md); vulnerabilities follow [SECURITY.md](SECURITY.md).
