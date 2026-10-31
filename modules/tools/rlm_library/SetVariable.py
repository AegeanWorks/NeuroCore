# SetVariable.py - Store intermediate results in REPL state
# Used in RLM (Recursive Language Model) for accumulating results across iterations

args = args.get("args", {})
repl_state = args.get("_repl_state", {})

name = args.get("name", "")
value = args.get("value", "")

if not name:
    result = {"error": "No variable name provided"}
else:
    variables = repl_state.get("variables", {})
    variables[name] = value
    
    # Return state updates in result for proper propagation instead of direct mutation
    result = {
        "success": True,
        "name": name,
        "value_type": type(value).__name__,
        "stored_keys": list(variables.keys()),
        "_state_update": {"variables": variables}  # Return state update for executor to merge
    }

