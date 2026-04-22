# Structured Output Extraction Specification

**Status:** ASPIRATIONAL / NOT IMPLEMENTED
**Created:** 2026-04-07
**Depends on:** MODULAR_ARCHITECTURE_V2.md, PLATFORM_DOGFOODING_SPEC.md

> **WARNING:** This document describes an aspirational pattern for a "code generation"
> workflow that would produce workflow YAML files. This is NOT how the current mozaiks
> runtime handles structured outputs.
>
> For the actual runtime contract, see:
> [structured-output-extraction-contract.md](structured-output-extraction-contract.md)

---

## What This Document Describes (Aspirational)

This spec envisions a specialized workflow (e.g., `AgentGenerator`) where:
- Multiple agents (ContextVariablesAgent, ToolsManagerAgent, etc.) produce typed outputs
- A separate "Extractor" layer transforms those outputs into workflow YAML files
- The result is a newly generated workflow bundle

## What Actually Exists (Reality)

The current mozaiks runtime uses a simpler pattern:
- Agents produce structured JSON outputs
- The `AutoToolEventHandler` validates outputs against Pydantic models
- Tools with `auto_tool_call: true` receive validated outputs as kwargs
- Tools persist data, emit UI events, and update context

**See the canonical contract:** [structured-output-extraction-contract.md](structured-output-extraction-contract.md)

---

## Original Aspirational Spec (Preserved Below)

This document specifies how agent structured outputs (defined in `structured_outputs.yaml`) could be extracted and transformed into consistent YAML configuration files across any mozaiks application.

---

## Overview

### The Extraction Pipeline

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        EXTRACTION PIPELINE                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌───────────┐ │
│  │   Intent    │────▶│   Agent     │────▶│  Extractor  │────▶│   YAML    │ │
│  │   (User)    │     │  Produces   │     │  Transforms │     │   Files   │ │
│  │             │     │  Output     │     │  & Validates│     │           │ │
│  └─────────────┘     └─────────────┘     └─────────────┘     └───────────┘ │
│                             │                   │                           │
│                             ▼                   ▼                           │
│                    structured_outputs.yaml   extraction_contracts.yaml     │
│                    (Schema Definitions)      (Mapping Rules)               │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Core Principle

> **Every YAML configuration file has exactly one source agent output type, and every transformation is deterministic and validated.**

---

## 1. Extraction Contracts

An extraction contract defines the mapping between an agent's structured output and the target YAML file(s) it produces.

### Contract Registry

| Agent | Output Type | Target File(s) | Extractor |
|-------|-------------|----------------|-----------|
| `ContextVariablesAgent` | `ContextVariablesPlanOutput` | `context_variables.yaml` | `context_variables_extractor` |
| `ToolsManagerAgent` | `ToolsManifestOutput` | `tools.yaml` | `tools_manifest_extractor` |
| `OrchestratorAgent` | `OrchestrationConfigOutput` | `orchestrator.yaml` | `orchestration_extractor` |
| `HandoffsAgent` | `HandoffRulesOutput` | `handoffs.yaml` | `handoffs_extractor` |
| `AgentsAgent` | `RuntimeAgentsOutput` | `agents.yaml` | `agents_extractor` |
| `StructuredOutputsAgent` | `StructuredModelsOutput` | `structured_outputs.yaml` (workflow) | `structured_models_extractor` |
| `PatternAgent` | `PatternSelectionOutput` | `manifest.json` (partial) | `pattern_selection_extractor` |
| `WorkflowStrategyAgent` | `WorkflowStrategyOutput` | `workflow_strategy.yaml` | `workflow_strategy_extractor` |
| `StateArchitectAgent` | `StateArchitectureOutput` | `state_architecture.yaml` | `state_architecture_extractor` |
| `UXArchitectAgent` | `UXArchitectureOutput` | `ux_architecture.yaml` | `ux_architecture_extractor` |
| `PackMetadataAgent` | `PackMetadataOutput` | `extended_orchestration/mfj_extension.json` | `pack_metadata_extractor` |
| `DatabaseIntentAgent` | `DatabaseIntentOutput` | `db_intent.json` | `database_intent_extractor` |

### Contract Definition Schema

