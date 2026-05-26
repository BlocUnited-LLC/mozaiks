"""
TreeSitter-Based Code Extractor

Full AST-based extraction using TreeSitter with type hint relationship inference.
Outputs context types for agent-specific consumption:
- config_config, database_config, middleware_config
- model_context, service_context, controller_context, route_context
- frontend_config_context, utilities_api_context, utilities_styles_context
- component_context, pages_context

Ported from project-aid-v2 code_context_manager.py
"""

import base64
import json
import logging
import os
import re
import traceback
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Import tree-sitter and language packages
try:
    from tree_sitter import Language, Parser
    import tree_sitter_python
    import tree_sitter_javascript
    import tree_sitter_css
    TREE_SITTER_AVAILABLE = True
except ImportError as e:
    TREE_SITTER_AVAILABLE = False
    logger.warning(f"tree_sitter packages not fully available: {e}")


class TreeSitterChunker:
    """
    Parses source code into structured context using Tree-sitter AST parsing.
    Detects relationships from type hints (List[X] -> one_to_many, Optional[X] -> nullable).
    Routes extraction based on source agent for context type assignment.
    """

    def __init__(self):
        if TREE_SITTER_AVAILABLE:
            # Initialize language parsers
            self.PYTHON = Language(tree_sitter_python.language())
            self.JAVASCRIPT = Language(tree_sitter_javascript.language())
            self.CSS = Language(tree_sitter_css.language())
            
            # Map file extensions to parsers
            self.parsers = {
                '.py': Parser(self.PYTHON),
                '.js': Parser(self.JAVASCRIPT),
                '.jsx': Parser(self.JAVASCRIPT),
                '.ts': Parser(self.JAVASCRIPT),  # TypeScript uses JS parser as fallback
                '.tsx': Parser(self.JAVASCRIPT),
                '.css': Parser(self.CSS),
            }
        else:
            self.parsers = {}
            logger.warning("Tree-sitter not available; extraction will use fallback methods")

    def chunk_file(
        self,
        content: str,
        file_extension: str,
        source_agent: str,
        filename: str
    ) -> List[Dict[str, Any]]:
        """
        Entry point: parse file content and apply agent-specific extraction.
        
        Args:
            content: Raw file content
            file_extension: File extension (e.g., '.py', '.js')
            source_agent: Agent that produced this file (determines context_type)
            filename: Full file path
        
        Returns:
            List of context dictionaries with context_type field
        """
        if not content:
            return []

        ext = file_extension.lower()
        if ext not in self.parsers and TREE_SITTER_AVAILABLE:
            return [self._build_raw_file_context(content, filename)]

        # Build initial file context using AST
        if ext == '.py':
            file_context = self._build_python_file_context(content, filename)
        elif ext in ('.js', '.jsx', '.ts', '.tsx'):
            file_context = self._build_javascript_file_context(content, filename)
        elif ext == '.css':
            file_context = self._build_css_file_context(content, filename)
        else:
            file_context = self._build_raw_file_context(content, filename)

        # Apply agent-specific extraction to assign context_type
        file_context = self._apply_extraction_logic(file_context, source_agent)

        return [file_context]

    def get_module_name(self, filename: str) -> str:
        """Converts a file path to a module-style dotted name."""
        module_path = filename.replace("\\", "/").replace("/", ".")
        if module_path.endswith(".py"):
            module_path = module_path[:-3]
        elif module_path.endswith(".js") or module_path.endswith(".ts"):
            module_path = module_path[:-3]
        elif module_path.endswith(".jsx") or module_path.endswith(".tsx"):
            module_path = module_path[:-4]
        return module_path

    # =========================================================================
    # AST Building Methods (language-specific)
    # =========================================================================

    def _build_python_file_context(self, content: str, filename: str) -> Dict[str, Any]:
        """Build initial context for Python files using tree-sitter AST."""
        parser = self.parsers.get('.py')
        if not parser:
            return self._build_raw_file_context(content, filename)
        
        tree = parser.parse(bytes(content, "utf-8"))
        root_node = tree.root_node
        
        context = {
            "filename": filename,
            "file_type": "python",
            "module": self.get_module_name(filename),
            "raw_content": content,
            "classes": [],
            "functions": [],
            "imports": [],
            "dependencies": []
        }
        
        for child in root_node.children:
            if child.type == "class_definition":
                class_info = self._extract_python_class(content, child)
                if class_info:
                    context["classes"].append(class_info)
            elif child.type == "function_definition":
                func_info = self._extract_python_function(content, child)
                if func_info:
                    context["functions"].append(func_info)
            elif child.type in ("import_statement", "import_from_statement"):
                import_text = self._extract_node_text(content, child)
                context["imports"].append(import_text)
        
        return context

    def _build_javascript_file_context(self, content: str, filename: str) -> Dict[str, Any]:
        """Build initial context for JavaScript/TypeScript files."""
        parser = self.parsers.get('.js')
        if not parser:
            return self._build_raw_file_context(content, filename)
        
        tree = parser.parse(bytes(content, "utf-8"))
        root_node = tree.root_node
        
        context = {
            "filename": filename,
            "file_type": "javascript",
            "module": self.get_module_name(filename),
            "raw_content": content,
            "classes": [],
            "functions": [],
            "imports": [],
            "exports": []
        }
        
        for child in root_node.children:
            if child.type == "class_declaration":
                class_info = self._extract_js_class(content, child)
                if class_info:
                    context["classes"].append(class_info)
            elif child.type in ("function_declaration", "arrow_function"):
                func_info = self._extract_js_function(content, child)
                if func_info:
                    context["functions"].append(func_info)
            elif child.type == "import_statement":
                import_text = self._extract_node_text(content, child)
                context["imports"].append(import_text)
            elif child.type in ("export_statement", "export_default_declaration"):
                export_text = self._extract_node_text(content, child)
                context["exports"].append(export_text)
        
        return context

    def _build_css_file_context(self, content: str, filename: str) -> Dict[str, Any]:
        """Build initial context for CSS files."""
        context = {
            "filename": filename,
            "file_type": "css",
            "module": self.get_module_name(filename),
            "raw_content": content,
            "selectors": [],
            "variables": []
        }
        
        # Extract CSS custom properties (variables)
        var_pattern = re.compile(r'--[\w-]+:\s*[^;]+;')
        for match in var_pattern.finditer(content):
            context["variables"].append(match.group())
        
        # Extract selectors
        selector_pattern = re.compile(r'([.#]?[\w-]+)\s*{')
        for match in selector_pattern.finditer(content):
            context["selectors"].append(match.group(1))
        
        return context

    def _build_raw_file_context(self, content: str, filename: str) -> Dict[str, Any]:
        """Fallback for unsupported file types."""
        return {
            "filename": filename,
            "file_type": "raw",
            "module": self.get_module_name(filename),
            "raw_content": content[:5000],  # Truncate large files
            "context_type": "raw_context"
        }

    # =========================================================================
    # Tree-sitter Node Helpers
    # =========================================================================

    def _find_child_of_type(self, node, child_type: str):
        """Finds the first child node of the given type."""
        for child in node.children:
            if child.type == child_type:
                return child
        return None

    def _extract_node_text(self, content: str, node) -> str:
        """Extracts the source text corresponding to the given node."""
        return content[node.start_byte:node.end_byte]

    def _clean_docstring(self, doc: str) -> str:
        """Enhanced docstring cleaning that handles triple quotes and indentation."""
        doc = doc.strip().strip('"""').strip("'''")
        lines = doc.split('\n')
        if len(lines) > 1:
            indents = [len(line) - len(line.lstrip()) for line in lines[1:] if line.strip()]
            if indents:
                min_indent = min(indents)
                lines = [lines[0]] + [line[min_indent:] if line.strip() else '' for line in lines[1:]]
        return '\n'.join(lines).strip()

    def _find_function_docstring(self, content: str, node) -> str:
        """Extract function docstring."""
        for child in node.children:
            if child.type == "string":
                return self._clean_docstring(self._extract_node_text(content, child))
        
        block_node = self._find_child_of_type(node, "block")
        if block_node and block_node.children:
            first_stmt = block_node.children[0]
            if first_stmt.type == "expression_statement":
                string_node = self._find_child_of_type(first_stmt, "string")
                if string_node:
                    return self._clean_docstring(self._extract_node_text(content, string_node))
        return ""

    def _find_class_docstring(self, content: str, node) -> str:
        """Extract class-level docstring."""
        for child in node.children:
            if child.type == "string":
                return self._clean_docstring(self._extract_node_text(content, child))
            elif child.type == "block":
                if child.children and child.children[0].type == "expression_statement":
                    string_node = self._find_child_of_type(child.children[0], "string")
                    if string_node:
                        return self._clean_docstring(self._extract_node_text(content, string_node))
        return ""

    def _extract_function_params(self, content: str, node) -> List[str]:
        """Extract function parameters from a function definition node."""
        params_node = self._find_child_of_type(node, "parameters")
        if params_node:
            params_text = self._extract_node_text(content, params_node)
            params_text = params_text.strip("()")
            return [p.strip() for p in params_text.split(",") if p.strip()]
        return []

    def _extract_class_attributes(self, content: str, node) -> Dict[str, str]:
        """Extract class-level attribute assignments."""
        attributes = {}
        for child in node.children:
            if child.type == "assignment":
                left = self._find_child_of_type(child, "identifier")
                if left:
                    attr_name = self._extract_node_text(content, left)
                    attributes[attr_name] = self._extract_node_text(content, child)
        return attributes

    def _extract_class_methods(self, content: str, node) -> List[Dict[str, Any]]:
        """Extract method definitions from within a class."""
        methods = []
        for child in node.children:
            if child.type == "function_definition":
                func_name_node = self._find_child_of_type(child, "identifier")
                if func_name_node:
                    func_name = self._extract_node_text(content, func_name_node)
                    docstring = self._find_function_docstring(content, child)
                    params = self._extract_function_params(content, child)
                    methods.append({
                        "name": func_name,
                        "parameters": params,
                        "docstring": docstring
                    })
        return methods

    def _find_instance_attributes(self, init_method, content: str) -> Dict[str, str]:
        """Extract instance attributes from __init__ method."""
        attributes = {}
        if init_method and hasattr(init_method, 'children'):
            for stmt in init_method.children:
                if stmt.type == "expression_statement":
                    assignment = self._find_child_of_type(stmt, "assignment")
                    if assignment:
                        attr_access = self._find_child_of_type(assignment, "attribute")
                        if attr_access:
                            attr_name = content[attr_access.start_byte:attr_access.end_byte]
                            if attr_name.startswith("self."):
                                attr_name = attr_name[5:]
                                attr_value = content[assignment.start_byte:assignment.end_byte]
                                attributes[attr_name] = attr_value
        return attributes

    def _extract_class_definition(self, class_node, content: str) -> Dict[str, Any]:
        """Helper to extract class information including instance attributes."""
        class_info = {}
        class_name = self._find_child_of_type(class_node, "identifier")
        if class_name:
            class_info["name"] = content[class_name.start_byte:class_name.end_byte]
            class_info["docstring"] = self._find_class_docstring(content, class_node)
            
            # Find __init__ method for instance attributes
            init_method = None
            for method in class_node.children:
                if method.type == "function_definition":
                    method_name = self._find_child_of_type(method, "identifier")
                    if method_name and content[method_name.start_byte:method_name.end_byte] == "__init__":
                        init_method = method
                        break
            
            static_attrs = self._extract_class_attributes(content, class_node)
            instance_attrs = self._find_instance_attributes(init_method, content)
            class_info["attributes"] = {**static_attrs, **instance_attrs}
            class_info["methods"] = self._extract_class_methods(content, class_node)
        
        return class_info

    # =========================================================================
    # Python Extraction
    # =========================================================================

    def _extract_python_class(self, content: str, node) -> Dict[str, Any]:
        """Extract a Python class definition with attributes and methods."""
        class_info = {}
        identifier = self._find_child_of_type(node, "identifier")
        class_info["name"] = self._extract_node_text(content, identifier) if identifier else "Unknown"
        class_info["docstring"] = self._find_class_docstring(content, node)
        class_info["attributes"] = self._extract_class_attributes(content, node)
        class_info["methods"] = self._extract_class_methods(content, node)
        return class_info

    def _extract_python_function(self, content: str, node) -> Dict[str, Any]:
        """Extract a Python function definition."""
        func_info = {}
        identifier = self._find_child_of_type(node, "identifier")
        func_info["name"] = self._extract_node_text(content, identifier) if identifier else "Unknown"
        func_info["docstring"] = self._find_function_docstring(content, node)
        func_info["parameters"] = self._extract_function_params(content, node)
        return func_info

    # =========================================================================
    # JavaScript Extraction
    # =========================================================================

    def _extract_js_docstring(self, content: str, node) -> str:
        """Extract JavaScript/JSX docstring from preceding block comment (/** ... */)."""
        start_byte = node.start_byte
        preceding_text = content[:start_byte]
        start_index = preceding_text.rfind("/**")
        if start_index == -1:
            return ""
        end_index = preceding_text.find("*/", start_index)
        if end_index == -1:
            return ""
        docstring = preceding_text[start_index:end_index + 2]
        return docstring.strip()

    def _extract_js_class(self, content: str, node) -> Dict[str, Any]:
        """Extract a JavaScript class definition."""
        class_info = {}
        identifier = self._find_child_of_type(node, "identifier")
        class_info["name"] = self._extract_node_text(content, identifier) if identifier else "Unknown"
        class_info["docstring"] = self._extract_js_docstring(content, node)
        methods = []
        for child in node.children:
            if child.type == "method_definition":
                method_name_node = self._find_child_of_type(child, "property_identifier")
                method_name = self._extract_node_text(content, method_name_node) if method_name_node else "unknown"
                methods.append({
                    "name": method_name,
                    "parameters": [],
                    "docstring": ""
                })
        class_info["methods"] = methods
        return class_info

    def _extract_js_function(self, content: str, node) -> Dict[str, Any]:
        """Extract a JavaScript function definition."""
        func_info = {}
        identifier = self._find_child_of_type(node, "identifier")
        func_info["name"] = self._extract_node_text(content, identifier) if identifier else "Unknown"
        func_info["docstring"] = self._extract_js_docstring(content, node)
        params_node = self._find_child_of_type(node, "formal_parameters")
        if params_node:
            params_text = self._extract_node_text(content, params_node)
            params_text = params_text.strip("()")
            func_info["parameters"] = [p.strip() for p in params_text.split(",") if p.strip()]
        else:
            func_info["parameters"] = []
        return func_info

    # =========================================================================
    # Agent-Specific Extraction (assigns context_type)
    # =========================================================================

    def _apply_extraction_logic(self, file_context: dict, source_agent: str) -> dict:
        """Route to agent-specific extraction and assign context_type."""
        logger.debug(f"Applying extraction for {source_agent}")
        logger.debug(f"Context keys: {file_context.keys()}")
        
        extraction_map = {
            "ConfigMiddlewareAgent": self._determine_config_middleware_extraction,
            "ModelAgent": self._extract_model_context,
            "ServiceAgent": self._extract_service_context,
            "ControllerAgent": self._extract_controller_context,
        }
        
        if source_agent in extraction_map:
            try:
                content = file_context.get("raw_content", "")
                file_extension = os.path.splitext(file_context.get("filename", ""))[-1].lower()
                
                parser = self.parsers.get(file_extension)
                if not parser:
                    file_context["context_type"] = "raw_context"
                    return file_context
                
                tree = parser.parse(bytes(content, "utf-8"))
                if not tree or not tree.root_node:
                    file_context["context_type"] = "raw_context"
                    return file_context
                
                return extraction_map[source_agent](tree.root_node, file_context)
            except Exception as e:
                logger.error(f"Extraction error for {source_agent}: {e}", exc_info=True)
                file_context["context_type"] = "raw_context"
                return file_context
        else:
            file_context["context_type"] = "raw_context"
            return file_context

    def _determine_config_middleware_extraction(self, node, context: dict) -> dict:
        """Determine which specialized extraction for ConfigMiddlewareAgent."""
        filename = context.get("filename", "").lower()
        if "database" in filename:
            return self._extract_database_config(node, context)
        elif "middleware" in filename:
            return self._extract_middleware_config(node, context)
        else:
            return self._extract_config_config(node, context)

    # =========================================================================
    # Config Extractions
    # =========================================================================

    def _find_preceding_comment(self, content: str, start_pos: int) -> Optional[str]:
        """Finds a comment that precedes a given position."""
        lines = content[:start_pos].split('\n')
        comments = []
        for i in range(min(3, len(lines) - 1), 0, -1):
            line = lines[-i].strip()
            if line.startswith('#'):
                comments.append(line[1:].strip())
            elif line == '':
                continue
            else:
                break
        return ' '.join(comments) if comments else None

    def _find_module_docstring(self, content: str) -> Optional[str]:
        """Extract module-level docstring if present."""
        docstring_pattern = re.compile(r'^"""(.*?)"""', re.MULTILINE | re.DOTALL)
        match = docstring_pattern.search(content)
        if match:
            return match.group(1).strip()
        return None

    def _extract_config_config(self, node, context: dict) -> Dict[str, Any]:
        """Extract environment variables from config files."""
        module_name = self.get_module_name(context.get("filename", ""))
        content = context.get("raw_content", "")
        
        config_info = {
            "module": module_name,
            "variables": {},
            "classes": [],
            "functions": [],
            "context_type": "config_config"
        }

        # Regex patterns for environment variable assignments
        patterns = {
            "env_get": re.compile(
                r'(?P<var_name>\w+)\s*=\s*os\.environ\.get\(\s*[\'"](?P<env_var>[\w_]+)[\'"]\s*(?:,\s*[\'"](?P<default>[^\'"]+)[\'"])?\s*\)'
            ),
            "env_direct": re.compile(
                r'(?P<var_name>\w+)\s*=\s*os\.environ\[[\'"](?P<env_var>[\w_]+)[\'"]\]'
            ),
            "class_env": re.compile(
                r'self\.(?P<var_name>\w+)\s*=\s*os\.environ\.get\(\s*[\'"](?P<env_var>[\w_]+)[\'"]\s*(?:,\s*[\'"](?P<default>[^\'"]+)[\'"])?\s*\)'
            ),
            "getenv_direct": re.compile(
                r'(?P<var_name>\w+)\s*=\s*os\.getenv\(\s*[\'"](?P<env_var>[\w_]+)[\'"]\s*(?:,\s*[\'"](?P<default>[^\'"]+)[\'"])?\s*\)'
            ),
        }

        for pattern_name, pattern in patterns.items():
            for match in pattern.finditer(content):
                var_name = match.group("var_name")
                env_var = match.group("env_var")
                default = match.group("default") if "default" in match.groupdict() else None
                
                if pattern_name.startswith("class_"):
                    var_name = f"self.{var_name}"

                config_info["variables"][env_var] = {
                    "name": var_name,
                    "default": default,
                    "required": default is None,
                    "docstring": self._find_preceding_comment(content, match.start()),
                    "pattern_type": pattern_name
                }

        module_docstring = self._find_module_docstring(content)
        if module_docstring:
            config_info["docstring"] = module_docstring

        return config_info

    def _extract_database_config(self, node, context: dict) -> Dict[str, Any]:
        """Extract database configuration context."""
        module_name = self.get_module_name(context.get("filename", ""))
        content = context.get("raw_content", "")
        
        database_info = {
            "module": module_name,
            "classes": [],
            "functions": [],
            "context_type": "database_config"
        }

        for child in node.children:
            if child.type == "class_definition":
                class_info = self._extract_class_definition(child, content)
                if class_info:
                    database_info["classes"].append(class_info)
            elif child.type == "function_definition":
                func_name = self._find_child_of_type(child, "identifier")
                if func_name:
                    database_info["functions"].append({
                        "name": content[func_name.start_byte:func_name.end_byte],
                        "parameters": self._extract_function_params(content, child),
                        "docstring": self._find_function_docstring(content, child)
                    })
        return database_info

    def _extract_middleware_config(self, node, context: dict) -> Dict[str, Any]:
        """Extract middleware configuration context."""
        module_name = self.get_module_name(context.get("filename", ""))
        content = context.get("raw_content", "")
        
        middleware_info = {
            "module": module_name,
            "classes": [],
            "functions": [],
            "context_type": "middleware_config"
        }

        for child in node.children:
            if child.type == "class_definition":
                class_info = self._extract_class_definition(child, content)
                if class_info:
                    middleware_info["classes"].append(class_info)
            elif child.type == "function_definition":
                func_name = self._find_child_of_type(child, "identifier")
                if func_name:
                    middleware_info["functions"].append({
                        "name": content[func_name.start_byte:func_name.end_byte],
                        "parameters": self._extract_function_params(content, child),
                        "docstring": self._find_function_docstring(content, child)
                    })
        return middleware_info

    # =========================================================================
    # Model Context with Type Hint Relationship Inference
    # =========================================================================

    def _extract_validator_info(self, content: str, node) -> List[Dict[str, Any]]:
        """Extract validator information from class methods."""
        validators = []
        for child in node.children:
            if child.type == "function_definition":
                decorators = []
                curr_node = child
                while curr_node.prev_sibling and curr_node.prev_sibling.type == "decorator":
                    dec_text = self._extract_node_text(content, curr_node.prev_sibling)
                    if "validator" in dec_text:
                        decorators.append(dec_text)
                    curr_node = curr_node.prev_sibling
                
                if decorators:
                    validator_info = {
                        "name": self._extract_node_text(content, self._find_child_of_type(child, "identifier")),
                        "decorators": decorators,
                        "docstring": self._find_function_docstring(content, child)
                    }
                    validators.append(validator_info)
        return validators

    def _extract_model_fields(self, content: str, node) -> Dict[str, Dict[str, Any]]:
        """
        Extract field definitions with complete metadata and relationships.
        KEY FEATURE: Detects relationships from type hints:
        - List[X] or Sequence[X] -> one_to_many
        - Optional[X] -> nullable
        - Dict[K,V] -> embedded
        - PyObjectId -> reference
        """
        fields = {}
        
        class_body = self._find_child_of_type(node, "block")
        if not class_body:
            return fields
        
        for child in class_body.children:
            if child.type == "expression_statement":
                # Handle annotated assignment: field_name: Type = value
                assignment = self._find_child_of_type(child, "assignment")
                if assignment:
                    left = assignment.children[0] if assignment.children else None
                    if left and left.type == "identifier":
                        field_name = self._extract_node_text(content, left)
                        field_info = self._parse_field_annotation(content, assignment)
                        if field_info:
                            fields[field_name] = field_info
        
        return fields

    def _parse_field_annotation(self, content: str, assignment_node) -> Optional[Dict[str, Any]]:
        """Parse a field assignment with type annotation."""
        field_info = {
            "type": None,
            "description": None,
            "default": None,
            "constraints": [],
            "relationship": None
        }
        
        # Look for type annotation
        for child in assignment_node.children:
            if child.type == "type":
                type_text = self._extract_node_text(content, child)
                field_info["type"] = type_text.strip()
                
                # Dynamic relationship detection based on type patterns
                if "List[" in type_text or "Sequence[" in type_text:
                    inner_match = re.search(r'List\[(.*?)\]|Sequence\[(.*?)\]', type_text)
                    if inner_match:
                        inner_type = (inner_match.group(1) or inner_match.group(2)).strip()
                        if inner_type and inner_type[0].isupper():
                            field_info["relationship"] = "one_to_many"
                            if "PyObjectId" in inner_type or "ObjectId" in inner_type:
                                field_info["relationship"] = "one_to_many_ref"
                                
                elif "Optional[" in type_text:
                    inner_match = re.search(r'Optional\[(.*?)\]', type_text)
                    if inner_match:
                        inner_type = inner_match.group(1).strip()
                        field_info["type"] = inner_type
                        if field_info["default"] is None:
                            field_info["default"] = "None"
                        if inner_type[0].isupper():
                            field_info["relationship"] = "one_to_one"
                            if "PyObjectId" in inner_type or "ObjectId" in inner_type:
                                field_info["relationship"] = "one_to_one_ref"
                                
                elif type_text and type_text[0].isupper():
                    if "PyObjectId" in type_text or "ObjectId" in type_text:
                        field_info["relationship"] = "one_to_one_ref"
                    elif "Dict[" in type_text or "Mapping[" in type_text:
                        field_info["relationship"] = "embedded"
                    else:
                        # Check if references another model
                        field_info["relationship"] = "one_to_one"

        # Look for Field() configuration
        full_text = self._extract_node_text(content, assignment_node)
        if "Field(" in full_text:
            field_params = re.findall(r'(\w+)\s*=\s*([^,\)]+)', full_text)
            for param_name, param_value in field_params:
                param_value = param_value.strip()
                if param_name == "default":
                    field_info["default"] = param_value
                elif param_name == "description":
                    field_info["description"] = param_value.strip('"\'')
                elif param_name == "pattern":
                    field_info["constraints"].append(f"pattern={param_value}")
                elif param_name not in ["alias"]:
                    field_info["constraints"].append(f"{param_name}={param_value}")

        return field_info if field_info["type"] else None

    def _extract_model_context(self, node, context: dict) -> dict:
        """Extract model context with dynamic field detection and relationships."""
        module_name = self.get_module_name(context.get("filename", ""))
        content = context.get("raw_content", "")
        
        model_info = {
            "module": module_name,
            "classes": [],
            "functions": [],
            "context_type": "model_context"
        }

        for child in node.children:
            if child.type == "class_definition":
                class_info = {}
                
                identifier = self._find_child_of_type(child, "identifier")
                if identifier:
                    class_name = self._extract_node_text(content, identifier)
                    class_info["name"] = class_name
                    class_info["docstring"] = self._find_class_docstring(content, child)
                    
                    # Get base classes
                    bases = []
                    base_node = self._find_child_of_type(child, "argument_list")
                    if base_node:
                        bases_text = self._extract_node_text(content, base_node)
                        bases = [b.strip() for b in bases_text.split(",") if b.strip()]
                    class_info["bases"] = bases
                
                # Extract fields with metadata
                class_info["fields"] = self._extract_model_fields(content, child)
                
                # Extract validators
                validators = self._extract_validator_info(content, child)
                if validators:
                    class_info["validators"] = validators
                
                # Extract nested Config class
                for sub_node in child.children:
                    if sub_node.type == "class_definition":
                        sub_name = self._extract_node_text(content, self._find_child_of_type(sub_node, "identifier"))
                        if sub_name == "Config":
                            class_info["config"] = {
                                "attributes": self._extract_class_attributes(content, sub_node)
                            }
                
                model_info["classes"].append(class_info)

        return model_info

    # =========================================================================
    # Service Context
    # =========================================================================

    def _extract_service_context(self, node, context: dict) -> Dict[str, Any]:
        """Extract service layer context."""
        module_name = self.get_module_name(context.get("filename", ""))
        content = context.get("raw_content", "")
        
        service_info = {
            "module": module_name,
            "classes": [],
            "functions": [],
            "context_type": "service_context"
        }

        for child in node.children:
            if child.type == "class_definition":
                class_info = self._extract_class_definition(child, content)
                if class_info:
                    class_info["docstring"] = self._find_class_docstring(content, child)
                    class_info["attributes"] = self._extract_class_attributes(content, child)
                    class_info["methods"] = self._extract_class_methods(content, child)
                    service_info["classes"].append(class_info)
            elif child.type == "function_definition":
                func_name = self._find_child_of_type(child, "identifier")
                if func_name:
                    func_info = {
                        "name": self._extract_node_text(content, func_name),
                        "parameters": self._extract_function_params(content, child),
                        "docstring": self._find_function_docstring(content, child)
                    }
                    service_info["functions"].append(func_info)
        return service_info

    # =========================================================================
    # Controller Context
    # =========================================================================

    def _extract_controller_context(self, node, context: dict) -> Dict[str, Any]:
        """Extract controller/API endpoint context."""
        module_name = self.get_module_name(context.get("filename", ""))
        content = context.get("raw_content", "")
        
        controller_info = {
            "module": module_name,
            "router": None,
            "endpoints": [],
            "context_type": "controller_context"
        }

        # First pass - find router assignment
        for child in node.children:
            if child.type == "assignment":
                identifier = self._find_child_of_type(child, "identifier")
                if identifier and self._extract_node_text(content, identifier) == "router":
                    controller_info["router"] = "router"
                    break

        # Second pass - find decorated endpoints
        for child in node.children:
            if child.type == "decorated_definition":
                decorator = self._find_child_of_type(child, "decorator")
                function = self._find_child_of_type(child, "function_definition")
                
                if decorator and function:
                    decorator_text = self._extract_node_text(content, decorator)
                    
                    # Extract HTTP method
                    http_method = None
                    if "router.post" in decorator_text: http_method = "POST"
                    elif "router.get" in decorator_text: http_method = "GET"
                    elif "router.put" in decorator_text: http_method = "PUT"
                    elif "router.delete" in decorator_text: http_method = "DELETE"
                    elif "router.patch" in decorator_text: http_method = "PATCH"
                    
                    # Extract route path
                    route_match = re.search(r'["\'](.*?)["\']', decorator_text)
                    route_path = route_match.group(1) if route_match else None
                    
                    func_name = self._find_child_of_type(function, "identifier")
                    if func_name:
                        handler_name = self._extract_node_text(content, func_name)
                        docstring = self._find_function_docstring(content, function)
                        
                        controller_info["endpoints"].append({
                            "http_method": http_method,
                            "route": route_path,
                            "handler": handler_name,
                            "docstring": docstring,
                            "parameters": self._extract_function_params(content, function)
                        })

        return controller_info

    # =========================================================================
    # Route Context
    # =========================================================================

    def _extract_route_context(self, node, context: dict) -> Dict[str, Any]:
        """Extract route/router configuration context."""
        module_name = self.get_module_name(context.get("filename", ""))
        content = context.get("raw_content", "")
        
        route_context = {
            "module": module_name,
            "router": None,
            "includes": [],
            "context_type": "route_context"
        }

        # Build import map
        import_map = {}
        for child in node.children:
            if child.type in ("import_statement", "import_from_statement"):
                text = self._extract_node_text(content, child)
                m = re.search(r'from\s+([\w./]+)\s+import\s+(\w+)(?:\s+as\s+(\w+))?', text)
                if m:
                    mod_path = m.group(1)
                    alias = m.group(3) if m.group(3) else m.group(2)
                    import_map[alias] = mod_path

        # Find router definitions and includes
        for child in node.children:
            if child.type == "assignment":
                identifier = self._find_child_of_type(child, "identifier")
                if identifier:
                    name = self._extract_node_text(content, identifier)
                    right_text = self._extract_node_text(content, child)
                    if "APIRouter" in right_text:
                        route_context["router"] = name
            
            elif child.type == "expression_statement":
                stmt_text = self._extract_node_text(content, child)
                if "include_router" in stmt_text:
                    controller_match = re.search(r'(\w+)_controller\.router', stmt_text)
                    prefix_match = re.search(r'prefix="([^"]+)"', stmt_text)
                    if controller_match:
                        controller_name = controller_match.group(1)
                        prefix = prefix_match.group(1) if prefix_match else ""
                        route_context["includes"].append({
                            "alias": f"{controller_name}_controller",
                            "module": f"backend.controllers.{controller_name}_controller",
                            "prefix": prefix
                        })

        return route_context

    # =========================================================================
    # Frontend Contexts
    # =========================================================================

    def _extract_frontend_config(self, node, context: dict) -> Dict[str, Any]:
        """Extract frontend configuration context."""
        module_name = self.get_module_name(context.get("filename", ""))
        content = context.get("raw_content", "")

        config_context = {
            "module": module_name,
            "exports": [],
            "config_values": [],
            "class_configs": {},
            "context_type": "frontend_config_context"
        }

        # Regex for process.env values in JS/TS
        env_var_pattern = re.compile(
            r'(?P<var_name>\w+)\s*:\s*process\.env\.(?P<env_var>\w+)\s*\|\|\s*[\'"](?P<default>[^\'"]+)[\'"]'
        )
        for match in env_var_pattern.finditer(content):
            env_var = match.group("env_var")
            config_context["config_values"].append(env_var)

        for child in node.children:
            if child.type == "class_definition":
                class_info = self._extract_class_definition(child, content)
                if class_info:
                    config_context["class_configs"][class_info["name"]] = class_info

        return config_context

    def _extract_utilities_api_context(self, node, context: dict) -> Dict[str, Any]:
        """Extract API utilities context."""
        module_name = self.get_module_name(context.get("filename", ""))
        content = context.get("raw_content", "")
        
        utilities_api = {
            "module": module_name,
            "functions": [],
            "constants": [],
            "classes": [],
            "context_type": "utilities_api_context"
        }

        for child in node.children:
            if child.type == "class_definition":
                class_info = self._extract_class_definition(child, content)
                if class_info:
                    utilities_api["classes"].append(class_info)
            elif child.type == "function_definition":
                func_name = self._find_child_of_type(child, "identifier")
                if func_name:
                    utilities_api["functions"].append({
                        "name": self._extract_node_text(content, func_name),
                        "parameters": self._extract_function_params(content, child),
                        "docstring": self._find_function_docstring(content, child)
                    })

        return utilities_api

    def _extract_utilities_styles_context(self, node, context: dict) -> Dict[str, Any]:
        """Extract styles utilities context."""
        module_name = self.get_module_name(context.get("filename", ""))
        content = context.get("raw_content", "")
        
        utilities_styles = {
            "module": module_name,
            "styles": [],
            "theme": {},
            "context_type": "utilities_styles_context"
        }

        # CSS variables
        var_pattern = re.compile(r'--[\w-]+:\s*[^;]+;')
        for match in var_pattern.finditer(content):
            utilities_styles["styles"].append(match.group())

        return utilities_styles

    # =========================================================================
    # Component & Page Contexts
    # =========================================================================

    def _extract_component_definitions(self, node, context: dict) -> Dict[str, Any]:
        """Extract React component definitions."""
        module_path = self.get_module_name(context.get("filename", ""))
        content = context.get("raw_content", "")
        
        component_info = {
            "components": [],
            "context_type": "component_context",
            "module": module_path
        }

        for child in node.children:
            if child.type in ("function_declaration", "class_declaration"):
                comp_info = self._extract_component_info(child, content)
                if comp_info:
                    comp_info["docstring"] = self._find_function_docstring(content, child)
                    component_info["components"].append(comp_info)
            
            elif child.type == "variable_declaration":
                for declarator in child.children:
                    if declarator.type == "variable_declarator":
                        comp_info = self._extract_arrow_component_info(declarator, content)
                        if comp_info:
                            component_info["components"].append(comp_info)

        return component_info

    def _extract_component_info(self, node, content: str) -> Optional[Dict[str, Any]]:
        """Extract component info from function/class declarations."""
        comp_name_node = self._find_child_of_type(node, "identifier")
        if comp_name_node:
            comp_name = self._extract_node_text(content, comp_name_node).strip()
            if comp_name and comp_name[0].isupper():
                return {
                    "name": comp_name,
                    "props": self._extract_component_props(node, content),
                    "type": "class" if node.type == "class_declaration" else "function"
                }
        return None

    def _extract_arrow_component_info(self, node, content: str) -> Optional[Dict[str, Any]]:
        """Extract component info from arrow function declarations."""
        name_node = self._find_child_of_type(node, "identifier")
        arrow_func = self._find_child_of_type(node, "arrow_function")
        if name_node and arrow_func:
            comp_name = self._extract_node_text(content, name_node).strip()
            if comp_name and comp_name[0].isupper():
                return {
                    "name": comp_name,
                    "props": self._extract_component_props(arrow_func, content),
                    "type": "arrow_function"
                }
        return None

    def _extract_component_props(self, node, content: str) -> List[str]:
        """Extract component props."""
        props = []
        params_node = self._find_child_of_type(node, "formal_parameters")
        if params_node:
            object_pattern = self._find_child_of_type(params_node, "object_pattern")
            if object_pattern:
                for child in object_pattern.children:
                    if child.type == "identifier":
                        prop_name = self._extract_node_text(content, child).strip()
                        if prop_name:
                            props.append(prop_name)
            else:
                params_text = self._extract_node_text(content, params_node)
                if params_text:
                    props = [param.strip() for param in params_text.strip("()").split(",") if param.strip()]
        return props

    def _extract_page_structure(self, node, context: dict) -> Dict[str, Any]:
        """Extract page structure context."""
        content = context.get("raw_content", "")
        
        page_info = {
            "pages": [],
            "layouts": [],
            "context_type": "pages_context",
            "module": self.get_module_name(context.get("filename", ""))
        }

        for child in node.children:
            if child.type in ("function_declaration", "class_declaration"):
                page_info_item = self._extract_page_info(child, content)
                if page_info_item:
                    docstring = self._find_function_docstring(content, child)
                    if docstring:
                        page_info_item["docstring"] = docstring
                        
                        layout_match = re.search(r'Layout:\s*(.*?)(?:\n|$)', docstring)
                        if layout_match:
                            page_info_item["layout"] = layout_match.group(1).strip()
                            if page_info_item["name"].endswith("Layout"):
                                page_info_item["type"] = "layout"

                        components_match = re.search(r'Components:\s*((?:- .*\n?)+)', docstring)
                        if components_match:
                            comp_text = components_match.group(1).strip()
                            components = [
                                line.lstrip("- ").split(":")[0].strip()
                                for line in comp_text.splitlines()
                                if line.strip()
                            ]
                            page_info_item["components"] = components

                    if page_info_item.get("type") == "layout":
                        page_info["layouts"].append(page_info_item)
                    else:
                        page_info["pages"].append(page_info_item)

        return page_info

    def _extract_page_info(self, node, content: str) -> Optional[Dict[str, Any]]:
        """Extract page information."""
        page_name_node = self._find_child_of_type(node, "identifier")
        if page_name_node:
            page_name = self._extract_node_text(content, page_name_node).strip()
            if page_name and page_name[0].isupper():
                return {
                    "name": page_name,
                    "type": "page",
                    "components": [],
                    "layout": ""
                }
        return None


