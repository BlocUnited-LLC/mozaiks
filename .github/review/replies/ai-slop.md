<!--
Use when: a PR or issue shows signs of unverified AI-generated content — the description
does not match the diff, invented APIs or file paths, claims of testing with no evidence,
boilerplate reasoning that could apply to any change.

Do NOT use when: the work is simply wrong, or the author is inexperienced. Those get a
normal review comment. This reply is specifically about content that was not verified
before being published.

Fill in {{observations}} with the actual specifics. A generic version of this message is
itself the thing it is complaining about.
-->

Hi @{{author}}, thanks for the submission.

AI-assisted contributions are welcome here — our
[AI policy](https://github.com/BlocUnited-LLC/mozaiks/blob/main/.github/AI_POLICY.md)
says so explicitly, and a good share of this repository is agent-authored. The
standard it sets is that a person verified the result before publishing it, and
this submission has some signs that did not happen:

{{observations}}

To move this forward:

1. Explain in your own words what problem this solves and how the change works.
2. Make the description match the actual diff — remove anything it claims that
   the code does not do.
3. Say how you validated it: the commands you ran and what you saw.

We ask this of every submission flagged this way, including our own. An
unverified description costs a reviewer more time than it saved the author,
because the reviewer has to discover the mismatch before they can start
reviewing.

Happy to help if you are stuck on any part of it — reply here or ask in
[Discord](https://discord.gg/Qnsywad9kp).
