"""
ComfyUI Dynamic Type-Safe API Generator

Parses a ComfyUI workflow_api.json file and cross-references it against
the live ComfyUI /object_info schema to generate a strongly-typed Python
Facade class (generated_client.py).

Nodes whose _meta.title starts with "API_" are exposed as typed parameters
on the generated build_payload() method.
"""

import json
import re
import sys

import requests


def _format_type_name(comfy_type: str) -> str:
    """Convert a ComfyUI type string like 'MOTION_LATENT' into 'MotionLatent'."""
    return comfy_type.title().replace("_", "")


def _sanitize_identifier(name: str) -> str:
    """Ensure a string is a valid Python identifier."""
    name = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    if name and name[0].isdigit():
        name = "_" + name
    return name


def _enum_alias_name(arg_name: str) -> str:
    """Convert an arg name like 'sampler_sampler_name' into 'SamplerSamplerName'."""
    return "".join(word.capitalize() for word in arg_name.split("_"))


def _format_default(
    input_value, nodes_map: dict | None = None, context: str = ""
) -> str:
    """
    Return a ' = <value>' default snippet from the workflow's current value.

    For graph connections, emits a Nodes.ClassName.SLOT reference when
    nodes_map is provided, falling back to a plain tuple.
    """
    if isinstance(input_value, list) and len(input_value) == 2:
        ref_id, output_index = str(input_value[0]), int(input_value[1])
        if nodes_map and ref_id in nodes_map:
            node_info = nodes_map[ref_id]
            slots = node_info["slots"]
            if output_index < len(slots):
                return f" = Nodes.{node_info['class_name']}.{slots[output_index]}"
        return f" = ({repr(ref_id)}, {output_index})"
    if isinstance(input_value, bool):
        return f" = {input_value}"
    if isinstance(input_value, (int, float)):
        return f" = {input_value}"
    if isinstance(input_value, str):
        return f" = {repr(input_value)}"
    where = f" [{context}]" if context else ""
    print(
        f"WARNING{where}: unrecognised default value {input_value!r} "
        f"(type: {type(input_value).__name__}) — parameter will be mandatory."
    )
    return ""


def _get_input_doc(class_type: str, input_name: str, object_info: dict) -> str:
    """Extract tooltip and numeric constraints from object_info for a given input."""
    node_schema = object_info.get(class_type, {}).get("input", {})
    for section in ("required", "optional"):
        section_data = node_schema.get(section, {})
        if input_name in section_data:
            spec = section_data[input_name]
            meta = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            parts = []
            if meta.get("tooltip"):
                parts.append(meta["tooltip"].rstrip("."))
            constraints = []
            if "min" in meta:
                constraints.append(f"min={meta['min']}")
            if "max" in meta:
                constraints.append(f"max={meta['max']}")
            if constraints:
                parts.append(f"[{', '.join(constraints)}]")
            return " ".join(parts)
    return ""


def _get_input_tooltip(class_type: str, input_name: str, object_info: dict) -> str:
    """Extract just the tooltip text for an input (no constraint annotation)."""
    node_schema = object_info.get(class_type, {}).get("input", {})
    for section in ("required", "optional"):
        section_data = node_schema.get(section, {})
        if input_name in section_data:
            spec = section_data[input_name]
            meta = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            return meta.get("tooltip", "").rstrip(".")
    return ""


def _get_input_constraints(
    class_type: str, input_name: str, object_info: dict
) -> tuple:
    """Return (min_val, max_val) for a numeric input, or (None, None)."""
    node_schema = object_info.get(class_type, {}).get("input", {})
    for section in ("required", "optional"):
        section_data = node_schema.get(section, {})
        if input_name in section_data:
            spec = section_data[input_name]
            meta = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            return meta.get("min"), meta.get("max")
    return None, None


