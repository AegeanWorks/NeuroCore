"""
Tool Execution Sandbox for NeuroCore

Provides a secure execution environment for custom Python tools with:
- Restricted builtins and globals
- Resource limits (timeout, memory, output size)
- Network access controls
- Filesystem restrictions
"""

import builtins
import sys
import os
import re
import signal
import threading
import functools
from typing import Any, Dict, Set, Optional, List
from contextlib import contextmanager
import json
import multiprocessing
import tempfile
from multiprocessing import Process
import logging
import tracemalloc

logger = logging.getLogger(__name__)

# Store the real __import__ function at module load time
# This avoids issues where __builtins__ might be a module instead of a dict
_real_import = builtins.__import__

# Memory limit enforcement - platform specific
try:
    import resource
    HAS_RESOURCE_MODULE = True
except ImportError:
    HAS_RESOURCE_MODULE = False




# Dangerous builtins that should never be available in sandboxed code
DANGEROUS_BUILTINS: Set[str] = {
    'open', 'input', 'raw_input', 'exec', 'eval', 'compile', 
    'breakpoint', 'help', 'quit', 'exit', '__import__',
    'reload', 'file', 'execfile',
}

# Reflection helpers expose the interpreter object graph even when the target attribute is
# computed (e.g. getattr((), "__cla" + "ss__")), so they are unavailable in the sandbox.
SANDBOX_BLOCKED_BUILTINS: Set[str] = {
    'getattr', 'setattr', 'delattr', 'hasattr', 'vars', 'globals', 'locals',
}

# Callable attributes that can retrieve an attribute by a string without calling getattr.
SANDBOX_BLOCKED_ATTRIBUTES: Set[str] = {'attrgetter', 'methodcaller'}

# Dangerous modules that should not be importable
DANGEROUS_MODULES: Set[str] = {
    'os', 'sys', 'subprocess', 'socket', 'multiprocessing',
    'threading', 'ctypes', 'mmap', 'resource', 'signal',
    'pickle', 'cPickle', 'marshal', 'imp', 'importlib',
    'site', 'warnings', 'traceback', 'gc', 'inspect',
    'code', 'codeop', 'pdb', 'bdb', 'cmd',
    'shutil', 'tempfile', 'pathlib', 'path', 'glob',
    'fnmatch', 'linecache', 'tracemalloc', 'faulthandler',
    'posix', 'nt', 'java', 'org', 'com', 'net',
}

# Allowed safe modules
SAFE_MODULES: Set[str] = {
    'math', 'random', 'datetime', 'time', 'calendar',
    'decimal', 'fractions', 'numbers', 'statistics',
    'itertools', 'functools', 'operator', 'copy',
    'string', 're', 'collections', 'enum', 'typing',
    'hashlib', 'base64', 'binascii', 'uuid', 'json',
    'html', 'html.entities', 'html.parser', 'urllib.parse',
    'textwrap', 'unicodedata', 'stringprep', 'codecs',
    'dataclasses', 'abc', 'contextlib', 'contextvars',
    'types', 'weakref', 'array', 'bisect', 'heapq',
    'copyreg', 'pickletools',
    # AST module — needed by Calculator tool for safe expression parsing
    'ast',
    # Timezone support — needed by TimeZoneConverter tool
    'zoneinfo',
    # Email — needed by SendEmail tool
    'smtplib', 'ssl', 'email', 'email.mime', 'email.mime.text', 'email.mime.multipart',
    # Third-party optional libraries (treated as safe if installed)
    'youtube_transcript_api',
}

# First-party modules whose imports pass through to the real Python importer unchanged.
# This is an explicit allowlist, not a namespace prefix: a bare ``modules`` prefix let sandboxed
# code import ``modules.tools.sandbox`` and reach the real ``os``/``sys`` globals of this very
# module, which is a direct escape.
#
# Only ``modules.calendar.events`` is allowed by default; the test suite exercises this path.
# If untrusted user code needs other first-party modules, add them here explicitly.
PASSTHROUGH_MODULES: Set[str] = {'modules.calendar.events'}

# Attributes that reach the real interpreter object graph. Traversing them escapes the sandbox
# (e.g. ``().__class__.__mro__[1].__subclasses__()`` finds the real ``os`` module).
DANGEROUS_ATTRIBUTES: Set[str] = {
    '__class__', '__bases__', '__base__', '__mro__', '__subclasses__',
    '__globals__', '__code__', '__closure__', '__builtins__', '__loader__',
    '__spec__', '__reduce__', '__reduce_ex__', '__getattribute__', '__subclasshook__',
    '__dict__', '__module__', '__func__', '__self__', '__wrapped__',
    '__defaults__', '__kwdefaults__', '__annotations__', '__getattr__', '__setattr__',
}


class SecurityError(Exception):
    """Raised when sandbox security policy is violated."""
    pass


class ResourceLimitError(Exception):
    """Raised when resource limits are exceeded."""
    pass


class TimeoutError(Exception):
    """Raised when tool execution exceeds time limit."""
    pass


