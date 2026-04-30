"""Mozaiks host composition layer.

The four layered FastAPI hosts:

- ``runtime``   — execution substrate (sessions, transport, persistence, workflow execution)
- ``platform``  — headless app host (modules, pages, shell config, admin, actions, routing)
- ``studio``    — Studio management host (local and hosted management/create control plane)
- ``mozaiks``   — hosted Mozaiks product host (extends Studio with hosted-only capabilities)

Each host is a superset of the layer below it. Import the host you need:

    from mozaiksai.hosts.studio import app

Or start via the CLI:

    mozaiks serve ./my-app
    mozaiks serve ./my-app --host studio
"""