def _resolve_type(
    class_type: str,
    input_name: str,
    arg_name: str,
    object_info: dict,
    primitives: dict,
    discovered_custom_types: set,
    discovered_enums: dict,
) -> str:
    """
    Resolve the Python type for a given node input by consulting object_info.

    Rules:
      1. If the schema type is a list (dropdown/enum) -> Literal[...] alias
      2. If it matches a known primitive (INT, FLOAT, STRING, BOOLEAN) -> builtin
      3. Otherwise -> auto-discover a NewType wrapping ComfyConnection
    """
    node_schema = object_info.get(class_type, {}).get("input", {})

    comfy_type = None
    for section in ("required", "optional"):
        section_data = node_schema.get(section, {})
        if input_name in section_data:
            comfy_type = section_data[input_name][0]
            break

    if comfy_type is None:
        # Fallback: treat as generic str when not found in schema.
        return "str"

    if isinstance(comfy_type, list):
        # Build a Literal alias from the dropdown choices (strings only).
        choices = [v for v in comfy_type if isinstance(v, str)]
        if not choices:
            return "str"
        alias = _enum_alias_name(arg_name)
        # If two inputs share the same alias but have different choices,
        # keep the first definition (they are typically the same node type).
        if alias not in discovered_enums:
            discovered_enums[alias] = choices
        return alias

    if comfy_type in primitives:
        return primitives[comfy_type]

    # Dynamic graph connection type
    py_type = _format_type_name(comfy_type)
    discovered_custom_types.add(py_type)
    return py_type


def _title_to_class_name(title: str, node_id: str, seen: dict, nodes_map: dict) -> str:
    """
    Convert a node _meta.title to a unique PascalCase inner class name.

    Strips 'API_' prefix if present, title-cases each word, removes
    non-identifier characters. On collision, BOTH the original entry and the
    new one are postfixed with their respective node IDs, so no entry ever
    holds an ambiguous bare name.
    """
    name = title
    if name.upper().startswith("API_"):
        name = name[4:].strip()
    name = re.sub(r"[^0-9a-zA-Z]+", " ", name).strip()
    name = "".join(word.capitalize() for word in name.split())
    if not name:
        name = f"Node{node_id}"
    if name[0].isdigit():
        name = "N" + name

    if name in seen:
        original_node_id = seen[name]
        # Retroactively rename the first entry that already has the bare name.
        original_new_name = f"{name}_{original_node_id}"
        # Update nodes_map for the original node.
        if original_node_id in nodes_map:
            nodes_map[original_node_id]["class_name"] = original_new_name
        # Re-key seen: the bare name now maps to nothing; register the postfixed original.
        del seen[name]
        seen[original_new_name] = original_node_id

        new_name = f"{name}_{node_id}"
        print(
            f"WARNING: Duplicate Nodes class name '{name}' "
            f"(nodes {original_node_id} and {node_id}); "
            f"renamed to '{original_new_name}' and '{new_name}'."
        )
        name = new_name

    seen[name] = node_id
    return name