class SandboxStartupError(RuntimeError):
    """Raised when the isolated worker cannot start.

    The sandbox never downgrades to in-process execution: a worker that cannot start is a
    failed execution, not a licence to run untrusted code inside the application process.
    """
    pass


class RestrictedImport:
    """
    Import hook that blocks dangerous modules and only allows safe ones.
    Accepts an optional ``mock_modules`` dict that maps module names to
    pre-built safe substitute objects (e.g. SafeHttpxClient for 'httpx').
    When a mocked module is imported, the substitute is returned instead of
    the real module — this lets tool code use ``import httpx`` while still
    receiving the domain-restricted SafeHttpxClient.
    """

    def __init__(self, allowed_modules: Optional[Set[str]] = None,
                 mock_modules: Optional[Dict[str, Any]] = None,
                 passthrough_modules: Optional[Set[str]] = None):
        self.allowed = allowed_modules or SAFE_MODULES
        self.blocked = DANGEROUS_MODULES
        self.mock_modules: Dict[str, Any] = mock_modules or {}
        # First-party modules whose imports pass through to the real importer unchanged.
        self.passthrough: Set[str] = passthrough_modules or PASSTHROUGH_MODULES

    def _is_passthrough(self, name: str) -> bool:
        """Whether `name` is exactly, or a submodule of, an allowlisted first-party module.

        A bare parent name (e.g. ``modules``) is deliberately *not* a match: importing the
        package alone would expose every already-loaded submodule as an attribute.
        """
        return any(name == allowed or name.startswith(allowed + '.') for allowed in self.passthrough)

    def __call__(self, name: str, globals=None, locals=None, fromlist=(), level=0):
        base_module = name.split('.')[0]

        # Return pre-built safe substitute (httpx → SafeHttpxClient, os → SafeEnv, etc.)
        if base_module in self.mock_modules:
            return self.mock_modules[base_module]

        # Block dangerous modules
        if base_module in self.blocked:
            raise SecurityError(f"Import of module '{name}' is not allowed in sandboxed environment")

        # Pass through trusted first-party modules (e.g. modules.calendar.events)
        if self._is_passthrough(name):
            return _real_import(name, globals, locals, fromlist, level)

        # Check if full name or any prefix is in allowed list
        # This supports both 'urllib.parse' and 'urllib' when 'urllib.parse' is in SAFE_MODULES
        parts = name.split('.')
        for i in range(len(parts)):
            prefix = '.'.join(parts[:i+1])
            if prefix in self.allowed:
                # Use the stored real __import__ function instead of __builtins__ subscript
                return _real_import(name, globals, locals, fromlist, level)

        # Unknown (non-dangerous, non-safe) module — raise ImportError so tools
        # with graceful ``except ImportError`` fallbacks work correctly.
        raise ImportError(f"Import of module '{name}' is not permitted. Allowed modules: {sorted(self.allowed)}")



class SafeOpen:
    """
    Restricted file open that only allows access to specific directories.
    """
    
    def __init__(self, allowed_dirs: Optional[List[str]] = None, read_only: bool = True):
        self.allowed_dirs = allowed_dirs or []
        self.read_only = read_only
    
    def _is_path_allowed(self, path: str) -> bool:
        """Check if a path is within allowed directories."""
        # Normalize the path
        try:
            real_path = os.path.realpath(os.path.abspath(path))
        except (OSError, ValueError):
            return False
        
        # Check for path traversal attempts (but allow absolute paths that resolve to allowed dirs)
        if '..' in path:
            # Path traversal detected - reject it
            return False
        
        # Check if path is within allowed directories using realpath containment
        # This allows absolute paths like /home/user/data that resolve inside allowed dirs
        for allowed_dir in self.allowed_dirs:
            try:
                real_allowed = os.path.realpath(os.path.abspath(allowed_dir))
                if real_path.startswith(real_allowed + os.sep) or real_path == real_allowed:
                    return True
            except (OSError, ValueError):
                continue
        
        return False
    
    def __call__(self, file: str, mode: str = 'r', *args, **kwargs):
        if not self._is_path_allowed(file):
            raise SecurityError(f"Access to file '{file}' is not permitted in sandboxed environment")
        
        # In read-only mode, only allow read modes (starting with 'r')
        if self.read_only and not mode.startswith('r'):
            raise SecurityError(f"Write access to file '{file}' is not permitted in sandboxed environment")
        
        _open_fn = getattr(builtins, "open")
        return _open_fn(file, mode, *args, **kwargs)


