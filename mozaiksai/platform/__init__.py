"""mozaiksai.platform
===================
Mozaiks-platform extension bundle for the AG2 runtime.

This package registers Mozaiks-proprietary features (themes, pack/journey
gating, OAuth callbacks, build exports, general chats, workflow ordering) into
the core runtime via the RUNTIME_PLATFORM_EXTENSIONS hook system.

To activate, set:

    RUNTIME_PLATFORM_EXTENSIONS=mozaiksai.platform.extensions:get_bundle

Open-source deployments omit this env var entirely; the core runtime runs
without this package loaded.
"""