```yaml
# extraction_contracts.yaml
contracts:
  context_variables_extractor:
    source_agent: ContextVariablesAgent
    source_type: ContextVariablesPlanOutput
    target_file: context_variables.yaml
    target_schema: ContextVariablesFileSchema
    transformations:
      - source_path: ContextVariablesPlan.definitions
        target_path: definitions
        transform: identity
      - source_path: ContextVariablesPlan.agents
        target_path: agents
        transform: identity
    validations:
      - rule: required_fields
        fields: [definitions, agents]
      - rule: unique_names
        path: definitions[*].name
      - rule: valid_source_types
        path: definitions[*].source.type
        allowed: [config, data_reference, data_entity, computed, state, external]
```

---

## 2. Target File Schemas

Each target YAML file has a canonical schema that all extractors must produce.

### context_variables.yaml

```yaml
# Schema: ContextVariablesFileSchema
definitions:
  - name: string                    # snake_case, unique
    type: string | null             # string|integer|boolean|etc.
    description: string | null      # Human readable
    source:
      type: config|data_reference|data_entity|computed|state|external
      # Type-specific fields...

agents:
  - agent: string                   # Agent name
    variables: [string]             # Variables exposed to this agent
```

### tools.yaml

```json
{
  "tools": [
    {
      "agent": "string",
      "file": "string",
      "function": "string",
      "description": "string",
      "tool_type": "UI_Tool|UI_Surface|Agent_Tool",
      "auto_tool_call": "boolean|null",
      "ui": {
        "component": "string",
        "mode": "inline|artifact"
      },
      "ui_contract": "object|null"
    }
  ],
  "lifecycle_tools": [
    {
      "trigger": "before_chat|after_chat|before_agent|after_agent",
      "agent": "string|null",
      "file": "string",
      "function": "string",
      "description": "string",
      "tool_type": "UI_Tool|UI_Surface|Agent_Tool",
      "ui": {...}
    }
  ]
}
```

### orchestrator.yaml

```json
{
  "workflow_name": "string",
  "max_turns": "integer",
  "human_in_the_loop": "boolean",
  "startup_mode": "AgentDriven|UserDriven|BackendOnly",
  "orchestration_pattern": "string",
  "initial_message_to_user": "string|null",
  "initial_message": "string|null",
  "initial_agent": "string",
  "runtime_extensions": [
    {
      "kind": "api_router|startup_service|lifecycle_hooks",
      "entrypoint": "string"
    }
  ]
}
```

### handoffs.yaml

```yaml
handoff_rules:
  - source_agent: string
    target_agent: string
    handoff_type: condition|after_work
    condition: string | null
    condition_type: expression|string_llm | null
    condition_scope: pre | null
    transition_target: AgentTarget|RevertToUserTarget|TerminateTarget
```

### agents.yaml

```yaml
agents:
  - name: string
    display_name: string
    prompt_sections:
      - id: string
        heading: string
        content: string
    max_consecutive_auto_reply: integer
    structured_outputs_required: boolean
```

---

## 3. Transformation Rules

### Identity Transform

Copies the source value directly to the target with no modification.

```python
def identity(source_value):
    return source_value
```

### Flatten Transform

Flattens a nested object to a single level.

```python
def flatten(source_value, prefix=""):
    result = {}
    for key, value in source_value.items():
        new_key = f"{prefix}{key}" if prefix else key
        if isinstance(value, dict):
            result.update(flatten(value, f"{new_key}_"))
        else:
            result[new_key] = value
    return result
```

### Map Transform

Maps list items through a transformation function.

```python
def map_transform(source_list, item_transform):
    return [item_transform(item) for item in source_list]
```

### Rename Transform

Renames keys according to a mapping.

```python
def rename(source_value, mapping):
    """
    mapping: { "old_key": "new_key" }
    """
    result = {}
    for key, value in source_value.items():
        new_key = mapping.get(key, key)
        result[new_key] = value
    return result
```

### Conditional Transform

Applies transformation only when condition is met.

```python
def conditional(source_value, condition, transform):
    if condition(source_value):
        return transform(source_value)
    return source_value
```

### Default Transform

Provides default values for missing fields.

```python
def with_defaults(source_value, defaults):
    result = {**defaults}
    result.update(source_value)
    return result
```

---

## 4. Validation Rules

### Required Fields

Ensures all required fields are present.

```python
def validate_required_fields(data, fields):
    missing = [f for f in fields if f not in data or data[f] is None]
    if missing:
        raise ValidationError(f"Missing required fields: {missing}")
```