class SafeHttpxClient:
    """
    Restricted HTTP client that enforces network security policies.
    """
    
    # Default allowed domains (can be configured)
    DEFAULT_ALLOWED_DOMAINS: Set[str] = {
        'api.openai.com',
        'api.anthropic.com',
        'api.together.xyz',
        'api.groq.com',
        'api.cohere.com',
        'api.ai21.com',
        'api.perplexity.ai',
        'api.mistral.ai',
        'generativelanguage.googleapis.com',
        'api.replicate.com',
        'api.huggingface.co',
        'api.wolframalpha.com',
        'api.weatherapi.com',  # Weather tool
        'wttr.in',  # Weather tool (wttr.in JSON API)
        'api.frankfurter.app',  # Currency converter
        'en.wikipedia.org',  # Wikipedia
        'export.arxiv.org',  # ArXiv
        'www.youtube.com', 'youtube.com',  # YouTube transcripts
        'api.telegram.org',  # Telegram
        '127.0.0.1',  # Internal API for modules (BrowserAutomation)
        'localhost',  # Internal API for modules
        'github.com', 'www.github.com', 'raw.githubusercontent.com', # Added for FetchURL
    }
    
    # Allowed URL schemes
    ALLOWED_SCHEMES: Set[str] = {'http', 'https'}

    # Names that may resolve to loopback. Any other name resolving there is DNS rebinding.
    LOOPBACK_NAMES: Set[str] = {'127.0.0.1', 'localhost'}

    # The only NeuroCore routes tools may call on the local control plane. Loopback clients need
    # no API token, so any other route (settings, module install, marketplace) would let tool
    # code reconfigure the app or load code into its process.
    TOOL_API_PREFIXES = ('/memory/goals', '/browser_automation/api/')
    
    # Blocked IP ranges (IPv4)
    BLOCKED_IPV4_RANGES: List[str] = [
        # Note: 127.0.0.0/8 is implicitly permitted for authorized internal domains (like 127.0.0.1) 
        # but blocked for general arbitrary fetching unless domain matches.
        '10.0.0.0/8',       # Private Class A
        '172.16.0.0/12',    # Private Class B
        '192.168.0.0/16',   # Private Class C
        '169.254.0.0/16',   # Link-local
        '0.0.0.0/8',        # Current network
    ]
    
    # Blocked IP ranges (IPv6)
    BLOCKED_IPV6_RANGES: List[str] = [
        '::1/128',          # Loopback
        'fc00::/7',         # Unique local address (private)
        'fe80::/10',        # Link-local
        '::/128',           # Unspecified
        '::ffff:0:0/96',    # IPv4-mapped
    ]
    
    def __init__(self, allowed_domains: Optional[Set[str]] = None, 
                 timeout: float = 30.0,
                 max_response_size: int = 10 * 1024 * 1024,  # 10MB
                 max_redirects: int = 5):
        # None means "use the default allowlist"; an explicit empty set means no network.
        self.allowed_domains = (
            set(self.DEFAULT_ALLOWED_DOMAINS)
            if allowed_domains is None else set(allowed_domains)
        )
        self.timeout = timeout
        self.max_response_size = max_response_size
        self.max_redirects = max(0, int(max_redirects))
        
        # Import ipaddress for proper CIDR-based IP validation
        import ipaddress
        self._ipaddress = ipaddress
        
        # Pre-compile IPv4 blocking patterns
        self._blocked_ipv4_networks = []
        for cidr in self.BLOCKED_IPV4_RANGES:
            try:
                self._blocked_ipv4_networks.append(ipaddress.ip_network(cidr, strict=False))
            except ValueError as _exc:
                logger.debug("ignored exception: %s", _exc)
        
        # Pre-compile IPv6 blocking patterns  
        self._blocked_ipv6_networks = []
        for cidr in self.BLOCKED_IPV6_RANGES:
            try:
                self._blocked_ipv6_networks.append(ipaddress.ip_network(cidr, strict=False))
            except ValueError as _exc:
                logger.debug("ignored exception: %s", _exc)
    
    def _is_domain_allowed(self, url: str) -> bool:
        """Check if a URL's domain is in the allowed list."""
        from urllib.parse import urlparse

        if not self.allowed_domains:
            return False
        
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            # Remove port if present
            if ':' in domain:
                domain = domain.split(':')[0]
            
            # Check exact match or subdomain match
            for allowed in self.allowed_domains:
                if domain == allowed or domain.endswith('.' + allowed):
                    return True
            
            return False
        except Exception:
            return False
    
    def _is_ip_blocked(self, host: str) -> bool:
        """Check if a host/IP is in blocked ranges using CIDR notation."""
        try:
            # Try to parse as IP address directly
            ip = self._ipaddress.ip_address(host)
            
            # Check IPv4 blocked ranges
            if isinstance(ip, self._ipaddress.IPv4Address):
                for network in self._blocked_ipv4_networks:
                    if ip in network:
                        return True
            
            # Check IPv6 blocked ranges
            if isinstance(ip, self._ipaddress.IPv6Address):
                for network in self._blocked_ipv6_networks:
                    if ip in network:
                        return True
            
            return False
        except ValueError:
            # Not an IP address, allow through (domain check happens elsewhere)
            return False
    
    def _resolve_and_validate_ip(self, hostname: str) -> str:
        """
        Resolve hostname and validate every resulting IP.
        
        Uses getaddrinfo for IPv4/IPv6 support instead of gethostbyname.
        
        Returns:
            The first resolved IP address (all answers were validated)
            
        Raises:
            SecurityError: If any answer is blocked or resolution fails. A host that returns
                both public and private addresses is rejected as a whole: accepting the public
                answer would leave the client free to connect to the private one.
        """
        import socket
        
        try:
            # getaddrinfo returns list of (family, type, proto, canonname, sockaddr)
            # We want to check both IPv4 and IPv6 results
            results = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            
            if not results:
                raise SecurityError(f"Could not resolve domain '{hostname}' - no DNS results")
            
            resolved_ips = [sockaddr[0] for _, _, _, _, sockaddr in results]
            blocked = [ip_addr for ip_addr in resolved_ips if self._is_ip_blocked(ip_addr)]
            if blocked:
                raise SecurityError(
                    f"Domain '{hostname}' resolves to blocked IP addresses: {blocked}"
                )
            if hostname not in self.LOOPBACK_NAMES:
                loopback = [ip_addr for ip_addr in resolved_ips if self._is_loopback(ip_addr)]
                if loopback:
                    raise SecurityError(
                        f"Domain '{hostname}' resolves to loopback addresses: {loopback}"
                    )
            return resolved_ips[0]
            
        except socket.gaierror:
            raise SecurityError(f"Could not resolve domain '{hostname}' - DNS lookup failed")
        except socket.herror:
            raise SecurityError(f"Could not resolve domain '{hostname}' - host not found")
    
    def _is_loopback(self, host: str) -> bool:
        try:
            return self._ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def _validate_control_plane_path(self, parsed) -> None:
        """Allow only the tool APIs when the target is NeuroCore's own port on loopback."""
        from core.security import bind_port

        port = parsed.port or (443 if parsed.scheme.lower() == 'https' else 80)
        if port != bind_port():
            return
        path = parsed.path or '/'
        if '..' in path or '%' in path or not path.startswith(self.TOOL_API_PREFIXES):
            raise SecurityError(
                f"Tools may not call '{path}' on the NeuroCore control plane; only "
                f"{', '.join(self.TOOL_API_PREFIXES)} are available."
            )

    def _validate_target(self, url: str) -> None:
        """Validate scheme, allowlist and every DNS answer for one request target."""
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.scheme.lower() not in self.ALLOWED_SCHEMES:
            raise SecurityError(
                f"URL scheme '{parsed.scheme}' is not allowed. "
                f"Only {', '.join(self.ALLOWED_SCHEMES)} are permitted."
            )
        hostname = parsed.hostname
        if not hostname:
            raise SecurityError(f"Invalid URL '{url}': no hostname found")
        hostname = hostname.lower()
        if not self._is_domain_allowed(url):
            raise SecurityError(
                f"HTTP requests to '{url}' are not permitted. Domain not in whitelist."
            )
        resolved = self._resolve_and_validate_ip(hostname)
        if hostname in self.LOOPBACK_NAMES or self._is_loopback(resolved):
            self._validate_control_plane_path(parsed)

    def request(self, method: str, url: str, **kwargs):
        """Make a restricted HTTP request.

        Redirects are followed manually so every destination is re-validated against the
        scheme, hostname and DNS policy. The body is streamed into a bounded buffer instead of
        being buffered by httpx first, so an oversized response cannot exhaust memory.

        Note: DNS is validated immediately before the request, but Python's HTTP stack resolves
        the name again while connecting. Pinning the validated address to that connection needs
        transport-level support; until then this is defense in depth, not a complete SSRF
        boundary.
        """
        import httpx
        from urllib.parse import urljoin

        kwargs = dict(kwargs)
        kwargs.pop("follow_redirects", None)
        supplied_timeout = kwargs.get("timeout", self.timeout)
        try:
            timeout = min(float(supplied_timeout), float(self.timeout))
        except (TypeError, ValueError):
            timeout = self.timeout
        kwargs["timeout"] = timeout

        current_url = url
        current_method = str(method).upper()
        current_kwargs = kwargs
        with httpx.Client(follow_redirects=False, timeout=timeout, trust_env=False) as client:
            for hop in range(self.max_redirects + 1):
                self._validate_target(current_url)
                request = client.build_request(current_method, current_url, **current_kwargs)
                response = client.send(request, stream=True)
                try:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise SecurityError(f"Redirect from '{current_url}' has no location")
                        if hop >= self.max_redirects:
                            raise SecurityError(
                                f"Too many redirects while requesting '{url}'"
                            )
                        next_url = httpx.URL(urljoin(str(response.url), location))
                        if (next_url.scheme, next_url.host, next_url.port) != (
                                response.url.scheme, response.url.host, response.url.port):
                            current_kwargs = self._without_credentials(current_kwargs)
                        current_url = str(next_url)
                        if response.status_code in (301, 302, 303) \
                                and current_method not in ("GET", "HEAD"):
                            current_method = "GET"
                            current_kwargs = {
                                key: value for key, value in current_kwargs.items()
                                if key not in ("json", "data", "content", "files")
                            }
                        continue

                    chunks = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > self.max_response_size:
                            raise ResourceLimitError(
                                f"Response size exceeds maximum allowed "
                                f"({self.max_response_size} bytes)"
                            )
                        chunks.append(chunk)
                    headers = [
                        (key, value) for key, value in response.headers.multi_items()
                        if key.lower() not in (
                            "content-encoding", "content-length", "transfer-encoding",
                        )
                    ]
                    return httpx.Response(
                        response.status_code,
                        headers=headers,
                        content=b"".join(chunks),
                        request=response.request,
                    )
                finally:
                    response.close()
        raise SecurityError(f"Too many redirects while requesting '{url}'")

    CREDENTIAL_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie"})

    def _without_credentials(self, kwargs: dict) -> dict:
        """Request options with credentials removed, for a redirect to another origin."""
        cleaned = {key: value for key, value in kwargs.items() if key not in ("auth", "cookies")}
        headers = kwargs.get("headers")
        if headers:
            items = headers.items() if hasattr(headers, "items") else headers
            cleaned["headers"] = [
                (key, value) for key, value in items
                if str(key).lower() not in self.CREDENTIAL_HEADERS
            ]
        return cleaned

    def get(self, url: str, **kwargs):
        return self.request('GET', url, **kwargs)
    
    def post(self, url: str, **kwargs):
        return self.request('POST', url, **kwargs)

    def delete(self, url: str, **kwargs):
        return self.request('DELETE', url, **kwargs)

    def put(self, url: str, **kwargs):
        return self.request('PUT', url, **kwargs)

    def patch(self, url: str, **kwargs):
        return self.request('PATCH', url, **kwargs)