# =========================================================================
# Public API
# =========================================================================

def extract_codebase(
    file_paths: List[str],
    content_map: Dict[str, str],
    source_agent: str = "Unknown",
    ignore_patterns: Optional[List[str]] = None
) -> Dict[str, Dict[str, Any]]:
    """
    Extract structured context from multiple files.
    
    Args:
        file_paths: List of file paths to index
        content_map: Map of file_path -> content
        source_agent: Agent that produced these files (determines context_type)
        ignore_patterns: Glob patterns to exclude
    
    Returns:
        Map of file_path -> extracted context with context_type
    """
    chunker = TreeSitterChunker()
    results = {}
    
    ignore_patterns = ignore_patterns or []
    
    for file_path in file_paths:
        from fnmatch import fnmatch
        if any(fnmatch(file_path, pattern) for pattern in ignore_patterns):
            continue
        
        content = content_map.get(file_path, "")
        if not content:
            continue
        
        try:
            ext = os.path.splitext(file_path)[-1].lower()
            contexts = chunker.chunk_file(content, ext, source_agent, file_path)
            if contexts:
                results[file_path] = contexts[0]  # chunk_file returns list
        except Exception as e:
            logger.error(f"Failed to extract {file_path}: {e}")
            continue
    
    return results


# Expose the main class for direct usage
__all__ = ["TreeSitterChunker", "extract_codebase", "TREE_SITTER_AVAILABLE"]
