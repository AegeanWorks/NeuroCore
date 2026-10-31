# Chunk.py - Split text into overlapping chunks of given size
# Supports two modes:
#   RLM mode:    var_name omitted → splits repl_state["prompt_var"]
#   Hybrid mode: var_name provided → splits repl_state["variables"][var_name]

args = args.get("args", {})
repl_state = args.get("_repl_state", {})

var_name = args.get("var_name", None)
if var_name is not None:
    variables = repl_state.get("variables", {})
    if var_name in variables:
        prompt = str(variables[var_name])
    else:
        result = {"error": f"Variable '{var_name}' not found", "available": list(variables.keys()), "chunks": [], "count": 0}
        prompt = None
else:
    prompt = repl_state.get("prompt_var", "")

size = args.get("size", 2000)
overlap = args.get("overlap", 200)

if prompt is None:
    pass  # error result already set above
elif not prompt:
    result = {"error": "No text to chunk", "chunks": [], "count": 0}
elif size <= 0 or overlap < 0 or overlap >= size:
    result = {"error": "size must be > overlap >= 0", "chunks": [], "count": 0}
else:
    chunks = []
    i = 0
    while i < len(prompt):
        chunk_text = prompt[i:i+size]
        chunks.append({
            "index": len(chunks),
            "start": i,
            "end": i + len(chunk_text),
            "text": chunk_text,
            "length": len(chunk_text)
        })
        i += size - overlap

    result = {"chunks": chunks, "count": len(chunks)}

