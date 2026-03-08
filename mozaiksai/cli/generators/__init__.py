"""mozaiksai.cli.generators — Declarative artifact generators.

Each generator reads a declarative config file (app.json, brand.json) and
produces derived infrastructure artifacts (realm-export.json, Keycloak theme).

Generators are pure functions: config in → files out.
No runtime state, no async, no database.
"""
