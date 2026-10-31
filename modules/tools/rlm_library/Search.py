# Search.py - Find regex matches in text
# Supports two modes:
#   RLM mode:    var_name omitted → searches repl_state["prompt_var"]
#   Hybrid mode: var_name provided → searches repl_state["variables"][var_name]

import re

args = args.get("args", {})
repl_state = args.get("_repl_state", {})

var_name = args.get("var_name", None)
if var_name is not None:
    variables = repl_state.get("variables", {})
    if var_name in variables:
        prompt = str(variables[var_name])
    else:
        result = {"error": f"Variable '{var_name}' not found", "available": list(variables.keys())}
        prompt = None
else:
    prompt = repl_state.get("prompt_var", "")

pattern = args.get("pattern", "")
max_results = args.get("max_results", 20)

if prompt is not None:
    if not pattern:
        result = {"error": "No pattern provided"}
    else:
        matches = []
        try:
            for m in re.finditer(pattern, prompt, re.IGNORECASE):
                if len(matches) >= max_results:
                    break
                matches.append({
                    "match": m.group(),
                    "start": m.start(),
                    "end": m.end(),
                    "context": prompt[max(0, m.start()-50):min(len(prompt), m.end()+50)]
                })
            result = {
                "matches": matches,
                "count": len(matches),
                "total_in_text": len(re.findall(pattern, prompt, re.IGNORECASE))
            }
        except re.error as e:
            result = {"error": f"Invalid regex pattern: {str(e)}"}