def run_dynamic_generator(
    workflow_path: str = "workflow_api.json",
    output_path: str = "generated_client.py",
    comfyui_url: str = "http://127.0.0.1:8188",
    *,
    emit_validate_enums: bool = True,
    emit_validate_ranges: bool = True,
    emit_validate_connections: bool = True,
    emit_schema: bool = True,
    emit_ranges: bool = True,
    emit_output_types: bool = True,
    emit_docstrings: bool = True,
    emit_nodes: bool = True,
) -> None:
    """Main entry point: parse workflow + schema, then emit generated_client.py.

    Feature flags (all default True — set to False to omit that feature):
        emit_validate_enums:       include the validate_enums decorator
        emit_validate_ranges:      include the validate_ranges decorator
        emit_validate_connections: include the validate_connections decorator
        emit_schema:               include the _SCHEMA dict and schema() classmethod
        emit_ranges:               include the _RANGES dict
        emit_output_types:         include the _OUTPUT_TYPES dict
        emit_docstrings:           include the Args: docstring block in build_payload
        emit_nodes:                include the class Nodes: namespace

    Conflict rules (hard errors):
        emit_validate_enums=True  requires emit_schema=True
        emit_validate_connections=True requires emit_schema=True and emit_output_types=True
        emit_validate_ranges=True  requires emit_ranges=True
    """
    # ------------------------------------------------------------------
    # 0. Validate flag combinations
    # ------------------------------------------------------------------
    if emit_validate_enums and not emit_schema:
        raise ValueError(
            "--no-schema cannot be used when enum validation is enabled. "
            "validate_enums reads from _SCHEMA at runtime. "
            "Add --no-validate-enums or remove --no-schema."
        )
    if emit_validate_connections and not emit_schema:
        raise ValueError(
            "--no-schema cannot be used when connection validation is enabled. "
            "validate_connections reads from _SCHEMA at runtime. "
            "Add --no-validate-connections or remove --no-schema."
        )
    if emit_validate_connections and not emit_output_types:
        raise ValueError(
            "--no-output-types cannot be used when connection validation is enabled. "
            "validate_connections reads from _OUTPUT_TYPES at runtime. "
            "Add --no-validate-connections or remove --no-output-types."
        )
    if emit_validate_ranges and not emit_ranges:
        raise ValueError(
            "--no-ranges cannot be used when range validation is enabled. "
            "validate_ranges reads from _RANGES at runtime. "
            "Add --no-validate-ranges or remove --no-ranges."
        )

    # ------------------------------------------------------------------
    # 1. Load the graph template
    # ------------------------------------------------------------------
    with open(workflow_path, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    # ------------------------------------------------------------------
    # 2. Fetch master schema from the running ComfyUI instance
    # ------------------------------------------------------------------
    try:
        resp = requests.get(f"{comfyui_url}/object_info", timeout=30)
        resp.raise_for_status()
        object_info = resp.json()
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Could not reach ComfyUI at {comfyui_url}/object_info: {exc}"
        ) from exc

    # ------------------------------------------------------------------
    # 3. Build nodes_map: all workflow nodes → class names + output slots
    # ------------------------------------------------------------------
    seen_class_names: dict[str, str] = {}  # class_name -> node_id
    nodes_map: dict[str, dict] = {}

    for node_id, node_data in workflow.items():
        title = node_data.get("_meta", {}).get("title", "")
        class_type = node_data["class_type"]
        class_name = _title_to_class_name(title, node_id, seen_class_names, nodes_map)

        raw_output_names = object_info.get(class_type, {}).get("output_name", [])
        raw_output_types = object_info.get(class_type, {}).get("output", [])
        if raw_output_names:
            slots = [
                s.upper() if s else f"OUT_{i}" for i, s in enumerate(raw_output_names)
            ]
        else:
            output_count = len(raw_output_types)
            slots = [f"OUT_{i}" for i in range(output_count)]

        nodes_map[node_id] = {
            "class_name": class_name,
            "title": title,
            "slots": slots,
            "output_types": list(raw_output_types),
        }

    # ------------------------------------------------------------------
    # 4. Iterate over API-exposed nodes and resolve types
    # ------------------------------------------------------------------
    PRIMITIVES = {
        "INT": "int",
        "FLOAT": "float",
        "STRING": "str",
        "BOOLEAN": "bool",
    }

    discovered_custom_types: set[str] = set()
    discovered_enums: dict[str, list[str]] = {}
    seen_arg_names: dict[str, tuple] = {}  # arg_name -> (origin_str, node_id)
    arg_entries: list[dict] = []  # {"name", "type", "default"}
    assignment_entries: list[dict] = []  # {"node_id", "input_name", "arg_name"}
    doc_entries: list[tuple[str, str]] = []  # [(arg_name, description)]
    schema_entries: list[dict] = []  # full schema per arg

    api_nodes_found = 0

    for node_id, node_data in workflow.items():
        title = node_data.get("_meta", {}).get("title", "")
        if not title.startswith("API_"):
            continue

        api_nodes_found += 1
        node_name = _sanitize_identifier(title[len("API_") :].strip().lower())
        class_type = node_data["class_type"]

        for input_name, input_value in node_data["inputs"].items():
            arg_name = f"{node_name}_{_sanitize_identifier(input_name)}"

            origin = f"node {node_id} ({title}) › {input_name}"
            if arg_name in seen_arg_names:
                orig_origin, orig_node_id = seen_arg_names.pop(arg_name)
                orig_new_name = f"{arg_name}_{orig_node_id}"
                new_arg_name = f"{arg_name}_{node_id}"

                # Retroactively rename the first occurrence in arg_entries and assignment_entries.
                for entry in arg_entries:
                    if entry["name"] == arg_name:
                        entry["name"] = orig_new_name
                        break
                for entry in assignment_entries:
                    if entry["arg_name"] == arg_name:
                        entry["arg_name"] = orig_new_name
                        break

                seen_arg_names[orig_new_name] = (orig_origin, orig_node_id)
                # Retroactively rename the doc entry for the first occurrence.
                for i, (dname, ddoc) in enumerate(doc_entries):
                    if dname == arg_name:
                        doc_entries[i] = (orig_new_name, ddoc)
                        break
                # Retroactively rename the schema entry for the first occurrence.
                for i, entry in enumerate(schema_entries):
                    if entry["name"] == arg_name:
                        schema_entries[i]["name"] = orig_new_name
                        break
                print(
                    f"WARNING: Duplicate argument name '{arg_name}':\n"
                    f"         {orig_origin}\n"
                    f"         Renamed to '{orig_new_name}'.\n"
                    f"         {origin}\n"
                    f"         Renamed to '{new_arg_name}'."
                )
                arg_name = new_arg_name
            seen_arg_names[arg_name] = (origin, node_id)

            py_type = _resolve_type(
                class_type,
                input_name,
                arg_name,
                object_info,
                PRIMITIVES,
                discovered_custom_types,
                discovered_enums,
            )

            description = _get_input_doc(class_type, input_name, object_info)
            doc_entries.append((arg_name, description))
            tooltip = _get_input_tooltip(class_type, input_name, object_info)
            mn, mx = _get_input_constraints(class_type, input_name, object_info)
            default_fmt = _format_default(
                input_value,
                nodes_map if emit_nodes else None,
                context=f"node {node_id} ({title}) › {input_name}",
            )
            default_code = default_fmt[3:] if default_fmt.startswith(" = ") else ""
            # For _SCHEMA defaults on connection inputs:
            # - When emit_nodes is True, use Nodes.X.SLOT references (matches
            #   the build_payload signature and keeps the dev experience consistent).
            # - When emit_nodes is False (release mode), fall back to plain
            #   JSON-serializable lists so json.dumps(schema()) still works.
            if isinstance(input_value, list) and len(input_value) == 2:
                if emit_nodes and nodes_map:
                    # Reuse the same Nodes.Class.SLOT reference as build_payload defaults.
                    schema_default_code = default_code
                else:
                    ref_id = str(input_value[0])
                    out_idx = int(input_value[1])
                    schema_default_code = f"[{repr(ref_id)}, {out_idx}]"
            else:
                schema_default_code = default_code
            choices = discovered_enums.get(py_type)
            # Resolve raw ComfyUI type for connection provenance validation.
            _raw_comfy_type = None
            _input_sec = object_info.get(class_type, {}).get("input", {})
            for _sec in ("required", "optional"):
                if input_name in _input_sec.get(_sec, {}):
                    _ct = _input_sec[_sec][input_name][0]
                    if isinstance(_ct, str) and _ct not in PRIMITIVES:
                        _raw_comfy_type = _ct
                    break
            schema_entries.append(
                {
                    "name": arg_name,
                    "type": py_type,
                    "default_code": default_code,
                    "schema_default_code": schema_default_code,
                    "min": mn,
                    "max": mx,
                    "description": tooltip,
                    "choices": choices,
                    "comfy_type": _raw_comfy_type,
                }
            )
            arg_entries.append(
                {"name": arg_name, "type": py_type, "default": default_fmt}
            )
            assignment_entries.append(
                {"node_id": node_id, "input_name": input_name, "arg_name": arg_name}
            )

    if api_nodes_found == 0:
        print(
            "WARNING: No nodes with _meta.title starting with 'API_' found "
            f"in {workflow_path}. The generated class will have no parameters."
        )

    # ------------------------------------------------------------------
    # 4. Build the generated source code
    # ------------------------------------------------------------------
    any_validator = (
        emit_validate_enums or emit_validate_ranges or emit_validate_connections
    )

    imports = ["import json"]
    if any_validator:
        imports.append("import functools")
    typing_names = ["Any", "Dict"]
    if discovered_custom_types:
        typing_names.append("NewType")
    if discovered_enums:
        typing_names.append("Literal")
    if emit_output_types or discovered_custom_types:
        typing_names.append("Tuple")
    imports.append(f"from typing import {', '.join(sorted(typing_names))}")

    code_lines: list[str] = [
        '"""',
        "Auto-generated ComfyUI workflow facade.",
        "",
        "DO NOT EDIT — this file is regenerated by generate_api.py.",
        '"""',
        "",
        *imports,
        "",
        "# --- Auto-Generated Strict Types ---",
        "ComfyConnection = Tuple[str, int]",
    ]

    for custom_type in sorted(discovered_custom_types):
        code_lines.append(f"{custom_type} = NewType('{custom_type}', ComfyConnection)")

    if discovered_enums:
        code_lines.append("")
        code_lines.append("# --- Auto-Generated Enum Types (dropdown choices) ---")
        for alias, choices in sorted(discovered_enums.items()):
            joined = ", ".join(repr(c) for c in choices)
            code_lines.append(f"{alias} = Literal[{joined}]")

    # Nodes namespace — one inner class per workflow node, slots as tuple constants
    if emit_nodes:
        code_lines.append("")
        code_lines.append("")
        code_lines.append("# --- Workflow Node Connections ---")
        code_lines.append("class Nodes:")
        for nid, ninfo in nodes_map.items():
            code_lines.append(
                f"    class {ninfo['class_name']}:  # node {nid} · {ninfo['title']}"
            )
            if ninfo["slots"]:
                for idx, slot in enumerate(ninfo["slots"]):
                    code_lines.append(f"        {slot} = ({repr(nid)}, {idx})")
            else:
                code_lines.append("        pass")

    # Emit _RANGES: numeric constraints for runtime validation.
    if emit_ranges:
        code_lines.append("")
        code_lines.append("# --- Input Range Constraints ---")
        code_lines.append("_RANGES: Dict[str, Any] = {")
        for entry in schema_entries:
            if entry["min"] is not None or entry["max"] is not None:
                code_lines.append(
                    f"    {repr(entry['name'])}: ({entry['min']!r}, {entry['max']!r}),"
                )
        code_lines.append("}")

    # Emit _SCHEMA: full parameter descriptions.
    if emit_schema:
        code_lines.append("")
        code_lines.append("# --- Parameter Schema ---")
        code_lines.append("_SCHEMA: Dict[str, Any] = {")
        for entry in schema_entries:
            code_lines.append(f"    {repr(entry['name'])}: {{")
            code_lines.append(f"        'type': {repr(entry['type'])},")
            if entry["schema_default_code"]:
                code_lines.append(f"        'default': {entry['schema_default_code']},")
            if entry["min"] is not None:
                code_lines.append(f"        'min': {entry['min']!r},")
            if entry["max"] is not None:
                code_lines.append(f"        'max': {entry['max']!r},")
            if entry.get("choices"):
                joined = ", ".join(repr(c) for c in entry["choices"])
                code_lines.append(f"        'choices': [{joined}],")
            if entry["description"]:
                code_lines.append(
                    f"        'description': {repr(entry['description'])},"
                )
            if entry.get("comfy_type"):
                code_lines.append(f"        'comfy_type': {repr(entry['comfy_type'])},")
            code_lines.append("    },")
        code_lines.append("}")

    # Emit _OUTPUT_TYPES: maps (node_id, slot_index) → raw ComfyUI output type.
    if emit_output_types:
        code_lines.append("")
        code_lines.append(
            "# --- Node Output Types (for connection provenance validation) ---"
        )
        code_lines.append("_OUTPUT_TYPES: Dict[Tuple[str, int], str] = {")
        for nid, ninfo in nodes_map.items():
            for idx, otype in enumerate(ninfo.get("output_types", [])):
                if isinstance(otype, str):
                    code_lines.append(f"    ({repr(nid)}, {idx}): {repr(otype)},")
        code_lines.append("}")

    if any_validator:
        code_lines.append("")
        code_lines.append("")
        code_lines.append("# --- Runtime Validation ---")

    if emit_validate_connections:
        code_lines.extend(
            [
                "def validate_connections(func):",
                '    """Decorator: validates ComfyConnection shape and type provenance at runtime."""',
                "    @functools.wraps(func)",
                "    def wrapper(self, *args, **kwargs):",
                "        errors = []",
                "        for key, val in kwargs.items():",
                "            if isinstance(val, (tuple, list)):",
                "                if (",
                "                    len(val) != 2",
                "                    or not isinstance(val[0], str)",
                "                    or not isinstance(val[1], int)",
                "                ):",
                "                    errors.append(",
                "                        f\"Argument '{key}' expected "
                '(NodeID: str, OutputIndex: int), got {val}"',
                "                    )",
                "                    continue",
                "                expected_type = _SCHEMA.get(key, {}).get('comfy_type')",
                "                if expected_type:",
                "                    actual_type = _OUTPUT_TYPES.get((val[0], val[1]))",
                "                    if actual_type and actual_type != expected_type:",
                "                        errors.append(",
                "                            f\"Argument '{key}' expects a {expected_type!r} connection, \"",
                '                            f"but node {val[0]!r} output {val[1]} produces {actual_type!r}"',
                "                        )",
                "        if errors:",
                r"""            raise ValueError('\n'.join(errors))""",
                "        return func(self, *args, **kwargs)",
                "    return wrapper",
                "",
                "",
            ]
        )

    if emit_validate_ranges:
        code_lines.extend(
            [
                "def validate_ranges(func):",
                '    """Decorator that validates numeric argument ranges at runtime."""',
                "    @functools.wraps(func)",
                "    def wrapper(self, *args, **kwargs):",
                "        errors = []",
                "        for key, val in kwargs.items():",
                "            if key in _RANGES and isinstance(val, (int, float)):",
                "                lo, hi = _RANGES[key]",
                "                if (lo is not None and val < lo) or (hi is not None and val > hi):",
                "                    errors.append(",
                "                        f\"Argument '{key}' must be between {lo} and {hi}, got {val}\"",
                "                    )",
                "        if errors:",
                r"""            raise ValueError('\n'.join(errors))""",
                "        return func(self, *args, **kwargs)",
                "    return wrapper",
                "",
                "",
            ]
        )

    if emit_validate_enums:
        code_lines.extend(
            [
                "def validate_enums(func):",
                '    """Decorator that validates Literal/enum argument choices at runtime."""',
                "    @functools.wraps(func)",
                "    def wrapper(self, *args, **kwargs):",
                "        errors = []",
                "        for key, val in kwargs.items():",
                "            choices = _SCHEMA.get(key, {}).get('choices')",
                "            if choices is not None and val not in choices:",
                "                errors.append(",
                "                    f\"Argument '{key}' must be one of {choices!r}, got {val!r}\"",
                "                )",
                "        if errors:",
                r"""            raise ValueError('\n'.join(errors))""",
                "        return func(self, *args, **kwargs)",
                "    return wrapper",
                "",
                "",
            ]
        )

    param_count = len(schema_entries)

    code_lines.extend(
        [
            "class ComfyWorkflowAPI:",
            '    """Type-safe facade over the ComfyUI workflow graph."""',
            "",
            "    __slots__ = ('base_workflow', '_template_path')",
            "",
            f"    def __init__(self, template_path: str = '{workflow_path}') -> None:",
            "        with open(template_path, 'r', encoding='utf-8') as f:",
            "            self.base_workflow = json.load(f)",
            "        self._template_path = template_path",
            "",
            "    def __repr__(self) -> str:",
            f'        return f"ComfyWorkflowAPI(template={{self._template_path!r}}, params={param_count})"',
            "",
        ]
    )

    if emit_schema:
        if emit_nodes:
            # _SCHEMA contains live Nodes.X.SLOT tuples as defaults — convert
            # them to [node_id, slot_index] lists so the result is json.dumps()-safe.
            code_lines.extend(
                [
                    "    @classmethod",
                    "    def schema(cls) -> Dict[str, Any]:",
                    '        """Return a JSON-serializable description of all build_payload parameters.',
                    "",
                    "        Connection defaults (Nodes.* tuples) are converted to",
                    "        [node_id, slot_index] lists so the result is json.dumps()-safe.",
                    '        """',
                    "        out = {}",
                    "        for k, v in _SCHEMA.items():",
                    "            entry = dict(v)",
                    "            if isinstance(entry.get('default'), tuple):",
                    "                entry['default'] = list(entry['default'])",
                    "            out[k] = entry",
                    "        return out",
                    "",
                ]
            )
        else:
            # _SCHEMA only contains plain JSON-safe values — return directly.
            code_lines.extend(
                [
                    "    @classmethod",
                    "    def schema(cls) -> Dict[str, Any]:",
                    '        """Return a JSON-serializable description of all build_payload parameters."""',
                    "        return _SCHEMA",
                    "",
                ]
            )

    if emit_validate_connections:
        code_lines.append("    @validate_connections")
    if emit_validate_ranges:
        code_lines.append("    @validate_ranges")
    if emit_validate_enums:
        code_lines.append("    @validate_enums")

    code_lines.append("    def build_payload(")

    # Render arg_entries → parameter strings
    args = ["        self,", "        *,"] + [
        f"        {e['name']}: {e['type']}{e['default']}," for e in arg_entries
    ]
    code_lines.extend(args)
    code_lines.append("    ) -> Dict[str, Any]:")
    # Build multi-line docstring with Args section.
    code_lines.append('        """Build a ready-to-submit ComfyUI prompt payload.')
    if emit_docstrings and doc_entries:
        code_lines.append("")
        code_lines.append("        Args:")
        for arg_name, description in doc_entries:
            if description:
                code_lines.append(f"            {arg_name}: {description}.")
            else:
                code_lines.append(f"            {arg_name}:")
    code_lines.append('        """')
    code_lines.append("        workflow = {}")
    code_lines.append("        for _id, _node in self.base_workflow.items():")
    code_lines.append(
        "            workflow[_id] = {**_node, 'inputs': dict(_node.get('inputs', {}))}"
    )
    # Render assignment_entries → assignment strings
    assignments = [
        f"        workflow['{e['node_id']}']['inputs']['{e['input_name']}'] = {e['arg_name']}"
        for e in assignment_entries
    ]
    code_lines.extend(assignments)
    code_lines.append("        return {'prompt': workflow}")
    code_lines.append("")  # trailing newline

    # ------------------------------------------------------------------
    # 5. Write to disk
    # ------------------------------------------------------------------
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(code_lines))

    print(
        f"Generated {output_path}  ({api_nodes_found} API node(s), "
        f"{len(discovered_custom_types)} custom type(s), "
        f"{len(discovered_enums)} enum type(s))"
    )


