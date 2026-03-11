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


def _format_default(input_value, nodes_map: dict | None = None) -> str:
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
) -> None:
    """Main entry point: parse workflow + schema, then emit generated_client.py."""

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
        print(f"ERROR: Could not reach ComfyUI at {comfyui_url}/object_info")
        print(f"       {exc}")
        sys.exit(1)

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
        if raw_output_names:
            slots = [s.upper() if s else f"OUT_{i}" for i, s in enumerate(raw_output_names)]
        else:
            output_count = len(object_info.get(class_type, {}).get("output", []))
            slots = [f"OUT_{i}" for i in range(output_count)]

        nodes_map[node_id] = {"class_name": class_name, "title": title, "slots": slots}

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
    args: list[str] = ["        self,", "        *,"]
    assignments: list[str] = []
    doc_entries: list[tuple[str, str]] = []  # [(arg_name, description)]

    api_nodes_found = 0

    for node_id, node_data in workflow.items():
        title = node_data.get("_meta", {}).get("title", "")
        if not title.startswith("API_"):
            continue

        api_nodes_found += 1
        node_name = _sanitize_identifier(title[len("API_"):].strip().lower())
        class_type = node_data["class_type"]

        for input_name, input_value in node_data["inputs"].items():
            arg_name = f"{node_name}_{_sanitize_identifier(input_name)}"

            origin = f"node {node_id} ({title}) › {input_name}"
            if arg_name in seen_arg_names:
                orig_origin, orig_node_id = seen_arg_names.pop(arg_name)
                orig_new_name = f"{arg_name}_{orig_node_id}"
                new_arg_name = f"{arg_name}_{node_id}"

                # Retroactively rename the first occurrence in args and assignments.
                for i, line in enumerate(args):
                    if f"        {arg_name}:" in line:
                        args[i] = line.replace(f"        {arg_name}:", f"        {orig_new_name}:", 1)
                        break
                for i, line in enumerate(assignments):
                    if f"] = {arg_name}" in line:
                        assignments[i] = line.replace(f"] = {arg_name}", f"] = {orig_new_name}", 1)
                        break

                seen_arg_names[orig_new_name] = (orig_origin, orig_node_id)
                # Retroactively rename the doc entry for the first occurrence.
                for i, (dname, ddoc) in enumerate(doc_entries):
                    if dname == arg_name:
                        doc_entries[i] = (orig_new_name, ddoc)
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
            args.append(f"        {arg_name}: {py_type}{_format_default(input_value, nodes_map)},")
            assignments.append(
                f"        workflow['{node_id}']['inputs']['{input_name}'] = {arg_name}"
            )

    if api_nodes_found == 0:
        print(
            "WARNING: No nodes with _meta.title starting with 'API_' found "
            f"in {workflow_path}. The generated class will have no parameters."
        )

    # ------------------------------------------------------------------
    # 4. Build the generated source code
    # ------------------------------------------------------------------
    code_lines: list[str] = [
        '"""',
        "Auto-generated ComfyUI workflow facade.",
        "",
        "DO NOT EDIT — this file is regenerated by generate_api.py.",
        '"""',
        "",
        "import copy",
        "import json",
        "import functools",
        "from typing import Any, Dict, Literal, NewType, Tuple",
        "",
        "# --- Auto-Generated Strict Types ---",
        "ComfyConnection = Tuple[str, int]",
    ]

    for custom_type in sorted(discovered_custom_types):
        code_lines.append(
            f"{custom_type} = NewType('{custom_type}', ComfyConnection)"
        )

    if discovered_enums:
        code_lines.append("")
        code_lines.append("# --- Auto-Generated Enum Types (dropdown choices) ---")
        for alias, choices in sorted(discovered_enums.items()):
            joined = ", ".join(repr(c) for c in choices)
            code_lines.append(f"{alias} = Literal[{joined}]")

    # Nodes namespace — one inner class per workflow node, slots as tuple constants
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

    code_lines.extend(
        [
            "",
            "",
            "# --- Runtime Validation ---",
            "def validate_connections(func):",
            '    """Decorator that validates all ComfyConnection tuples at runtime."""',
            "    @functools.wraps(func)",
            "    def wrapper(self, *args, **kwargs):",
            "        for key, val in kwargs.items():",
            "            if isinstance(val, (tuple, list)):",
            "                if (",
            "                    len(val) != 2",
            "                    or not isinstance(val[0], str)",
            "                    or not isinstance(val[1], int)",
            "                ):",
            "                    raise ValueError(",
            '                        f"Argument \'{key}\' expected '
            "(NodeID: str, OutputIndex: int), got {val}\"",
            "                    )",
            "        return func(self, *args, **kwargs)",
            "    return wrapper",
            "",
            "",
            "class ComfyWorkflowAPI:",
            '    """Type-safe facade over the ComfyUI workflow graph."""',
            "",
            f"    def __init__(self, template_path: str = '{workflow_path}') -> None:",
            "        with open(template_path, 'r', encoding='utf-8') as f:",
            "            self.base_workflow = json.load(f)",
            "",
            "    @validate_connections",
            "    def build_payload(",
        ]
    )

    code_lines.extend(args)
    code_lines.append("    ) -> Dict[str, Any]:")
    # Build multi-line docstring with Args section.
    code_lines.append('        """Build a ready-to-submit ComfyUI prompt payload.')
    if doc_entries:
        code_lines.append("")
        code_lines.append("        Args:")
        for arg_name, description in doc_entries:
            if description:
                code_lines.append(f"            {arg_name}: {description}.")
            else:
                code_lines.append(f"            {arg_name}:")
    code_lines.append('        """')
    code_lines.append("        workflow = copy.deepcopy(self.base_workflow)")
    code_lines.extend(assignments)
    code_lines.append("        return {'prompt': workflow}")
    code_lines.append("")  # trailing newline

    # ------------------------------------------------------------------
    # 5. Write to disk
    # ------------------------------------------------------------------
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(code_lines))

    print(f"Generated {output_path}  ({api_nodes_found} API node(s), "
          f"{len(discovered_custom_types)} custom type(s), "
          f"{len(discovered_enums)} enum type(s))")


if __name__ == "__main__":
    run_dynamic_generator()
