"""mozaiksai.cli — Platform CLI tools.

Declarative generators and dev utilities exposed as a proper CLI:

    python -m mozaiksai.cli init              # first-run bootstrap ritual
    python -m mozaiksai.cli up                # one-command local startup
    python -m mozaiksai.cli doctor            # setup diagnostics
    python -m mozaiksai.cli generate          # regenerate all artifacts
    python -m mozaiksai.cli generate --realm  # only Keycloak realm
    python -m mozaiksai.cli generate --theme  # only Keycloak theme
    python -m mozaiksai.cli generate --check  # verify artifacts are up-to-date
    python -m mozaiksai.cli generate --dry    # preview without writing
"""
