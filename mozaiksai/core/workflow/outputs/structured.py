# ==============================================================================
# FILE: mozaiksai/core/workflow/outputs/structured.py
# DESCRIPTION: Clean, simplified structured output models for AG2 workflows
# ==============================================================================

import logging
from pydantic import BaseModel, Field, create_model
from typing import List, Dict, Any, Optional, Union, Tuple, Set, Literal, get_args, get_origin
from enum import Enum
from ..llm_config import get_llm_config
from ..workflow_manager import workflow_manager

# Workflow-specific model cache
_workflow_models: Dict[str, Dict[str, type]] = {}
_workflow_registries: Dict[str, Dict[str, type]] = {}
# Cache of workflow -> set(agent_names) that have structured output models
_workflow_structured_agents: Dict[str, Set[str]] = {}
_provider_response_model_cache: Dict[type[BaseModel], type[BaseModel]] = {}
logger = logging.getLogger(__name__)

# Type mapping for consistent field resolution
TYPE_MAP = {
    'str': str,
    'string': str,
    'int': int,
    'bool': bool,
    'optional_str': Optional[str],
    'Optional[str]': Optional[str],
    'list': list,
    'List': list,
    'dict': Dict[str, Any],
    'Dict': Dict[str, Any],
    'float': float,
}


def _build_literal_enum(name: str, values: List[Any]) -> type[Enum]:
    enum_name = f"{name}Enum_{abs(hash(tuple(values))) % 10000}"
    enum_members = {f"VALUE_{i}": value for i, value in enumerate(values)}
    return Enum(enum_name, enum_members)  # type: ignore[return-value]


def _resolve_named_type(
    type_name: str,
    available_models: Dict[str, type],
    alias_defs: Dict[str, Dict[str, Any]],
    alias_cache: Dict[str, Any],
    stack: Optional[Set[str]] = None,
) -> Any:
    normalized = str(type_name or "").strip()
    if not normalized:
        raise ValueError("Empty type reference")

    if normalized in TYPE_MAP:
        return TYPE_MAP[normalized]
    if normalized in available_models:
        return available_models[normalized]
    if normalized not in alias_defs:
        raise ValueError(f"Unknown type reference: {normalized}")

    if normalized in alias_cache:
        return alias_cache[normalized]

    stack = set(stack or set())
    if normalized in stack:
        raise ValueError(f"Circular type alias reference: {normalized}")
    stack.add(normalized)

    alias_def = alias_defs[normalized]
    alias_type = str(alias_def.get("type", "")).strip()

    if alias_type == "literal":
        values = alias_def.get("values") or []
        if not values:
            raise ValueError(f"Literal alias '{normalized}' requires non-empty values")
        resolved = _build_literal_enum(normalized, values)
    elif alias_type == "union":
        variants = alias_def.get("variants") or []
        if not isinstance(variants, list) or not variants:
            raise ValueError(f"Union alias '{normalized}' requires non-empty variants")
        resolved_types = []
        optional_variant = False
        for variant in variants:
            variant_key = str(variant or "").strip()
            if variant_key.lower() in {"null", "none"}:
                optional_variant = True
                continue
            resolved_types.append(
                _resolve_named_type(
                    variant_key,
                    available_models,
                    alias_defs,
                    alias_cache,
                    stack,
                )
            )
        if not resolved_types:
            raise ValueError(f"Union alias '{normalized}' must include at least one non-null variant")
        unique_types = []
        for resolved_type in resolved_types:
            if resolved_type not in unique_types:
                unique_types.append(resolved_type)
        base_type = unique_types[0] if len(unique_types) == 1 else Union[tuple(unique_types)]  # type: ignore[misc]
        resolved = Optional[base_type] if optional_variant else base_type  # type: ignore[arg-type]
    else:
        raise ValueError(f"Unsupported alias type: {alias_type}")

    alias_cache[normalized] = resolved
    stack.remove(normalized)
    return resolved


