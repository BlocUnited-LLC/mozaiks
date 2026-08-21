<!--
Use when: a bug report cannot be reproduced from what is written, or is missing the
information needed to try.
Action: label `question`, or close as *not planned* if there is no response after a
reasonable wait. Say the wait out loud rather than letting it go quiet.
-->

Thanks for the report @{{author}}. I tried to reproduce this and could not, so
I am probably missing a piece of your setup.

Could you add:

- the exact command you ran and the full output or traceback,
- what you expected instead,
- `python --version`, your OS, and whether you installed with
  `pip install -e ".[dev]"` from a checkout,
- the commit you are on (`git rev-parse --short HEAD`),
- for anything involving Studio or the frontend: your Node version, and whether
  MongoDB is local, Atlas, or Docker.

If you can reduce it to a minimal case that fails — a few lines, or a single
failing test — that usually turns a hard bug into an obvious one.

I will leave this open for now. If we do not hear back in a couple of weeks I
will close it to keep the queue honest, but a comment at any point reopens the
conversation and nothing is lost.
