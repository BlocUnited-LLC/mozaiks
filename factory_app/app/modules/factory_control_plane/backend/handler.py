class FactoryControlPlaneModule:
    """Zero-action identity module for Studio module discovery.

    The real control-plane harness does not live under
    `app/modules/factory_control_plane/backend/`.

    Canonical runtime paths:
    - `mozaiksai/control_plane/*`
    - `factory_app/control_plane/config/*`
    - `factory_app/control_plane/prompts/*`
    - `factory_app/control_plane/tools/*`
    """
