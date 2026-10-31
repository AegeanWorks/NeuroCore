# GetVariable.py - Retrieve stored results from REPL state
# Used in RLM (Recursive Language Model) for accessing intermediate results

args = args.get("args", {})
repl_state = args.get("_repl_state", {})

name = args.get("name", "")

if not name:
    result = {"error": "No variable name provided"}
else:
    variables = repl_state.get("variables", {})
    if name in variables:
        value = variables[name]
        # Return value with metadata
        value_str = str(value)
        result = {
            "name": name,
            "value": value,
            "value_type": type(value).__name__,
            "length": len(value_str) if isinstance(value, (str, list, dict)) else "N/A",
            "all_keys": list(variables.keys())
        }
    else:
        result = {
            "error": f"Variable '{name}' not found",
            "available_keys": list(variables.keys())
        }

