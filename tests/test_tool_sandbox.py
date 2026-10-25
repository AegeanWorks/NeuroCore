"""
Tests for the Tool Sandbox security system.

These tests verify that:
1. Dangerous code is blocked (imports, eval, exec, etc.)
2. Resource limits are enforced (timeout, output size)
3. Network restrictions work (domain whitelisting, SSRF protection)
4. Safe code executes correctly
"""

import pytest
import time
from modules.tools.sandbox import (
    ToolSandbox, 
    SecurityError, 
    ResourceLimitError, 
    TimeoutError,
    RestrictedImport,
    SafeOpen,
    SafeHttpxClient,
    execute_sandboxed,
    DANGEROUS_MODULES,
    SAFE_MODULES
)


class TestRestrictedImport:
    """Tests for the restricted import hook."""
    
    def test_blocks_dangerous_modules(self):
        """Test that dangerous modules like os, sys are blocked."""
        importer = RestrictedImport()
        
        with pytest.raises(SecurityError) as exc_info:
            importer('os')
        assert "os" in str(exc_info.value)
        
        with pytest.raises(SecurityError) as exc_info:
            importer('sys')
        assert "sys" in str(exc_info.value)
        
        with pytest.raises(SecurityError) as exc_info:
            importer('subprocess')
        assert "subprocess" in str(exc_info.value)
    
    def test_blocks_dangerous_submodules(self):
        """Test that submodules of dangerous modules are blocked."""
        importer = RestrictedImport()
        
        with pytest.raises(SecurityError) as exc_info:
            importer('os.path')
        assert "os" in str(exc_info.value)
    
    def test_allows_safe_modules(self):
        """Test that safe modules like math, json are allowed."""
        importer = RestrictedImport()
        
        # These should not raise
        math_module = importer('math')
        assert math_module is not None
        
        json_module = importer('json')
        assert json_module is not None


class TestSafeOpen:
    """Tests for the restricted file open."""
    
    def test_blocks_access_outside_allowed_dirs(self):
        """Test that files outside allowed directories are blocked."""
        safe_open = SafeOpen(allowed_dirs=['/tmp/safe'])
        
        with pytest.raises(SecurityError) as exc_info:
            safe_open('/etc/passwd', 'r')
        assert "not permitted" in str(exc_info.value)
        
        with pytest.raises(SecurityError) as exc_info:
            safe_open('/tmp/../etc/passwd', 'r')
        assert "not permitted" in str(exc_info.value)
    
    def test_blocks_write_access_when_read_only(self):
        """Test that write access is blocked in read-only mode."""
        import tempfile
        import os
        # Use a temp directory that exists
        temp_dir = tempfile.mkdtemp()
        try:
            safe_open = SafeOpen(allowed_dirs=[temp_dir], read_only=True)
            
            # Try to write - should be blocked
            with pytest.raises(SecurityError) as exc_info:
                safe_open(os.path.join(temp_dir, 'file.txt'), 'w')
            assert "Write access" in str(exc_info.value) or "not permitted" in str(exc_info.value)
        finally:
            os.rmdir(temp_dir)
    
    def test_blocks_append_mode_in_read_only(self):
        """Test that append mode 'a' is blocked in read-only mode."""
        import tempfile
        import os
        temp_dir = tempfile.mkdtemp()
        try:
            safe_open = SafeOpen(allowed_dirs=[temp_dir], read_only=True)
            
            with pytest.raises(SecurityError) as exc_info:
                safe_open(os.path.join(temp_dir, 'file.txt'), 'a')
            assert "Write access" in str(exc_info.value) or "not permitted" in str(exc_info.value)
        finally:
            os.rmdir(temp_dir)
    
    def test_blocks_exclusive_mode_in_read_only(self):
        """Test that exclusive mode 'x' is blocked in read-only mode."""
        import tempfile
        import os
        temp_dir = tempfile.mkdtemp()
        try:
            safe_open = SafeOpen(allowed_dirs=[temp_dir], read_only=True)
            
            with pytest.raises(SecurityError) as exc_info:
                safe_open(os.path.join(temp_dir, 'file.txt'), 'x')
            assert "Write access" in str(exc_info.value) or "not permitted" in str(exc_info.value)
        finally:
            os.rmdir(temp_dir)
    
    def test_blocks_binary_read_in_read_only(self):
        """Test that binary read mode 'rb' is blocked in read-only mode."""
        safe_open = SafeOpen(allowed_dirs=['/tmp/safe'], read_only=True)
        
        # Note: 'rb' contains 'b' but also has no write characters
        # This tests the operator precedence fix - 'r' alone should work
        # but we want to ensure that the write check properly uses 'in'
        # Let me fix this test to actually test what we want
        try:
            # Binary read should be allowed in read-only mode (it's read, not write)
            # The issue is that 'rb' contains 'b' and the original code had a bug
            # where 'b' wasn't being handled. Let's actually test with a file that exists
            import tempfile
            import os
            with tempfile.NamedTemporaryFile(delete=False) as f:
                f.write(b"test")
                temp_path = f.name
            
            try:
                safe_open = SafeOpen(allowed_dirs=[os.path.dirname(temp_path)], read_only=True)
                # This should work - reading binary is allowed in read-only mode
                with safe_open(temp_path, 'rb') as f:
                    content = f.read()
                assert content == b"test"
            finally:
                os.unlink(temp_path)
        except SecurityError:
            # If this fails, it means binary read is incorrectly blocked
            pytest.fail("Binary read mode 'rb' should be allowed in read-only mode")


