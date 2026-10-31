import ast
import math
import operator

expression = str(args.get('expression', ''))

# Allowed operators for safe evaluation
ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}

# Safe constants and functions
safe_constants = {
    "pi": math.pi,
    "e": math.e,
    "inf": float('inf'),
}

safe_functions = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "pow": pow,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
}


def evaluate_node(node, variables=None):
    """Safe AST-based expression evaluator."""
    variables = variables or {}
    
    if isinstance(node, ast.Expression):
        return evaluate_node(node.body, variables)
        
    if isinstance(node, ast.Name):
        if node.id in safe_constants:
            return safe_constants[node.id]
        if node.id in safe_functions:
            return safe_functions[node.id]
        if node.id in variables:
            return variables[node.id]
        raise ValueError(f"Unknown variable: {node.id}")
    
    if isinstance(node, ast.Constant):
        return node.value
    
    if isinstance(node, ast.BinOp):
        left = evaluate_node(node.left, variables)
        right = evaluate_node(node.right, variables)
        op_type = type(node.op)
        if op_type not in ALLOWED_OPERATORS:
            raise ValueError(f"Operator not allowed: {op_type.__name__}")
        return ALLOWED_OPERATORS[op_type](left, right)
    
    if isinstance(node, ast.UnaryOp):
        operand = evaluate_node(node.operand, variables)
        op_type = type(node.op)
        if op_type not in ALLOWED_OPERATORS:
            raise ValueError(f"Operator not allowed: {op_type.__name__}")
        return ALLOWED_OPERATORS[op_type](operand)
    
    if isinstance(node, ast.Compare):
        left = evaluate_node(node.left, variables)
        for op, comparator in zip(node.ops, node.comparators):
            right = evaluate_node(comparator, variables)
            op_type = type(op)
            if op_type not in ALLOWED_OPERATORS:
                raise ValueError(f"Operator not allowed: {op_type.__name__}")
            if not ALLOWED_OPERATORS[op_type](left, right):
                return False
            left = right
        return True
    
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in safe_functions:
            args = [evaluate_node(arg, variables) for arg in node.args]
            return safe_functions[node.func.id](*args)
        raise ValueError(f"Function call not allowed: {node.func.id if isinstance(node.func, ast.Name) else 'unknown'}")
    
    raise ValueError(f"Unsupported AST node: {type(node).__name__}")


try:
    # Basic security check
    unsafe = False
    for forbidden in ["__", "import", "lambda", "exec", "eval", "compile", "class", "def"]:
        if forbidden in expression:
            unsafe = True
            break
            
    if not expression:
        result = "Error: No expression provided."
    elif unsafe:
        result = "Error: Unsafe expression detected."
    else:
        # Handle power operator ^ -> ** (common LLM mistake)
        expression = expression.replace('^', '**')
        
        # Parse and evaluate safely using AST
        try:
            tree = ast.parse(expression, mode='eval')
            calc_result = evaluate_node(tree)
            result = f"{calc_result}"
        except SyntaxError:
            result = "Error: Invalid expression syntax."
        except ValueError as e:
            result = f"Error: {str(e)}"
except Exception as e:
    result = f"Error evaluating expression: {str(e)}"
