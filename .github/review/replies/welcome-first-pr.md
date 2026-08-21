<!--
Use when: a first-time contributor opens their first PR.
Action: none — informational. Combine with other replies if something is also blocking.
Note: post this promptly. It is also the moment to click "Approve and run workflows",
since a first-time contributor's CI will not start without it.
-->

Welcome @{{author}}, and thanks for your first contribution to Mozaiks! 🎉

What happens next:

- **CI needs a maintainer to start it.** GitHub holds workflow runs from
  first-time contributors until someone approves them, so if the checks look
  like they are not running, that is on our side and not on yours. We have
  approved them now.
- A maintainer will review the diff and either merge it or leave specific
  comments. If we ask for changes, push to the same branch — the PR updates
  automatically, no need to open a new one.
- If CI fails, the log usually says why. `python -m pytest <your test file> -q --no-cov`
  reproduces most failures locally; the `--no-cov` flag avoids tripping the
  repository-wide coverage gate on a narrow test run.

Useful links: [Contributing guide](https://github.com/BlocUnited-LLC/mozaiks/blob/main/CONTRIBUTING.md) ·
[AI policy](https://github.com/BlocUnited-LLC/mozaiks/blob/main/.github/AI_POLICY.md) ·
[Docs](https://docs.mozaiks.ai) ·
[Discord](https://discord.gg/Qnsywad9kp)

If anything about the process is unclear or slower than you expected, say so
here or in Discord. That feedback is useful to us.