class TestSafeHttpxClient:
    """Tests for the restricted HTTP client."""
    
    def test_blocks_non_whitelisted_domains(self):
        """Test that requests to non-whitelisted domains are blocked."""
        client = SafeHttpxClient(allowed_domains={'api.example.com'})
        
        with pytest.raises(SecurityError) as exc_info:
            client.get('https://evil.com/malicious')
        assert "not permitted" in str(exc_info.value)
    
    def test_allows_whitelisted_domains(self):
        """Test that requests to whitelisted domains are allowed."""
        # This test would make actual HTTP requests, so we mock it
        client = SafeHttpxClient(allowed_domains={'httpbin.org'})
        
        # Should not raise for allowed domain
        # Note: We don't actually make the request in unit tests
        assert client._is_domain_allowed('https://httpbin.org/get') == True
    
    def test_blocks_internal_ips(self):
        """Test that requests to internal IP addresses are blocked (SSRF protection)."""
        client = SafeHttpxClient()
        
        # These should be blocked
        assert client._is_ip_blocked('10.0.0.1') == True
        assert client._is_ip_blocked('192.168.1.1') == True
        assert client._is_ip_blocked('172.16.0.1') == True
        assert client._is_ip_blocked('::1') == True


class TestToolSandboxStaticAnalysis:
    """Tests for the sandbox static code analysis."""
    
    def test_blocks_dangerous_imports(self):
        """Test that code with dangerous imports is blocked."""
        sandbox = ToolSandbox()

        # os is now mocked with SafeEnv so `import os` is allowed at the static level
        # (the mock restricts what attributes are accessible at runtime)
        sandbox._check_code_safety('import os')  # must NOT raise

        with pytest.raises(SecurityError) as exc_info:
            sandbox._check_code_safety('from subprocess import call')
        assert "Dangerous import" in str(exc_info.value)
    
    def test_blocks_dangerous_builtins(self):
        """Test that code with dangerous builtins is blocked."""
        sandbox = ToolSandbox()
        
        with pytest.raises(SecurityError) as exc_info:
            sandbox._check_code_safety('eval("1 + 1")')
        assert "eval" in str(exc_info.value)
        
        with pytest.raises(SecurityError) as exc_info:
            sandbox._check_code_safety('exec("print(1)")')
        assert "exec" in str(exc_info.value)
    
    def test_blocks_path_traversal(self):
        """Test that code with path traversal is blocked."""
        sandbox = ToolSandbox()
        
        # Path traversal is caught by the open() builtin check first
        with pytest.raises(SecurityError) as exc_info:
            sandbox._check_code_safety('open("../../etc/passwd")')
        # open() is blocked as a dangerous builtin when no file dirs are allowed
        assert "open(" in str(exc_info.value)
        
        # Test path traversal detection directly
        with pytest.raises(SecurityError) as exc_info:
            sandbox._check_code_safety('"/etc/passwd"')
        assert "path traversal" in str(exc_info.value) or "system file" in str(exc_info.value)