### Unique Names

Ensures a list of items has unique name values.

```python
def validate_unique_names(items, name_path="name"):
    names = [get_path(item, name_path) for item in items]
    duplicates = [n for n in names if names.count(n) > 1]
    if duplicates:
        raise ValidationError(f"Duplicate names found: {set(duplicates)}")
```

### Valid Enum Values

Ensures field values match allowed enum values.

```python
def validate_enum(data, path, allowed_values):
    value = get_path(data, path)
    if value not in allowed_values:
        raise ValidationError(f"Invalid value '{value}' at {path}. Allowed: {allowed_values}")
```

### Cross-Reference Validation

Ensures references point to valid items.

```python
def validate_cross_reference(data, ref_path, target_path):
    refs = get_all_values(data, ref_path)
    targets = get_all_values(data, target_path)
    invalid = [r for r in refs if r not in targets]
    if invalid:
        raise ValidationError(f"Invalid references: {invalid}")
```

### Schema Validation

Validates the entire structure against a JSON Schema.

```python
def validate_schema(data, schema):
    jsonschema.validate(data, schema)
```

---

## 5. Extractor Implementation

### Base Extractor Class

```python
# extractors/base.py
from abc import ABC, abstractmethod
from typing import Any, Dict, List

class BaseExtractor(ABC):
    """Base class for all output extractors."""

    @property
    @abstractmethod
    def source_type(self) -> str:
        """The source output type name."""
        pass

    @property
    @abstractmethod
    def target_file(self) -> str:
        """The target file name to produce."""
        pass

    @abstractmethod
    def transform(self, output: Dict[str, Any]) -> Dict[str, Any]:
        """Transform the agent output to target format."""
        pass

    @abstractmethod
    def validate(self, data: Dict[str, Any]) -> List[str]:
        """Validate the transformed data. Returns list of errors."""
        pass

    def extract(self, agent_output: Dict[str, Any]) -> Dict[str, Any]:
        """Main extraction pipeline."""
        # 1. Transform
        transformed = self.transform(agent_output)

        # 2. Validate
        errors = self.validate(transformed)
        if errors:
            raise ExtractionError(f"Validation failed: {errors}")

        # 3. Return
        return transformed
```

### Context Variables Extractor

```python
# extractors/context_variables.py

class ContextVariablesExtractor(BaseExtractor):
    """Extracts context_variables.yaml from ContextVariablesPlanOutput."""

    source_type = "ContextVariablesPlanOutput"
    target_file = "context_variables.yaml"

    VALID_SOURCE_TYPES = [
        "config", "data_reference", "data_entity",
        "computed", "state", "external"
    ]

    def transform(self, output: Dict[str, Any]) -> Dict[str, Any]:
        plan = output.get("ContextVariablesPlan", output)

        return {
            "definitions": plan.get("definitions", []),
            "agents": plan.get("agents", [])
        }

    def validate(self, data: Dict[str, Any]) -> List[str]:
        errors = []

        # Required fields
        if "definitions" not in data:
            errors.append("Missing required field: definitions")
        if "agents" not in data:
            errors.append("Missing required field: agents")

        # Unique names
        names = [d.get("name") for d in data.get("definitions", [])]
        duplicates = [n for n in names if names.count(n) > 1]
        if duplicates:
            errors.append(f"Duplicate variable names: {set(duplicates)}")

        # Valid source types
        for defn in data.get("definitions", []):
            source_type = defn.get("source", {}).get("type")
            if source_type and source_type not in self.VALID_SOURCE_TYPES:
                errors.append(f"Invalid source type '{source_type}' for {defn.get('name')}")

        # Agent references valid
        defined_names = set(names)
        for agent_entry in data.get("agents", []):
            for var in agent_entry.get("variables", []):
                if var not in defined_names:
                    errors.append(f"Agent '{agent_entry.get('agent')}' references undefined variable: {var}")

        return errors
```

---

## 6. Module YAML Extraction

For platform dogfooding, module.yaml files have a specific extraction pattern.

### Module Definition Contract

```yaml
# Contract: ModuleDefinitionExtractor
contracts:
  module_definition_extractor:
    source_type: ModuleDefinition  # From platform workflow
    target_file: "{module_name}.module.yaml"
    target_schema: ModuleDefinitionSchema
    transformations:
      - source_path: module_name
        target_path: name
        transform: identity
      - source_path: module_version
        target_path: version
        transform: identity
      - source_path: module_description
        target_path: description
        transform: identity
      - source_path: is_external
        target_path: external
        transform: identity
      - source_path: service_name
        target_path: service
        transform: conditional_include  # Only if external=true
      - source_path: actions
        target_path: actions
        transform: map_action_definitions
```