class SafeEnv:
    """
    Restricted mock for the ``os`` module.

    Only environment variables the caller explicitly permits are visible. Tools never receive
    the worker process environment as a whole, so an API key or bot token cannot be read just
    because the host process happens to hold it. All other ``os`` attributes raise
    SecurityError.
    """

    class _ReadOnlyEnviron:
        """A read-only view over the permitted environment values."""

        def __init__(self, permitted: Dict[str, str]):
            self._permitted = dict(permitted)

        def get(self, key: str, default=None):
            return self._permitted.get(key, default)

        def __getitem__(self, key: str):
            try:
                return self._permitted[key]
            except KeyError:
                raise KeyError(key) from None

        def __contains__(self, key: str):
            return key in self._permitted

        def __iter__(self):
            return iter(self._permitted)

        def keys(self):
            return self._permitted.keys()

        def items(self):
            return self._permitted.items()

    def __init__(self, permitted: Optional[Dict[str, str]] = None):
        self._permitted = {
            str(key): str(value)
            for key, value in (permitted or {}).items()
            if isinstance(value, (str, int, float, bool))
        }
        self.environ = self._ReadOnlyEnviron(self._permitted)

    def getenv(self, key: str, default=None):
        return self._permitted.get(key, default)

    def __getattr__(self, name: str):
        raise SecurityError(
            f"os.{name} is not permitted in sandboxed environment. "
            "Only permitted environment variables are available."
        )