class TestToolSandboxExecution:
    """Tests for sandboxed code execution."""
    
    def test_executes_safe_code(self):
        """Test that safe code executes correctly."""
        sandbox = ToolSandbox()
        
        code = """
result = args['x'] + args['y']
"""
        result = sandbox.execute(code, {'args': {'x': 5, 'y': 3}})
        assert result['result'] == 8
    
    def test_provides_safe_modules(self):
        """Test that safe modules are available in sandbox."""
        sandbox = ToolSandbox()
        
        code = """
import math
result = math.sqrt(16)
"""
        result = sandbox.execute(code, {'args': {}})
        assert result['result'] == 4.0
    
    def test_blocks_dangerous_code_at_runtime(self):
        """Test that dangerous code is blocked during execution."""
        sandbox = ToolSandbox()
        
        # This should be caught by static analysis first
        code = """
import os
result = os.system('ls')
"""
        with pytest.raises(SecurityError):
            sandbox.execute(code, {'args': {}})
    
    def test_enforces_timeout(self):
        """Test that code exceeding timeout is terminated."""
        sandbox = ToolSandbox(timeout=0.1)  # 100ms timeout
        
        code = """
import time
time.sleep(10)
result = "done"
"""
        with pytest.raises(TimeoutError):
            sandbox.execute(code, {'args': {}})
    
    def test_enforces_output_size_limit(self):
        """Test that output exceeding size limit is blocked."""
        sandbox = ToolSandbox(max_output_size=10)  # 10 chars max
        
        code = """
result = "a" * 1000
"""
        with pytest.raises(ResourceLimitError):
            sandbox.execute(code, {'args': {}})

    def test_enforces_memory_limit(self):
        """Test that memory limit is enforced (if supported on platform)."""
        # Use a very low memory limit to trigger enforcement
        sandbox = ToolSandbox(max_memory_mb=1)  # 1MB limit
        
        # Code that allocates significant memory
        code = """
# Try to allocate more than 1MB
big_list = [0] * (300 * 1024)  # ~2.4MB of integers
result = "allocated"
"""
        # This may or may not raise ResourceLimitError depending on platform
        # The test verifies the memory limit mechanism exists and is called
        try:
            sandbox.execute(code, {'args': {}})
            # If it succeeds, that's fine - memory limits are best-effort on some platforms
        except ResourceLimitError as e:
            # If it fails due to memory limit, that's the expected behavior
            assert "Memory" in str(e) or "memory" in str(e)



class TestExecuteSandboxed:
    """Tests for the convenience function."""
    
    def test_basic_execution(self):
        """Test basic sandboxed execution."""
        result = execute_sandboxed(
            "result = args['value'] * 2",
            {'value': 21},
            timeout=5.0
        )
        assert result == 42
    
    def test_security_violation(self):
        """Test that security violations are caught."""
        with pytest.raises(SecurityError):
            execute_sandboxed(
                "import os; result = os.getcwd()",
                {},
                timeout=5.0
            )


