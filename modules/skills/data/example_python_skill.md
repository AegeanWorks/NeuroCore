# Python Best Practices

## Overview
This skill provides guidelines for writing clean, maintainable, and Pythonic code. Follow these practices to ensure your code is readable, efficient, and follows community standards.

## Best Practices

### Code Style
- Follow PEP 8 style guide for Python code
- Use 4 spaces for indentation (no tabs)
- Keep line length to 79 characters maximum
- Use meaningful variable and function names
- Add docstrings to all public modules, functions, classes, and methods

### Error Handling
- Use specific exceptions rather than bare `except:` clauses
- Use `try/except` blocks to handle expected errors gracefully
- Always clean up resources using `try/finally` or context managers
- Log exceptions with appropriate context for debugging

### Code Organization
- One statement per line
- Organize imports: standard library, third-party, local
- Use absolute imports over relative imports
- Keep functions focused and single-purpose
- Avoid deeply nested code (maximum 3-4 levels)

### Performance
- Use list comprehensions and generator expressions appropriately
- Use `with` statements for resource management
- Prefer `is` or `is not` for singletons like `None`
- Use `dict.get()` for safe dictionary access
- Consider using `collections` module for specialized data structures

## Guidelines

1. **Write tests first** - Use TDD approach when possible
2. **Keep it simple** - Simple is better than complex
3. **Explicit is better than implicit** - Avoid magic when possible
4. **Readability counts** - Code is read more often than written
5. **Document as you go** - Don't postpone documentation

## Examples

### Good: Using context managers
```python
# Good
with open('file.txt', 'r') as f:
    content = f.read()

# Bad
f = open('file.txt', 'r')
content = f.read()
f.close()
```

### Good: List comprehension
```python
# Good
squares = [x**2 for x in range(10) if x % 2 == 0]

# Bad
squares = []
for x in range(10):
    if x % 2 == 0:
        squares.append(x**2)
```

### Good: Exception handling
```python
# Good
try:
    result = process_data(data)
except ValueError as e:
    logger.error(f"Invalid data format: {e}")
    return None

# Bad
try:
    result = process_data(data)
except:
    return None
```

## Common Patterns

- Use `if __name__ == "__main__":` for script entry points
- Use `@property` for computed attributes
- Use `dataclasses` for simple data containers
- Use `typing` module for type hints
- Use `pathlib` for file path operations