def _build_arg_parser():
    import argparse

    p = argparse.ArgumentParser(
        prog="generate_api",
        description=(
            "Generate a strongly-typed Python facade from a ComfyUI workflow.\n\n"
            "Nodes whose _meta.title starts with 'API_' are exposed as typed\n"
            "parameters on the generated build_payload() method."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python generate_api.py\n"
            "  python generate_api.py --workflow my_workflow.json --out client.py\n"
            "  python generate_api.py --url http://192.168.1.10:8188\n"
            "  python generate_api.py --out examples/generated_client.py --examples\n"
            "  python generate_api.py --release\n"
            "  python generate_api.py --no-validate-enums --no-validate-ranges\n"
        ),
    )
    p.add_argument(
        "--workflow",
        "-w",
        default="workflow_api.json",
        metavar="PATH",
        help="Path to the workflow_api.json file  (default: workflow_api.json)",
    )
    p.add_argument(
        "--out",
        "-o",
        default="generated_client.py",
        metavar="PATH",
        help="Destination path for the generated Python file  (default: generated_client.py)",
    )
    p.add_argument(
        "--url",
        "-u",
        default="http://127.0.0.1:8188",
        metavar="URL",
        help="Base URL of the running ComfyUI instance  (default: http://127.0.0.1:8188)",
    )
    p.add_argument(
        "--examples",
        action="store_true",
        help=(
            "Also write basic_usage.py and track_progress.py into the same "
            "directory as --out"
        ),
    )

    # --- Feature disable flags ---
    feat = p.add_argument_group(
        "feature flags",
        "Disable individual features in the generated file. "
        "Use --release to disable all at once.",
    )
    feat.add_argument(
        "--release",
        action="store_true",
        help=(
            "Disable all optional features (equivalent to combining all --no-* flags). "
            "Produces the leanest possible output with no validation, no schema, "
            "no docstrings, and no Nodes namespace."
        ),
    )
    feat.add_argument(
        "--no-validate-enums",
        action="store_true",
        dest="no_validate_enums",
        help="Omit the validate_enums decorator from the generated file.",
    )
    feat.add_argument(
        "--no-validate-ranges",
        action="store_true",
        dest="no_validate_ranges",
        help="Omit the validate_ranges decorator from the generated file.",
    )
    feat.add_argument(
        "--no-validate-connections",
        action="store_true",
        dest="no_validate_connections",
        help="Omit the validate_connections decorator from the generated file.",
    )
    feat.add_argument(
        "--no-schema",
        action="store_true",
        dest="no_schema",
        help=(
            "Omit the _SCHEMA dict and schema() classmethod. "
            "Incompatible with --no-validate-enums=False or --no-validate-connections=False "
            "(i.e. those validators must also be disabled)."
        ),
    )
    feat.add_argument(
        "--no-ranges",
        action="store_true",
        dest="no_ranges",
        help=(
            "Omit the _RANGES dict. Incompatible with range validation being enabled."
        ),
    )
    feat.add_argument(
        "--no-output-types",
        action="store_true",
        dest="no_output_types",
        help=(
            "Omit the _OUTPUT_TYPES dict. "
            "Incompatible with connection validation being enabled."
        ),
    )
    feat.add_argument(
        "--no-docstrings",
        action="store_true",
        dest="no_docstrings",
        help="Omit the Args: docstring block from build_payload.",
    )
    feat.add_argument(
        "--no-nodes",
        action="store_true",
        dest="no_nodes",
        help="Omit the class Nodes: namespace from the generated file.",
    )
    return p


