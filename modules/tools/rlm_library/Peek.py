# Peek.py - View a slice of text by character position
# Supports two modes:
#   RLM mode:    var_name omitted → reads from repl_state["prompt_var"]
#   Hybrid mode: var_name provided → reads from repl_state["variables"][var_name]

args = args.get("args", {})
repl_state = args.get("_repl_state", {})

var_name = args.get("var_name", None)
source = None

if var_name is not None:
    variables = repl_state.get("variables", {})
    if var_name in variables:
        source = str(variables[var_name])
    else:
        result = {"error": f"Variable '{var_name}' not found", "available": list(variables.keys())}
else:
    source = repl_state.get("prompt_var", "")

if source is not None:
    start = args.get("start", 0)
    end = args.get("end", min(1000, len(source)))
    start = max(0, min(start, len(source)))
    end = max(start, min(end, len(source)))
    result = source[start:end]