def _inline_schema_refs(node: Any, defs: Dict[str, Any], stack: Optional[Set[str]] = None) -> Any:
    if stack is None:
        stack = set()

    if isinstance(node, dict):
        ref = node.get('$ref')
        if isinstance(ref, str) and ref.startswith('#/$defs/'):
            key = ref.split('/')[-1]
            if key in stack:
                return {k: _inline_schema_refs(v, defs, stack) for k, v in node.items() if k != '$ref'}
            if key in defs:
                stack.add(key)
                resolved = _inline_schema_refs(defs[key], defs, stack)
                stack.remove(key)
                remainder = {k: _inline_schema_refs(v, defs, stack) for k, v in node.items() if k != '$ref'}
                if remainder:
                    merged = dict(resolved)
                    merged.update(remainder)
                    return merged
                return resolved
        return {k: _inline_schema_refs(v, defs, stack) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_schema_refs(item, defs, stack) for item in node]
    return node


def _add_additional_properties(schema: Any) -> Any:
    """Recursively normalize object schemas for OpenAI strict structured outputs."""
    if isinstance(schema, dict):
        # OpenAI strict structured outputs require every object schema to:
        # 1. set additionalProperties explicitly
        # 2. provide a required array that includes every declared property
        if schema.get('type') == 'object':
            if schema.get('additionalProperties') is True or 'additionalProperties' not in schema:
                schema['additionalProperties'] = False
            properties = schema.get('properties')
            if isinstance(properties, dict):
                schema['required'] = list(properties.keys())
            elif 'required' not in schema:
                schema['required'] = []

        # Recursively process all nested schemas
        return {k: _add_additional_properties(v) for k, v in schema.items()}
    elif isinstance(schema, list):
        return [_add_additional_properties(item) for item in schema]
    return schema