def _execute_in_process(code: str, local_vars: Dict[str, Any], result_file: str,
                        allowed_file_dirs: List[str], read_only_files: bool,
                        allowed_domains: Optional[Set[str]], timeout: float,
                        max_output_size: int, max_memory_mb: int,
                        permitted_environment: Optional[Dict[str, str]] = None):
    """Execute code in a separate process with restricted environment.

    Results are written to *result_file* as JSON rather than through a
    multiprocessing.Queue.  This avoids the "cannot pickle '_asyncio.Future'"
    error that occurs on Windows when Queue pipe-handles get registered with
    asyncio's ProactorEventLoop (IOCP) before being duplicated for the child.
    """
    payload: dict = {'success': False, 'error_type': 'Unknown', 'error_msg': 'Process did not complete'}
    try:
        # Enforce memory limit using resource module (Unix only)
        if HAS_RESOURCE_MODULE and max_memory_mb > 0:
            max_bytes = max_memory_mb * 1024 * 1024
            try:
                resource.setrlimit(resource.RLIMIT_AS, (max_bytes, max_bytes))
            except (ValueError, OSError) as e:
                logger.warning(f"Failed to set memory limit: {e}")

        # Start tracemalloc for memory monitoring (cross-platform)
        if max_memory_mb > 0:
            try:
                tracemalloc.start()
            except Exception as _exc:
                logger.debug("ignored exception: %s", _exc)  # Optional on some platforms

        # Rebuild sandbox environment in child process
        sandbox = ToolSandbox(
            timeout=timeout,
            allowed_file_dirs=allowed_file_dirs,
            allowed_domains=allowed_domains,
            read_only_files=read_only_files,
            max_output_size=max_output_size,
            max_memory_mb=max_memory_mb,
            permitted_environment=permitted_environment,
        )

        result = sandbox._execute_internal(code, local_vars)

        # Check memory usage after execution
        if max_memory_mb > 0:
            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            peak_mb = peak / (1024 * 1024)
            if peak_mb > max_memory_mb:
                raise ResourceLimitError(
                    f"Memory limit exceeded: {peak_mb:.1f}MB used, limit was {max_memory_mb}MB"
                )

        payload = {'success': True, 'result': result.get('result')}

    except SecurityError as e:
        payload = {'success': False, 'error_type': 'SecurityError', 'error_msg': str(e)}
    except ResourceLimitError as e:
        payload = {'success': False, 'error_type': 'ResourceLimitError', 'error_msg': str(e)}
    except TimeoutError as e:
        payload = {'success': False, 'error_type': 'TimeoutError', 'error_msg': str(e)}
    except Exception as e:
        payload = {'success': False, 'error_type': type(e).__name__, 'error_msg': str(e)}
    finally:
        # Always write the result — even on error — so the parent can read it.
        try:
            with open(result_file, 'w', encoding='utf-8') as _f:
                json.dump(payload, _f)
        except Exception as _write_err:
            # Last-ditch: log to stderr (visible in the parent's stderr capture)
            import sys as _sys
            print(f"[sandbox] failed to write result file: {_write_err}", file=_sys.stderr)



