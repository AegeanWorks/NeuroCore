# SetFinal.py - Set final answer and terminate RLM loop
# Used in RLM (Recursive Language Model) to signal completion

args = args.get("args", {})
repl_state = args.get("_repl_state", {})

value = args.get("value", "")

# Store the final value - return state updates in result for proper propagation
result = {
    "success": True,
    "final_set": True,
    "value_length": len(str(value)),
    "value_preview": str(value)[:200] + "..." if len(str(value)) > 200 else str(value),
    "_state_update": {"final": value}  # Return state update for executor to merge
}