if __name__ == "__main__":
    import argparse

    args = _build_arg_parser().parse_args()

    # --release implies all --no-* flags
    no_validate_enums = args.no_validate_enums or args.release
    no_validate_ranges = args.no_validate_ranges or args.release
    no_validate_connections = args.no_validate_connections or args.release
    no_schema = args.no_schema or args.release
    no_ranges = args.no_ranges or args.release
    no_output_types = args.no_output_types or args.release
    no_docstrings = args.no_docstrings or args.release
    no_nodes = args.no_nodes or args.release

    try:
        run_dynamic_generator(
            workflow_path=args.workflow,
            output_path=args.out,
            comfyui_url=args.url,
            emit_validate_enums=not no_validate_enums,
            emit_validate_ranges=not no_validate_ranges,
            emit_validate_connections=not no_validate_connections,
            emit_schema=not no_schema,
            emit_ranges=not no_ranges,
            emit_output_types=not no_output_types,
            emit_docstrings=not no_docstrings,
            emit_nodes=not no_nodes,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    if args.examples:
        import os, textwrap

        out_dir = os.path.dirname(os.path.abspath(args.out))
        client_module = os.path.splitext(os.path.basename(args.out))[0]

        basic = textwrap.dedent(f"""\
            \"\"\"Basic usage example — submit a prompt and print the prompt_id.\"\"\"
            import uuid, requests
            from {client_module} import ComfyWorkflowAPI

            COMFY_URL = "{args.url}"

            api = ComfyWorkflowAPI()
            payload = api.build_payload()
            payload["client_id"] = str(uuid.uuid4())

            resp = requests.post(f"{{COMFY_URL}}/prompt", json=payload)
            resp.raise_for_status()
            print("Queued prompt:", resp.json()["prompt_id"])
            """)

        track = textwrap.dedent(f"""\
            \"\"\"Track generation progress via WebSocket.\"\"\"
            import uuid, json, websocket, requests
            from {client_module} import ComfyWorkflowAPI

            COMFY_URL = "{args.url}"
            WS_URL   = COMFY_URL.replace("http", "ws", 1)

            client_id = str(uuid.uuid4())
            api = ComfyWorkflowAPI()
            payload = api.build_payload()
            payload["client_id"] = client_id

            resp = requests.post(f"{{COMFY_URL}}/prompt", json=payload)
            resp.raise_for_status()
            prompt_id = resp.json()["prompt_id"]
            print(f"Queued: {{prompt_id}}")

            ws = websocket.WebSocket()
            ws.connect(f"{{WS_URL}}/ws?clientId={{client_id}}")
            try:
                while True:
                    raw = ws.recv()
                    if isinstance(raw, bytes):
                        continue  # skip binary preview frames
                    msg = json.loads(raw)
                    t = msg.get("type")
                    if t == "progress":
                        d = msg["data"]
                        pct = int(d["value"] / d["max"] * 100)
                        print(f"  Step {{d['value']}}/{{d['max']}}  ({{pct}}%)")
                    elif t == "execution_success":
                        print("Done!")
                        break
                    elif t == "execution_error":
                        print("Error:", msg["data"])
                        break
            finally:
                ws.close()
            """)

        for fname, content in [("basic_usage.py", basic), ("track_progress.py", track)]:
            dest = os.path.join(out_dir, fname)
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(content)
            print(f"Written {dest}")