def _patch_model_schema(model_cls: type[BaseModel]) -> None:
    """Ensure JSON schema expands nested refs for OpenAI structured outputs."""

    if getattr(model_cls, "__mozaiks_schema_patched", False):
        return

    def _model_json_schema(cls, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        schema = BaseModel.model_json_schema.__func__(cls, *args, **kwargs)  # type: ignore[attr-defined]
        defs = schema.pop('$defs', None)
        if isinstance(defs, dict) and defs:
            schema = _inline_schema_refs(schema, defs)
        # Add additionalProperties: false to all object types for OpenAI strict mode
        schema = _add_additional_properties(schema)
        return schema

    model_cls.model_json_schema = classmethod(_model_json_schema)  # type: ignore[assignment]
    setattr(model_cls, "__mozaiks_schema_patched", True)


def _is_pydantic_model(value: Any) -> bool:
    try:
        return isinstance(value, type) and issubclass(value, BaseModel)
    except Exception:
        return False


def _strictify_response_annotation(annotation: Any) -> Any:
    origin = get_origin(annotation)

    if _is_pydantic_model(annotation):
        return get_provider_response_model(annotation)

    try:
        if isinstance(annotation, type) and issubclass(annotation, Enum):
            values = tuple(member.value for member in annotation)
            if values:
                return Literal[values]  # type: ignore[valid-type]
    except Exception:
        pass

    if origin in (list, List):
        args = get_args(annotation)
        if not args:
            return annotation
        return List[_strictify_response_annotation(args[0])]  # type: ignore[valid-type]

    if origin in (set, Set):
        args = get_args(annotation)
        if not args:
            return annotation
        return Set[_strictify_response_annotation(args[0])]  # type: ignore[valid-type]

    if origin in (tuple, Tuple):
        args = get_args(annotation)
        if not args:
            return annotation
        return Tuple[tuple(_strictify_response_annotation(arg) for arg in args)]  # type: ignore[misc]

    if origin is Union:
        strict_args = tuple(_strictify_response_annotation(arg) for arg in get_args(annotation))
        return Union[strict_args]  # type: ignore[misc]

    return annotation


def get_provider_response_model(model_cls: type[BaseModel]) -> type[BaseModel]:
    cached = _provider_response_model_cache.get(model_cls)
    if cached is not None:
        return cached

    fields: Dict[str, Tuple[Any, Any]] = {}
    for field_name, field_info in getattr(model_cls, "model_fields", {}).items():
        raw_annotation = getattr(field_info, "annotation", Any)
        field_annotation = _strictify_response_annotation(raw_annotation)
        field_kwargs: Dict[str, Any] = {}
        description = getattr(field_info, "description", None)
        if (
            isinstance(description, str)
            and description.strip()
            and not _is_pydantic_model(field_annotation)
        ):
            field_kwargs["description"] = description
        fields[field_name] = (field_annotation, Field(..., **field_kwargs))

    strict_model = create_model(model_cls.__name__, **fields)  # type: ignore[arg-type]
    _patch_model_schema(strict_model)
    _provider_response_model_cache[model_cls] = strict_model
    return strict_model


def _find_open_ended_object_path(
    annotation: Any,
    *,
    path: str,
    visited_models: Optional[Set[type[BaseModel]]] = None,
) -> Optional[str]:
    """Return the first path that contains a freeform object/dict annotation."""
    visited_models = visited_models or set()

    origin = get_origin(annotation)
    if origin in (dict, Dict):
        return path

    if origin in (list, List, set, tuple):
        for arg in get_args(annotation):
            found = _find_open_ended_object_path(
                arg,
                path=f"{path}[]",
                visited_models=visited_models,
            )
            if found:
                return found
        return None

    if origin is Union:
        for arg in get_args(annotation):
            if arg is type(None):
                continue
            found = _find_open_ended_object_path(
                arg,
                path=path,
                visited_models=visited_models,
            )
            if found:
                return found
        return None

    if _is_pydantic_model(annotation):
        model_cls = annotation
        if model_cls in visited_models:
            return None
        visited_models.add(model_cls)
        try:
            model_fields = getattr(model_cls, "model_fields", {})
        except Exception:
            model_fields = {}
        for field_name, field_info in model_fields.items():
            field_annotation = getattr(field_info, "annotation", None)
            found = _find_open_ended_object_path(
                field_annotation,
                path=f"{path}.{field_name}",
                visited_models=visited_models,
            )
            if found:
                return found
        return None

    return None


def supports_provider_response_format(model_cls: type[BaseModel]) -> tuple[bool, Optional[str]]:
    """Return whether a model is safe for provider-enforced strict response_format.

    OpenAI strict structured outputs do not support open-ended object blobs like
    Dict[str, Any]. Those remain valid for Mozaiks runtime-side parsing and
    validation, but they should not be sent as provider response_format schemas.
    """
    offending_path = _find_open_ended_object_path(model_cls, path=model_cls.__name__)
    if offending_path:
        return False, offending_path
    return True, None


def _build_field(field_kwargs: Dict[str, Any]) -> Any:
    if 'default' in field_kwargs:
        default = field_kwargs.pop('default')
        return Field(default, **field_kwargs)
    return Field(..., **field_kwargs)


def resolve_field_type(
    field_def: Dict[str, Any],
    available_models: Dict[str, type],
    alias_defs: Optional[Dict[str, Dict[str, Any]]] = None,
    alias_cache: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, Any]:
    alias_defs = alias_defs or {}
    alias_cache = alias_cache or {}
    field_type_str = str(field_def.get('type', '')).strip()
    field_kwargs: Dict[str, Any] = {}
    if 'description' in field_def:
        field_kwargs['description'] = field_def['description']
    if 'default' in field_def:
        field_kwargs['default'] = field_def['default']
    if field_type_str == 'optional_dict':
        return Optional[Dict[str, Any]], _build_field(field_kwargs)  # type: ignore[return-value]
    # Primitive
    if field_type_str in {'list', 'optional_list'}:
        items_type = field_def.get('items')
        if not items_type:
            raise ValueError("List type requires 'items'")
        if isinstance(items_type, str):
            item_type = _resolve_named_type(items_type, available_models, alias_defs, alias_cache)
            base = List[item_type]  # type: ignore[valid-type]
        else:
            raise ValueError("Unsupported list items spec")
        if field_type_str == 'optional_list':
            return Optional[base], _build_field(field_kwargs)  # type: ignore[return-value]
        return base, _build_field(field_kwargs)  # type: ignore[return-value]
    if field_type_str in TYPE_MAP:
        return TYPE_MAP[field_type_str], _build_field(field_kwargs)
    # Literal -> Enum
    if field_type_str == 'literal':
        values = field_def.get('values') or []
        if not values:
            raise ValueError("Literal type requires 'values'")
        LiteralEnum = _build_literal_enum("Literal", values)
        return LiteralEnum, _build_field(field_kwargs)
    # list type
    # dict primitive support (already mapped in TYPE_MAP earlier, but handle explicit 'dict' path if missed)
    if field_type_str == 'dict':
        return Dict[str, Any], _build_field(field_kwargs)  # type: ignore
    if field_type_str == 'union':
        variants = field_def.get('variants') or []
        if not isinstance(variants, list) or not variants:
            raise ValueError("Union type requires 'variants'")
        resolved_types = []
        optional_variant = False
        for variant in variants:
            if isinstance(variant, str):
                variant_key = variant.strip()
                if variant_key.lower() in ('null', 'none'):
                    optional_variant = True
                    continue
                resolved_types.append(
                    _resolve_named_type(variant_key, available_models, alias_defs, alias_cache)
                )
                continue
            raise ValueError(f"Unknown union variant: {variant}")
        if not resolved_types:
            raise ValueError("Union type must include at least one non-null variant")
        unique_types = []
        for rtype in resolved_types:
            if rtype not in unique_types:
                unique_types.append(rtype)
        base_type = unique_types[0] if len(unique_types) == 1 else Union[tuple(unique_types)]  # type: ignore[misc]
        if optional_variant:
            base_type = Optional[base_type]  # type: ignore[arg-type]
        return base_type, _build_field(field_kwargs)
    # direct model ref
    if field_type_str in available_models or field_type_str in alias_defs:
        return _resolve_named_type(field_type_str, available_models, alias_defs, alias_cache), _build_field(field_kwargs)
    # list[Model]
    if field_type_str.startswith('list[') and field_type_str.endswith(']'):
        inner = field_type_str[5:-1].strip()
        inner_type = _resolve_named_type(inner, available_models, alias_defs, alias_cache)
        return List[inner_type], _build_field(field_kwargs)  # type: ignore
    raise ValueError(f"Unknown field type: {field_type_str}")

def build_models_from_config(models_config: Dict[str, Any]) -> Dict[str, type]:
    if not models_config:
        return {}
    models: Dict[str, type] = {}
    alias_defs: Dict[str, Dict[str, Any]] = {
        name: mdef
        for name, mdef in models_config.items()
        if isinstance(mdef, dict) and mdef.get("type") in {"literal", "union"}
    }
    alias_cache: Dict[str, Any] = {}
    pending: List[Tuple[str, Dict[str, Any]]] = []
    # first pass
    for name, mdef in models_config.items():
        if mdef.get('type') != 'model':
            continue
        fields: Dict[str, Tuple[Any, Any]] = {}
        unresolved = False
        for fname, fdef in (mdef.get('fields') or {}).items():
            try:
                ftype, fld = resolve_field_type(fdef, models, alias_defs, alias_cache)
                fields[fname] = (ftype, fld)
            except ValueError:
                unresolved = True
                break
        if unresolved:
            pending.append((name, mdef))
        else:
            model_cls = create_model(name, **fields)  # type: ignore[arg-type]
            _patch_model_schema(model_cls)
            models[name] = model_cls
    # iterative resolution
    for _ in range(len(pending)):
        remaining: List[Tuple[str, Dict[str, Any]]] = []
        for name, mdef in pending:
            fields: Dict[str, Tuple[Any, Any]] = {}
            unresolved = False
            for fname, fdef in (mdef.get('fields') or {}).items():
                try:
                    ftype, fld = resolve_field_type(fdef, models, alias_defs, alias_cache)
                    fields[fname] = (ftype, fld)
                except ValueError:
                    unresolved = True
                    break
            if unresolved:
                remaining.append((name, mdef))
            else:
                model_cls = create_model(name, **fields)  # type: ignore[arg-type]
                _patch_model_schema(model_cls)
                models[name] = model_cls
        pending = remaining
        if not pending:
            break
    if pending:
        raise ValueError(f"Unresolved model dependencies: {[n for n,_ in pending]}")
    return models

def load_workflow_structured_outputs(workflow_name: str) -> tuple[Dict[str, type], Dict[str, type]]:
    """Load structured outputs configuration for a workflow."""
    if workflow_name in _workflow_models:
        # Ensure structured agents cache is initialized before returning cached models.
        if workflow_name not in _workflow_structured_agents:
            _workflow_structured_agents[workflow_name] = set(_workflow_registries.get(workflow_name, {}).keys())
        return _workflow_models[workflow_name], _workflow_registries[workflow_name]
    
    # Load workflow configuration
    workflow_config = workflow_manager.get_config(workflow_name)
    if not workflow_config:
        raise ValueError(f"No configuration found for workflow: {workflow_name}")
    
    structured_config = workflow_config.get('structured_outputs', {})
    
    models_config = structured_config.get('models', {})
    registry_config = structured_config.get('registry', {})
    
    if not models_config:
        # No structured outputs defined - this is valid
        _workflow_models[workflow_name] = {}
        _workflow_registries[workflow_name] = {}
        _workflow_structured_agents[workflow_name] = set()
        return {}, {}
    
    # Build models from json config
    models = build_models_from_config(models_config)
    
    # Build registry mapping agent names to models
    registry = {}
    for agent_name, model_name in registry_config.items():
        if model_name is None:
            continue
        if model_name not in models:
            raise ValueError(f"Registry references unknown model '{model_name}' for agent '{agent_name}'")
        registry[agent_name] = models[model_name]
    
    # Cache results
    _workflow_models[workflow_name] = models
    _workflow_registries[workflow_name] = registry
    _workflow_structured_agents[workflow_name] = set(registry.keys())
    
    return models, registry

def get_structured_outputs_for_workflow(workflow_name: str) -> Dict[str, type]:
    """Get structured outputs registry for a specific workflow."""
    _, registry = load_workflow_structured_outputs(workflow_name)
    return registry

# ---------------------------------------------------------------------------
# NEW HELPER TAG / INTROSPECTION FUNCTIONS
# ---------------------------------------------------------------------------
def get_structured_output_agents(workflow_name: str) -> List[str]:
    """Return list of agent names in a workflow that produce structured outputs.

    Provides a simple 'tag' list the rest of the system (or UI) can use to
    decide whether to treat an agent's messages as candidates for JSON parsing.
    """
    load_workflow_structured_outputs(workflow_name)
    return sorted(_workflow_structured_agents.get(workflow_name, set()))

def agent_has_structured_output(workflow_name: str, agent_name: str) -> bool:
    """Boolean helper to check if an agent is registered for structured outputs."""
    load_workflow_structured_outputs(workflow_name)
    return agent_name in _workflow_structured_agents.get(workflow_name, set())

def get_structured_output_model_fields(workflow_name: str, agent_name: str) -> Dict[str, str]:
    """Return a mapping of field_name -> python_type_name for an agent's model.

    Useful for embedding lightweight schema hints in events or persistence so
    the frontend can render structured outputs without needing full Pydantic
    model objects.
    """
    registry = get_structured_outputs_for_workflow(workflow_name)
    model_cls = registry.get(agent_name)
    if not model_cls:
        return {}
    try:
        # Pydantic v2: model_fields contains field info
        return {fname: getattr(finfo.annotation, '__name__', str(finfo.annotation)) for fname, finfo in getattr(model_cls, 'model_fields', {}).items()}  # type: ignore[attr-defined]
    except Exception:
        try:  # fallback for models without pydantic v2 field metadata
            return {fname: type(getattr(model_cls, fname)).__name__ for fname in dir(model_cls) if not fname.startswith('_')}
        except Exception:
            return {}

def build_dynamic_models(spec_models: List[Dict[str, Any]], existing_models: Dict[str, type]) -> Dict[str, type]:
    """Build dynamic models from runtime specifications."""
    if not spec_models:
        return {}
    
    # Convert spec to YAjsonML-like format for reuse of existing logic
    models_config = {}
    for spec in spec_models:
        if not isinstance(spec, dict):
            continue
        model_name = spec.get('model_name')
        fields_spec = spec.get('fields', [])
        
        if not model_name or not isinstance(fields_spec, list):
            continue
        
        # Convert fields spec to json format
        fields = {}
        for field_spec in fields_spec:
            if not isinstance(field_spec, dict):
                continue
            fname = field_spec.get('name')
            ftype = field_spec.get('type')
            if not fname or not ftype:
                continue
            fdesc = field_spec.get('description')
            field_def: Dict[str, Any] = {'type': ftype}
            if fdesc:
                field_def['description'] = fdesc
            if 'items' in field_spec:
                field_def['items'] = field_spec['items']
            fields[fname] = field_def
        
        models_config[model_name] = {
            'type': 'model',
            'fields': fields
        }
    
    # Build models using existing logic, combining with existing models
    combined_models = existing_models.copy()
    new_models = build_models_from_config(models_config)
    combined_models.update(new_models)
    
    return new_models


def create_pydantic_model_from_schema(schema: Dict[str, Any]) -> type:
    """Create a Pydantic BaseModel class from a simple schema dict.

    Expected schema format::

        {
            "name": "MyModel",
            "fields": [
                {"name": "field_name", "type": "str", "description": "..."},
                ...
            ]
        }

    Supported type strings: str, int, float, bool, list, dict.
    Unknown types fall back to Any.
    """
    from pydantic import create_model as pydantic_create_model
    from typing import Optional as Opt

    _TYPE_MAP: Dict[str, Any] = {
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "list": list,
        "dict": dict,
    }

    model_name: str = schema.get("name", "DynamicModel")
    fields_spec = schema.get("fields", [])

    field_definitions: Dict[str, Any] = {}
    for field in fields_spec:
        fname = field.get("name")
        if not fname:
            continue
        ftype = _TYPE_MAP.get(str(field.get("type", "str")), Any)
        field_definitions[fname] = (ftype, ...)

    model_cls = pydantic_create_model(model_name, **field_definitions)
    return model_cls


async def get_llm_for_workflow(
    workflow_name: str,
    flow: str = "base",
    agent_name: Optional[str] = None,
    *,
    extra_config: Optional[dict] = None,
) -> tuple:
    """Create LLM config for an agent with optional structured response model."""
    
    try:
        structured_registry = get_structured_outputs_for_workflow(workflow_name)
        lookup_key = agent_name or flow
        
        if lookup_key in structured_registry:
            model_cls = structured_registry[lookup_key]
            supports_strict, offending_path = supports_provider_response_format(model_cls)
            if supports_strict:
                return await get_llm_config(
                    response_format=get_provider_response_model(model_cls),
                    extra_config=extra_config,
                )

            logger.warning(
                "[STRUCTURED_OUTPUTS] Provider strict response_format disabled for %s/%s (%s uses open-ended object fields)",
                workflow_name,
                lookup_key,
                offending_path,
            )
            return await get_llm_config(extra_config=extra_config)
    except (ValueError, FileNotFoundError):
        pass
    
    # Fallback to plain LLM config
    return await get_llm_config(extra_config=extra_config)
