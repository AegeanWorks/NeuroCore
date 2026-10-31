import re
from urllib.parse import urlparse

url = args.get('url')

if not url:
    result = "Error: No URL provided."
else:
    try:
        # Validate URL scheme
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            result = "Error: Only http/https URLs are allowed."
        else:
            hostname = parsed.hostname or ""

            # SSRF Protection: Block private/internal hostnames using pattern matching.
            # We avoid socket.gethostbyname() because `socket` is blocked by the sandbox.
            # This covers the most common SSRF vectors without a DNS lookup.
            blocked_hostname_patterns = [
                r'^localhost$',
                r'^127\.',
                r'^10\.',
                r'^172\.(1[6-9]|2[0-9]|3[0-1])\.',
                r'^192\.168\.',
                r'^169\.254\.',
                r'^0\.',
                r'^::1$',
                r'^fc00:',
                r'^fe80:',
                r'\.internal$',
                r'\.local$',
            ]

            blocked = False
            for pattern in blocked_hostname_patterns:
                if re.match(pattern, hostname, re.IGNORECASE):
                    result = "Error: Request to private/internal addresses is not allowed."
                    blocked = True
                    break

            if not blocked:
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
                resp = httpx.get(url, headers=headers, timeout=10, follow_redirects=True)
                resp.raise_for_status()

                # Simple HTML tag stripping
                text = resp.text
                text = re.sub(r'<script.*?>.*?</script>', '', text, flags=re.DOTALL)
                text = re.sub(r'<style.*?>.*?</style>', '', text, flags=re.DOTALL)
                text = re.sub(r'<[^>]+>', ' ', text)
                text = ' '.join(text.split())

                # Truncate if too long to prevent context overflow
                max_len = 2000
                if len(text) > max_len:
                    text = text[:max_len] + " [content truncated]"

                result = text
    except Exception as e:
        if 'result' in dir() and isinstance(result, str) and result.startswith("Error:"):
            pass  # Keep the existing error message
        else:
            result = f"Error fetching URL: {str(e)}"