class ToolSandbox:
    """
    Main sandbox class for executing tool code securely.
    """
    
    DEFAULT_TIMEOUT: float = 30.0  # seconds
    DEFAULT_MAX_OUTPUT_SIZE: int = 100 * 1024  # 100KB
    DEFAULT_MAX_MEMORY_MB: int = 100  # MB
    
    def __init__(self, 
                 timeout: Optional[float] = None,
                 max_output_size: Optional[int] = None,
                 allowed_file_dirs: Optional[List[str]] = None,
                 allowed_domains: Optional[Set[str]] = None,
                 read_only_files: bool = True,
                 max_memory_mb: Optional[int] = None,
                 permitted_environment: Optional[Dict[str, str]] = None):
        self.timeout = timeout or self.DEFAULT_TIMEOUT
        self.max_output_size = max_output_size or self.DEFAULT_MAX_OUTPUT_SIZE
        self.max_memory_mb = max_memory_mb or self.DEFAULT_MAX_MEMORY_MB
        self.allowed_file_dirs = allowed_file_dirs or []
        self.allowed_domains = allowed_domains
        self.read_only_files = read_only_files
        self.permitted_environment = {
            str(key): str(value)
            for key, value in (permitted_environment or {}).items()
        }
        
        # Build restricted globals
        self._globals = self._build_restricted_globals()


    
    def _build_restricted_globals(self) -> Dict[str, Any]:
        """Build a dictionary of restricted globals for sandboxed execution."""
        
        # Start with safe builtins
        safe_builtins = {}
        for name in dir(builtins):
            if (name not in DANGEROUS_BUILTINS
                    and name not in SANDBOX_BLOCKED_BUILTINS
                    and not name.startswith('_')):
                safe_builtins[name] = getattr(builtins, name)
        
        # Build safe substitutes for modules that are mocked rather than blocked
        safe_http_client = SafeHttpxClient(
            allowed_domains=self.allowed_domains,
            timeout=self.timeout
        )
        safe_env = SafeEnv(self.permitted_environment)

        # Add our restricted import with:
        #   - mock_modules: substitute objects returned for specific import names
        #   - passthrough_modules: namespaces passed through to the real importer
        safe_builtins['__import__'] = RestrictedImport(
            mock_modules={'httpx': safe_http_client, 'os': safe_env},
        )
        
        # Add restricted open if file access is needed
        if self.allowed_file_dirs:
            safe_builtins['open'] = SafeOpen(self.allowed_file_dirs, self.read_only_files)
        
        # Build the globals dict
        restricted_globals = {
            '__builtins__': safe_builtins,
            '__name__': '__sandbox__',
            '__doc__': None,
            '__package__': None,
        }
        
        # Pre-import safe modules
        for module_name in SAFE_MODULES:
            try:
                module = __import__(module_name)
                restricted_globals[module_name] = module
            except ImportError as _exc:
                logger.debug("ignored exception: %s", _exc)
        
        # Expose safe substitutes as top-level globals so tool code can use them
        # directly (without import) as well as via `import httpx` / `import os`
        restricted_globals['httpx'] = safe_http_client
        restricted_globals['os'] = safe_env
        
        # Add json module (commonly needed)
        restricted_globals['json'] = json
        
        return restricted_globals
    
    def _check_code_safety(self, code: str) -> None:
        """
        Static analysis to check for dangerous code patterns.
        """
        # Check for dangerous imports
        import_patterns = [
            # os is intentionally excluded: it is mocked with SafeEnv at runtime
            r'^\s*import\s+(sys|subprocess|socket|multiprocessing|ctypes|mmap)',
            r'^\s*from\s+(sys|subprocess|socket|multiprocessing|ctypes|mmap)\s+import',
            r'__import__\s*\(',
            r'importlib\s*\.\s*import_module',
        ]
        
        for pattern in import_patterns:
            if re.search(pattern, code, re.MULTILINE | re.IGNORECASE):
                match = re.search(pattern, code, re.MULTILINE | re.IGNORECASE)
                raise SecurityError(f"Dangerous import pattern detected: {match.group()}")
        
        # Check for dangerous builtins
        for dangerous in ['eval(', 'exec(', 'compile(', 'breakpoint(', 'open(']:
            if dangerous in code:
                # Allow open() if it's our SafeOpen (will be checked at runtime)
                if dangerous == 'open(' and self.allowed_file_dirs:
                    continue
                raise SecurityError(f"Dangerous builtin '{dangerous}' detected in code")
        
        # Check for file path traversal
        if '..' in code or '/etc/' in code or 'C:\\\\Windows' in code:
            raise SecurityError("Potential path traversal or system file access detected")
        
        # AST-based analysis to detect dangerous function calls (harder to bypass)
        try:
            import ast
            tree = ast.parse(code)
            dangerous_funcs = {'exec', 'eval', 'compile', 'breakpoint', 'open', '__import__'}
            for node in ast.walk(tree):
                # Even a reference to a reflection builtin is dangerous: it can be passed to a
                # helper such as functools.reduce and invoked with a computed attribute name.
                if isinstance(node, ast.Name) and node.id in SANDBOX_BLOCKED_BUILTINS:
                    raise SecurityError(
                        f"Reflective builtin '{node.id}' is not permitted in sandboxed code"
                    )
                # Dunder traversal (attribute, subscript key or getattr string) reaches the real
                # interpreter objects and escapes the sandbox.
                if isinstance(node, ast.Attribute) and node.attr in DANGEROUS_ATTRIBUTES:
                    raise SecurityError(f"Dangerous attribute access detected: .{node.attr}")
                if isinstance(node, ast.Attribute) \
                        and node.attr in SANDBOX_BLOCKED_ATTRIBUTES:
                    raise SecurityError(
                        f"Dangerous attribute helper detected: .{node.attr}"
                    )
                if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                        and isinstance(node.slice.value, str) \
                        and node.slice.value in DANGEROUS_ATTRIBUTES:
                    raise SecurityError(
                        f"Dangerous subscript access detected: [{node.slice.value!r}]")
                if isinstance(node, ast.Call):
                    # Check direct function calls like exec()
                    if isinstance(node.func, ast.Name) and node.func.id in dangerous_funcs:
                        # Allow open() if it's our SafeOpen (will be checked at runtime)
                        if node.func.id == 'open' and self.allowed_file_dirs:
                            continue
                        raise SecurityError(f"Dangerous call detected: {node.func.id}()")
                    # getattr(x, "__globals__") reaches the same object graph by a string name.
                    if isinstance(node.func, ast.Name) and node.func.id in ('getattr', 'setattr') \
                            and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                            and node.args[1].value in DANGEROUS_ATTRIBUTES:
                        raise SecurityError(
                            f"Dangerous {node.func.id} access detected: {node.args[1].value!r}")
                    # Check getattr(builtins, "exec")() style calls
                    if isinstance(node.func, ast.Attribute):
                        if node.func.attr in dangerous_funcs:
                            raise SecurityError(f"Dangerous call detected: .{node.func.attr}()")
        except SyntaxError as _exc:
            # If code can't be parsed, let it fail at execution time
            logger.debug("ignored exception: %s", _exc)
    
    def _execute_internal(self, code: str, local_vars: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Internal execution without multiprocessing - used by child process."""
        # Pre-execution safety checks
        self._check_code_safety(code)
        
        # Prepare execution environment
        exec_globals = self._globals.copy()
        if local_vars:
            exec_globals.update(local_vars)
        
        # Execute
        result_container = {'result': None, 'error': None, 'output': ''}
        
        try:
            exec(code, exec_globals)  # hygiene: allow isolated code sandbox execution
            result_container['result'] = exec_globals.get('result')
        except Exception as e:
            result_container['error'] = e
        
        # Check for errors
        if result_container['error']:
            raise result_container['error']
        
        # Check output size
        if result_container['result']:
            result_str = str(result_container['result'])
            if len(result_str) > self.max_output_size:
                raise ResourceLimitError(
                    f"Tool output size ({len(result_str)} chars) exceeds maximum ({self.max_output_size})"
                )
        
        return result_container

    def execute(self, code: str, local_vars: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Execute code in the sandboxed environment using multiprocessing for true isolation.
        
        Args:
            code: Python code to execute
            local_vars: Variables to inject into the local scope
            
        Returns:
            Dictionary containing execution results and any output variables
            
        Raises:
            SecurityError: If code violates security policy
            ResourceLimitError: If resource limits are exceeded
            TimeoutError: If execution exceeds time limit
        """
        # Pre-execution safety checks (in parent process)
        self._check_code_safety(code)
        
        # Use multiprocessing for true isolation and timeout enforcement.
        #
        # On Windows with ProactorEventLoop (asyncio), multiprocessing.Process
        # in spawn mode is unreliable: IOCP registers handles that interfere with
        # the spawning mechanism, and the Process object itself (including its
        # internal Popen/Connection state) may hold asyncio.Future references
        # that prevent pickling.
        #
        # Strategy:
        #   1. Strip _repl_state from local_vars["args"] — it's the parent's live
        #      runtime dict (contains asyncio objects) and is meaningless in a
        #      child process anyway.  RLM tools that need it run via a separate
        #      in-process path.
        #   2. Run Process.start()+join() inside a threading.Thread to stay off
        #      the event-loop thread (avoids WinError 6 handle-duplication error).
        #   3. IPC via temp JSON file instead of multiprocessing.Queue (Queue
        #      pipe handles also conflict with IOCP).
        #   4. If the worker still cannot start (e.g. a pickle error because the
        #      Popen/Connection object holds an asyncio.Future), fail closed with
        #      SandboxStartupError.  Static analysis is not process isolation, so tool
        #      code never runs inside the application process.
        import os as _os

        # --- Step 1: sanitise local_vars before pickling ---
        _local_vars_for_proc: dict = {}
        if local_vars:
            _local_vars_for_proc = dict(local_vars)
            if isinstance(_local_vars_for_proc.get("args"), dict):
                _tool_args = dict(_local_vars_for_proc["args"])
                _tool_args.pop("_repl_state", None)   # unpicklable; useless in child
                _local_vars_for_proc["args"] = _tool_args

        _fd, result_file = tempfile.mkstemp(suffix='.json')
        _os.close(_fd)

        _thread_exc: list = []
        _exitcode_holder: list = [None]

        def _spawn_and_join():
            try:
                _target = sys.modules[__name__]._execute_in_process
                p = Process(
                    target=_target,
                    args=(code, _local_vars_for_proc, result_file,
                          self.allowed_file_dirs, self.read_only_files,
                          self.allowed_domains, self.timeout,
                          self.max_output_size, self.max_memory_mb,
                          self.permitted_environment)
                )
                p.start()
                p.join(timeout=self.timeout)
                if p.is_alive():
                    logger.warning(
                        f"Tool execution exceeded {self.timeout}s limit, terminating"
                    )
                    p.terminate()
                    p.join()
                    _thread_exc.append(
                        TimeoutError(f"Tool execution exceeded {self.timeout} second limit")
                    )
                    return
                _exitcode_holder[0] = p.exitcode
            except Exception as _e:
                _thread_exc.append(_e)

        _t = threading.Thread(target=_spawn_and_join, daemon=True)
        _t.start()
        # Allow a small buffer beyond the tool timeout for process overhead
        _t.join(timeout=self.timeout + 10)

        _subprocess_err: Exception | None = None
        try:
            if _t.is_alive():
                raise TimeoutError(
                    f"Tool execution exceeded {self.timeout} second limit"
                )

            if _thread_exc:
                _subprocess_err = _thread_exc[0]
                raise _subprocess_err

            if _exitcode_holder[0] is not None and _exitcode_holder[0] != 0:
                logger.error(f"Tool process exited with code {_exitcode_holder[0]}")
                raise Exception(
                    f"Tool execution failed with exit code {_exitcode_holder[0]}"
                )

            # Read result from temp file written by child process
            try:
                with open(result_file, 'r', encoding='utf-8') as _f:
                    result = json.load(_f)
            except Exception:
                raise Exception("Tool execution failed to return result")
        except (TypeError, AttributeError) as _spawn_err:
            # Never execute untrusted code inside the application process. A worker that cannot
            # start is reported as a failed execution so the caller can fix the environment.
            raise SandboxStartupError(
                f"Sandbox worker could not start; refusing in-process execution: {_spawn_err}"
            ) from _spawn_err
        finally:
            try:
                _os.unlink(result_file)
            except Exception as _exc:
                logger.debug("ignored exception: %s", _exc)
        
        # Check if result contains an error
        if isinstance(result, dict):
            if not result.get('success', False):
                error_type = result.get('error_type', 'Exception')
                error_msg = result.get('error_msg', 'Unknown error')
                
                # Re-raise as appropriate exception type
                if error_type == 'SecurityError':
                    raise SecurityError(error_msg)
                elif error_type == 'ResourceLimitError':
                    raise ResourceLimitError(error_msg)
                elif error_type == 'TimeoutError':
                    raise TimeoutError(error_msg)
                else:
                    raise Exception(f"{error_type}: {error_msg}")
            
            # Return successful result
            return {'result': result.get('result')}
        
        return result




# Convenience function for simple sandboxed execution
def execute_sandboxed(code: str, 
                     args: Dict[str, Any],
                     timeout: float = 30.0,
                     allowed_domains: Optional[Set[str]] = None) -> Any:
    """
    Execute tool code in a sandbox with the given arguments.
    
    Args:
        code: Python code to execute
        args: Arguments to pass to the tool (available as 'args' variable)
        timeout: Maximum execution time in seconds
        allowed_domains: Set of allowed domains for HTTP requests
        
    Returns:
        The 'result' variable from the executed code
    """
    sandbox = ToolSandbox(timeout=timeout, allowed_domains=allowed_domains)
    result = sandbox.execute(code, {'args': args})
    return result['result']
