"""Send a request to the exact address an SSRF check approved (IDEA-063).

Checking a hostname's resolved addresses and then handing the hostname to httpx resolves it twice:
a DNS answer that changes between the two (rebinding) reaches a private address the check
rejected. A pinned request connects to the vetted IP, carries the hostname in the ``Host`` header,
and passes it to TLS as ``sni_hostname`` so the certificate is still verified against the name.
"""

import ipaddress
from typing import Dict, Tuple
from urllib.parse import urlsplit, urlunsplit


def pinned_target(url: str, address: str) -> Tuple[str, Dict[str, str], Dict[str, str]]:
    """``(url on the address, headers, request extensions)`` for fetching ``url`` from ``address``.

    Credentials in the URL's user-info are dropped: neither caller sends them.
    """
    parts = urlsplit(url)
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    host = f"[{ip}]" if ip.version == 6 else str(ip)
    port = f":{parts.port}" if parts.port else ""
    pinned = urlunsplit((parts.scheme, host + port, parts.path, parts.query, parts.fragment))
    extensions = {"sni_hostname": parts.hostname} if parts.scheme == "https" else {}
    return pinned, {"Host": f"{parts.hostname}{port}"}, extensions