### Module Action Transform

```python
def map_action_definitions(actions: List[Dict]) -> List[Dict]:
    """Transform action definitions to module.yaml format."""
    return [
        {
            "name": action["name"],
            "type": action["action_type"],  # query|mutation
            "description": action.get("description"),
            "params": [
                {
                    "name": p["name"],
                    "type": p["param_type"],
                    "required": p.get("required", False),
                    "optional": p.get("optional", False),
                    "default": p.get("default"),
                    "enum": p.get("enum")
                }
                for p in action.get("params", [])
            ],
            "returns": action.get("returns"),
            "emits": action.get("emits", [])
        }
        for action in actions
    ]
```

---

## 7. Page YAML Extraction

Admin pages for dogfooding follow a specific schema.

### Page Definition Schema

```yaml
# Schema: PageDefinitionSchema
type: Page
title: string
layout: dashboard|list|detail|form
access:
  roles: [string]

data:
  {key}:
    source: "module:{module}:{action}"
    params: {}

filters:  # Optional, for list layouts
  - key: string
    type: search|select|date_range
    label: string
    options: []  # For select type

sections:
  - type: Section|DataTable|StatGroup|Grid|Card|...
    # Type-specific fields...

modals:  # Optional
  {ModalName}:
    title: string
    variant: default|destructive
    children: []
```

### Page Extractor

```python
# extractors/page.py

class PageExtractor(BaseExtractor):
    """Extracts page.yaml from PageDefinitionOutput."""

    VALID_LAYOUTS = ["dashboard", "list", "detail", "form"]
    VALID_COMPONENT_TYPES = [
        "Section", "DataTable", "StatGroup", "Grid", "Card",
        "Stack", "Text", "Badge", "Avatar", "Icon", "Alert",
        "Form", "Button", "Link"
    ]

    def transform(self, output: Dict[str, Any]) -> Dict[str, Any]:
        page = output.get("PageDefinition", output)

        return {
            "type": "Page",
            "title": page.get("title"),
            "layout": page.get("layout", "list"),
            "access": page.get("access", {}),
            "data": self._transform_data_bindings(page.get("data", {})),
            "filters": page.get("filters"),
            "sections": self._transform_sections(page.get("sections", [])),
            "modals": page.get("modals")
        }

    def _transform_data_bindings(self, data: Dict) -> Dict:
        """Ensure data source strings follow module: pattern."""
        result = {}
        for key, binding in data.items():
            if isinstance(binding, dict):
                source = binding.get("source", "")
                # Validate source format: module:{name}:{action}
                if source and not source.startswith("module:"):
                    raise ExtractionError(f"Invalid data source format: {source}")
                result[key] = binding
        return result

    def _transform_sections(self, sections: List[Dict]) -> List[Dict]:
        """Recursively transform section definitions."""
        return [self._transform_component(s) for s in sections]

    def _transform_component(self, component: Dict) -> Dict:
        """Transform a single component, handling nested children."""
        result = {**component}

        # Recursively transform children
        if "children" in result:
            result["children"] = [
                self._transform_component(c) for c in result["children"]
            ]

        return result

    def validate(self, data: Dict[str, Any]) -> List[str]:
        errors = []

        if data.get("type") != "Page":
            errors.append("type must be 'Page'")

        if not data.get("title"):
            errors.append("Missing required field: title")

        layout = data.get("layout")
        if layout and layout not in self.VALID_LAYOUTS:
            errors.append(f"Invalid layout '{layout}'. Allowed: {self.VALID_LAYOUTS}")

        # Validate component types
        errors.extend(self._validate_sections(data.get("sections", [])))

        return errors

    def _validate_sections(self, sections: List[Dict]) -> List[str]:
        errors = []
        for section in sections:
            comp_type = section.get("type")
            if comp_type and comp_type not in self.VALID_COMPONENT_TYPES:
                errors.append(f"Invalid component type: {comp_type}")

            # Recurse into children
            if "children" in section:
                errors.extend(self._validate_sections(section["children"]))

        return errors
```

---