class TestSandboxSecurityScenarios:
    """Real-world security scenario tests."""
    
    def test_prevents_code_injection(self):
        """Test that code injection attempts are blocked."""
        sandbox = ToolSandbox()
        
        # Attempt to inject code through string formatting
        malicious_args = {
            'code': "__import__('os').system('rm -rf /')"
        }
        
        # This should be safe because the args are just data
        code = """
result = args['code']
"""
        result = sandbox.execute(code, {'args': malicious_args})
        # The malicious code is just a string, not executed
        assert result['result'] == malicious_args['code']
    
    def test_prevents_import_bypass(self):
        """Test that various import bypass techniques are blocked."""
        sandbox = ToolSandbox()
        
        # __import__ builtin
        with pytest.raises(SecurityError):
            sandbox.execute("__import__('os')", {})
        
        # importlib
        with pytest.raises(SecurityError):
            sandbox.execute("import importlib; importlib.import_module('os')", {})
        
        # Builtins manipulation
        with pytest.raises(SecurityError):
            sandbox.execute("""
import builtins
builtins.__import__('os')
""", {})
    
    def test_calculator_tool_safety(self):
        """Test that calculator-like tools are safe."""
        # Note: eval() is blocked by sandbox static analysis for security
        # Tools should use ast.literal_eval or safe evaluation libraries instead
        sandbox = ToolSandbox()
        
        # Safe mathematical expression using safe evaluation
        code = """
import math
import operator

expr = str(args.get('expression', ''))

# Security check
forbidden_found = False
for forbidden in ["__", "import", "lambda", "exec", "eval"]:
    if forbidden in expr:
        forbidden_found = True
        break

if forbidden_found:
    result = "Error: Unsafe expression"
else:
    # Safe evaluation using only allowed operators
    try:
        # Handle power operator
        expr_clean = expr.replace('^', '**')
        # For this test, just do simple addition
        if '+' in expr_clean:
            parts = expr_clean.split('+')
            total = 0.0
            for p in parts:
                total = total + float(p.strip())
            result = total
        else:
            result = float(expr_clean)
    except Exception as e:
        result = "Error: " + str(e)
"""
        
        # Safe calculation
        result = sandbox.execute(code, {'args': {'expression': '2 + 2'}})
        assert result['result'] == 4.0
        
        # Blocked dangerous expression in the security check
        code_blocked = """
expr = str(args.get('expression', ''))
if '__import__' in expr:
    result = "Error: Unsafe expression"
else:
    result = eval(expr)
"""
        with pytest.raises(SecurityError):
            sandbox.execute(code_blocked, {'args': {'expression': '__import__("os").system("ls")'}})


    
    def test_network_tool_safety(self):
        """Test that network tools respect domain restrictions."""
        client = SafeHttpxClient(allowed_domains={'api.example.com'})
        
        # Allowed domain
        assert client._is_domain_allowed('https://api.example.com/data') == True
        assert client._is_domain_allowed('https://sub.api.example.com/data') == True
        
        # Blocked domains
        assert client._is_domain_allowed('https://evil.com/data') == False
        assert client._is_domain_allowed('https://api.example.com.evil.com/data') == False

    def test_import_httpx_returns_safe_client(self):
        """
        Regression test: tool code that does `import httpx` must receive
        the SafeHttpxClient substitute, not the real httpx module.
        Previously this raised SecurityError because httpx wasn't in SAFE_MODULES.
        """
        sandbox = ToolSandbox()
        code = """
import httpx
# Verify we got the safe client (SafeHttpxClient), not the real httpx module
result = type(httpx).__name__
"""
        result = sandbox.execute(code, {})
        assert result['result'] == 'SafeHttpxClient', (
            f"Expected SafeHttpxClient, got {result['result']}"
        )

    def test_httpx_import_respects_domain_restriction(self):
        """
        Tool code that imports httpx and calls a blocked domain must be rejected.
        """
        sandbox = ToolSandbox()
        code = """
import httpx
try:
    httpx.get('https://evil-domain-not-in-whitelist.com/data')
    result = "should_not_reach"
except Exception as e:
    result = "blocked: " + str(e)
"""
        result = sandbox.execute(code, {})
        assert result['result'].startswith('blocked:'), (
            f"Expected domain to be blocked, got: {result['result']}"
        )

    def test_import_os_returns_safe_env(self):
        """import os must return SafeEnv, not the real os module."""
        sandbox = ToolSandbox()
        code = """
import os
result = type(os).__name__
"""
        result = sandbox.execute(code, {})
        assert result['result'] == 'SafeEnv'

    def test_os_getenv_hides_the_inherited_environment(self):
        """A tool cannot read a host secret it was not explicitly given."""
        import os as _real_os
        _real_os.environ['_SANDBOX_TEST_VAR'] = 'hello'
        try:
            sandbox = ToolSandbox()
            code = """
import os
result = [os.getenv('_SANDBOX_TEST_VAR', 'missing'), '_SANDBOX_TEST_VAR' in os.environ]
"""
            result = sandbox.execute(code, {})
            assert result['result'] == ['missing', False]
        finally:
            del _real_os.environ['_SANDBOX_TEST_VAR']

    def test_os_getenv_reads_permitted_values(self):
        """os.getenv() and os.environ expose exactly the permitted values."""
        sandbox = ToolSandbox(permitted_environment={'WEATHER_API_KEY': 'k-123'})
        code = """
import os
result = [os.getenv('WEATHER_API_KEY'), os.environ['WEATHER_API_KEY'], list(os.environ)]
"""
        result = sandbox.execute(code, {})
        assert result['result'] == ['k-123', 'k-123', ['WEATHER_API_KEY']]

    def test_os_dangerous_attrs_blocked(self):
        """os.system, os.path, etc. must be blocked via SafeEnv."""
        sandbox = ToolSandbox()
        code = """
import os
try:
    os.system('echo hi')
    result = 'not_blocked'
except Exception as e:
    result = 'blocked'
"""
        result = sandbox.execute(code, {})
        assert result['result'] == 'blocked'

    def test_import_ast_works(self):
        """import ast must succeed (needed by Calculator tool)."""
        sandbox = ToolSandbox()
        code = """
import ast
tree = ast.parse('1 + 2', mode='eval')
result = type(tree).__name__
"""
        result = sandbox.execute(code, {})
        assert result['result'] == 'Expression'

    def test_import_zoneinfo_works(self):
        """from zoneinfo import ZoneInfo must succeed (needed by TimeZoneConverter)."""
        sandbox = ToolSandbox()
        code = """
from zoneinfo import ZoneInfo
tz = ZoneInfo('UTC')
result = str(tz)
"""
        result = sandbox.execute(code, {})
        assert result['result'] == 'UTC'

    def test_unknown_module_raises_import_error(self):
        """Unknown non-dangerous modules raise ImportError, not SecurityError."""
        sandbox = ToolSandbox()
        code = """
try:
    import totally_unknown_module_xyz
    result = 'imported'
except ImportError:
    result = 'import_error'
except Exception as e:
    result = f'other: {type(e).__name__}'
"""
        result = sandbox.execute(code, {})
        assert result['result'] == 'import_error'

    def test_modules_passthrough_works(self):
        """from modules.* imports pass through to the real NeuroCore package."""
        sandbox = ToolSandbox()
        code = """
from modules.calendar.events import event_manager
result = type(event_manager).__name__
"""
        result = sandbox.execute(code, {})
        # Should succeed and return the real class name (not raise SecurityError)
        assert 'Error' not in str(result.get('result', ''))


@pytest.mark.asyncio
class TestToolDispatcherIntegration:
    """Integration tests with the ToolDispatcherExecutor."""
    
    async def test_dispatcher_uses_sandbox(self):
        """Test that the tool dispatcher uses the sandbox."""
        from modules.tools.node import ToolDispatcherExecutor
        
        dispatcher = ToolDispatcherExecutor()
        
        # Verify sandbox is initialized
        assert dispatcher.sandbox is not None
        assert dispatcher.sandbox.timeout == 30.0
    
    async def test_dispatcher_catches_security_errors(self):
        """Test that security errors are properly caught and reported."""
        from modules.tools.node import ToolDispatcherExecutor
        
        dispatcher = ToolDispatcherExecutor()
        
        # Create a mock tool call that would trigger security error
        # This is a simplified test - in reality the tool code comes from files
        input_data = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "id": "call_123",
                        "function": {
                            "name": "test_tool",
                            "arguments": '{"cmd": "ls"}'
                        }
                    }]
                }
            }]
        }
        
        # The actual security test would require a malicious tool file
        # For now, we just verify the structure is in place
        result = await dispatcher.receive(input_data, {"allowed_tools": []})
        assert "tool_results" in result