## 8. Extraction Registry

The extraction registry manages all extractors and routes agent outputs to the correct extractor.

```python
# extractors/registry.py

class ExtractionRegistry:
    """Central registry for all extractors."""

    _extractors: Dict[str, BaseExtractor] = {}

    @classmethod
    def register(cls, extractor: BaseExtractor):
        """Register an extractor."""
        cls._extractors[extractor.source_type] = extractor

    @classmethod
    def get_extractor(cls, source_type: str) -> BaseExtractor:
        """Get extractor for a source type."""
        if source_type not in cls._extractors:
            raise ValueError(f"No extractor registered for {source_type}")
        return cls._extractors[source_type]

    @classmethod
    def extract(cls, source_type: str, output: Dict) -> Dict:
        """Extract using the appropriate extractor."""
        extractor = cls.get_extractor(source_type)
        return extractor.extract(output)

    @classmethod
    def extract_all(cls, agent_outputs: Dict[str, Dict]) -> Dict[str, Dict]:
        """Extract all outputs from a generation run."""
        results = {}
        for agent_name, output in agent_outputs.items():
            # Look up source type from agent registry
            source_type = AGENT_OUTPUT_REGISTRY.get(agent_name)
            if source_type and source_type in cls._extractors:
                results[agent_name] = cls.extract(source_type, output)
        return results

# Register all extractors
ExtractionRegistry.register(ContextVariablesExtractor())
ExtractionRegistry.register(ToolsManifestExtractor())
ExtractionRegistry.register(OrchestrationExtractor())
ExtractionRegistry.register(HandoffsExtractor())
ExtractionRegistry.register(AgentsExtractor())
ExtractionRegistry.register(PackMetadataExtractor())
ExtractionRegistry.register(PageExtractor())
# ... etc
```

---

## 9. File Writer

The file writer serializes extracted data to disk with consistent formatting.

```python
# extractors/writer.py
import yaml
import json
from pathlib import Path

class FileWriter:
    """Writes extracted data to YAML/JSON files with consistent formatting."""

    YAML_EXTENSIONS = [".yaml", ".yml"]
    JSON_EXTENSIONS = [".json"]

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    def write(self, filename: str, data: Dict[str, Any]):
        """Write data to file with appropriate format."""
        filepath = self.output_dir / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)

        ext = filepath.suffix.lower()

        if ext in self.YAML_EXTENSIONS:
            self._write_yaml(filepath, data)
        elif ext in self.JSON_EXTENSIONS:
            self._write_json(filepath, data)
        else:
            raise ValueError(f"Unknown file extension: {ext}")

    def _write_yaml(self, filepath: Path, data: Dict):
        """Write YAML with consistent formatting."""
        with open(filepath, "w", encoding="utf-8") as f:
            yaml.dump(
                data,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
                width=100,
                indent=2
            )

    def _write_json(self, filepath: Path, data: Dict):
        """Write JSON with consistent formatting."""
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")  # Trailing newline
```

---

## 10. Integration with Generation Pipeline

The extraction system integrates with the workflow generation pipeline:

```python
# generation/pipeline.py

async def generate_workflow(intent: str, context: Dict) -> GenerationResult:
    """Full workflow generation pipeline."""

    # 1. Run all agents (produces structured outputs)
    agent_outputs = await run_generation_agents(intent, context)

    # 2. Extract all outputs to target formats
    extracted = ExtractionRegistry.extract_all(agent_outputs)

    # 3. Write files
    writer = FileWriter(output_dir=context["workflow_path"])

    for agent_name, data in extracted.items():
        extractor = ExtractionRegistry.get_extractor(
            AGENT_OUTPUT_REGISTRY[agent_name]
        )
        writer.write(extractor.target_file, data)

    return GenerationResult(
        workflow_path=context["workflow_path"],
        files_written=list(extracted.keys())
    )
```

---

## Summary

| Concept | Purpose |
|---------|---------|
| **Extraction Contract** | Maps agent output type to target file and schema |
| **Transformation** | Converts output structure to target format |
| **Validation** | Ensures target data is correct and complete |
| **Registry** | Routes outputs to correct extractors |
| **File Writer** | Serializes with consistent formatting |

This extraction system ensures that:
1. Every agent output maps to exactly one file type
2. Transformations are explicit and deterministic
3. All outputs are validated before writing
4. File formatting is consistent across all generated workflows
