# 🛠️ NeuroCore Tool Library Guide

> **Empower your AI agents with custom Python functions through OpenAI-compatible Function Calling**

---

## 📑 Table of Contents

1. [What is a Tool?](#1-what-is-a-tool)
2. [Architecture Overview](#2-architecture-overview)
3. [Built-in Tools Reference](#3-built-in-tools-reference)
4. [Creating Custom Tools](#4-creating-custom-tools)
5. [Writing Python Logic](#5-writing-python-logic)
6. [Using Tools in AI Flows](#6-using-tools-in-ai-flows)
7. [Advanced Features](#7-advanced-features)
8. [Importing and Exporting](#8-importing-and-exporting)
9. [Best Practices](#9-best-practices)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. What is a Tool?

A **Tool** is a bridge between your AI agent and the external world. It consists of two essential parts:

| Component | Purpose | Format |
|-----------|---------|--------|
| **Definition** | Describes the function to the LLM so it knows *how* and *when* to call it | JSON Schema |
| **Implementation** | The actual Python code that executes when the tool is called | Python Code |

### Tool Execution Flow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   User      │────▶│  LLM Core   │────▶│  Tool Call  │────▶│  Tool       │
│   Request   │     │  (decides)  │     │  Request    │     │  Dispatcher │
└─────────────┘     └─────────────┘     └─────────────┘     └──────┬──────┘
                                                                     │
                              ┌────────────────────────────────────┘
                              ▼
                       ┌─────────────┐
                       │  Python     │
                       │  Execution  │
                       └──────┬──────┘
                              │
                              ▼
                       ┌─────────────┐     ┌─────────────┐
                       │  Result     │────▶│  LLM        │
                       │  Return     │     │  Response   │
                       └─────────────┘     └─────────────┘
```

---

## 2. Architecture Overview

The Tool Library is built on these core components:

- **Tool Definitions** (`modules/tools/tools.json`): Stores tool metadata and JSON schemas. `tools.json.lock` is a runtime lock file used during concurrent writes — do not delete it manually.
- **Library Directory** (`modules/tools/library/`): Contains standard Python implementation files (16 standard tools)
- **RLM Library** (`modules/tools/rlm_library/`): Contains Recursive Language Model tool implementations (7 RLM tools)
- **Tool Dispatcher Node** (`tool_dispatcher`): Executes tool calls within AI Flows
- **System Prompt Integration**: Injects available tools into LLM context
- **Sandbox** (`modules/tools/sandbox.py`): Restricted execution environment for all tool code

---

## 3. Built-in Tools Reference

NeuroCore comes with **23 built-in tools**: 16 standard tools in `library/` and 7 RLM tools in `rlm_library/`.

### 🌤️ Information & Utilities

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| **Weather** | Get current weather for any location | `location` (string) |
| **SystemTime** | Get current system date and time | None |
| **TimeZoneConverter** | Convert times between time zones | `source_tz`, `target_tz`, `time_string` |
| **ConversionCalculator** | Convert between units (temp, length, weight, volume) | `value`, `from_unit`, `to_unit` |
| **Calculator** | Evaluate mathematical expressions | `expression` (string) |

### 🌐 Web & Search

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| **FetchURL** | Fetch raw text content from any URL | `url` (string) |
| **WikipediaLookup** | Search and retrieve Wikipedia articles | `query`, `mode` (summary/full) |
| **ArXivSearch** | Search academic papers on ArXiv | `query`, `max_results` |
| **YouTubeTranscript** | Fetch video transcripts | `url` (YouTube URL) |
| **CurrencyConverter** | Real-time currency conversion | `amount`, `from_currency`, `to_currency` |

### 📅 Calendar & Reminders

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| **SaveReminder** | Save calendar events and reminders | `title`, `time` (ISO 8601) |
| **CheckCalendar** | Check calendar for events | `date` (YYYY-MM-DD, optional) |

### 📧 Communication

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| **SendEmail** | Send emails via SMTP | `to_email`, `subject`, `body` |

### 🎯 Goals Management

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| **SetGoal** | Create new goals for the agent | `description`, `priority`, `deadline` |
| **MarkGoalComplete** | Mark goals as completed | `goal_id` (number) |
| **DeleteGoal** | Remove goals from system | `goal_id` (number) |

### 🧠 RLM Tools (`rlm_library/`)
Used by agents for memory manipulation, variable storage, and recursive sub-calls when processing long or complex inputs.

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| **Peek** | View a slice of the prompt | `start`, `end` |
| **Search** | Find regex matches in prompt | `pattern`, `max_results` |
| **Chunk** | Split prompt into chunks | `size`, `overlap` |
| **SubCall** | Recursively call an LLM | `prompt`, `model`, `max_tokens` |
| **SetVariable** | Store intermediate results | `name`, `value` |
| **GetVariable** | Retrieve stored results | `name` |
| **SetFinal** | Set final answer and terminate | `value` |

### Detailed Tool Schemas

#### Weather
```json
{
  "type": "object",
  "properties": {
    "location": {
      "type": "string",
      "description": "The city and state, e.g. San Francisco, CA"
    }
  },
  "required": ["location"]
}
```

#### Calculator
```json
{
  "type": "object",
  "properties": {
    "expression": {
      "type": "string",
      "description": "The mathematical expression to evaluate (e.g., '2 + 2', 'sqrt(16)', 'sin(pi/2)')"
    }
  },
  "required": ["expression"]
}
```

#### TimeZoneConverter
```json
{
  "type": "object",
  "properties": {
    "time_string": {
      "type": "string",
      "description": "The date and time to convert (ISO 8601 format). If omitted, uses current system time."
    },
    "source_tz": {
      "type": "string",
      "description": "The source time zone (IANA format, e.g., 'America/New_York', 'UTC')"
    },
    "target_tz": {
      "type": "string",
      "description": "The target time zone (IANA format, e.g., 'Europe/London', 'Asia/Tokyo')"
    }
  },
  "required": ["source_tz", "target_tz"]
}
```

#### ConversionCalculator
```json
{
  "type": "object",
  "properties": {
    "value": {
      "type": "number",
      "description": "The numerical value to convert"
    },
    "from_unit": {
      "type": "string",
      "description": "The unit to convert from (e.g., 'meters', 'celsius', 'lbs')"
    },
    "to_unit": {
      "type": "string",
      "description": "The unit to convert to (e.g., 'feet', 'fahrenheit', 'kg')"
    }
  },
  "required": ["value", "from_unit", "to_unit"]
}
```

#### FetchURL
```json
{
  "type": "object",
  "properties": {
    "url": {
      "type": "string",
      "description": "The URL to fetch content from"
    }
  },
  "required": ["url"]
}
```

#### CurrencyConverter
```json
{
  "type": "object",
  "properties": {
    "amount": {
      "type": "number",
      "description": "The amount to convert"
    },
    "from_currency": {
      "type": "string",
      "description": "Source currency code (e.g., USD, EUR, JPY)"
    },
    "to_currency": {
      "type": "string",
      "description": "Target currency code (e.g., EUR, GBP, CAD)"
    }
  },
  "required": ["amount", "from_currency", "to_currency"]
}
```

#### WikipediaLookup
```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "description": "The search term or topic"
    },
    "mode": {
      "type": "string",
      "enum": ["summary", "full"],
      "description": "Whether to retrieve just the summary or full article text"
    }
  },
  "required": ["query"]
}
```

#### ArXivSearch
```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "description": "The search query (keywords, topics, paper titles)"
    },
    "max_results": {
      "type": "number",
      "description": "Maximum number of results to return (default 5, max 20)"
    }
  },
  "required": ["query"]
}
```

#### YouTubeTranscript
```json
{
  "type": "object",
  "properties": {
    "url": {
      "type": "string",
      "description": "The URL of the YouTube video"
    }
  },
  "required": ["url"]
}
```

#### SaveReminder
```json
{
  "type": "object",
  "properties": {
    "title": {
      "type": "string",
      "description": "The title of the event"
    },
    "time": {
      "type": "string",
      "description": "The date and time (ISO 8601 preferred). If omitted, uses current time"
    }
  },
  "required": ["title"]
}
```

#### CheckCalendar
```json
{
  "type": "object",
  "properties": {
    "date": {
      "type": "string",
      "description": "The date to check (YYYY-MM-DD). If omitted, returns upcoming events"
    }
  },
  "required": []
}
```

#### SendEmail
```json
{
  "type": "object",
  "properties": {
    "to_email": {
      "type": "string",
      "description": "The recipient's email address"
    },
    "subject": {
      "type": "string",
      "description": "The subject of the email"
    },
    "body": {
      "type": "string",
      "description": "The content of the email"
    }
  },
  "required": ["to_email", "subject", "body"]
}
```

#### SetGoal
```json
{
  "type": "object",
  "properties": {
    "description": {
      "type": "string",
      "description": "The goal description - what should be achieved"
    },
    "priority": {
      "type": "number",
      "description": "Priority level (higher = more important, default 0)"
    },
    "deadline": {
      "type": "number",
      "description": "Optional deadline as Unix timestamp"
    }
  },
  "required": ["description"]
}
```

#### MarkGoalComplete / DeleteGoal
```json
{
  "type": "object",
  "properties": {
    "goal_id": {
      "type": "number",
      "description": "The ID of the goal"
    }
  },
  "required": ["goal_id"]
}
```

#### Peek (RLM)
```json
{
  "type": "object",
  "properties": {
    "start": {
      "type": "number",
      "description": "Start character position (inclusive)."
    },
    "end": {
      "type": "number",
      "description": "End character position (exclusive)."
    }
  },
  "required": ["start", "end"]
}
```

#### Search (RLM)
```json
{
  "type": "object",
  "properties": {
    "pattern": {
      "type": "string",
      "description": "The regex pattern to search for."
    },
    "max_results": {
      "type": "number",
      "description": "Maximum number of matches to return (default 20)."
    }
  },
  "required": ["pattern"]
}
```

#### SubCall (RLM)
```json
{
  "type": "object",
  "properties": {
    "prompt": {
      "type": "string",
      "description": "The prompt to send to the sub-call LLM."
    },
    "model": {
      "type": "string",
      "description": "Optional model to use for sub-call."
    }
  },
  "required": ["prompt"]
}
```

---

## 4. Creating Custom Tools

Navigate to **Settings** -> **Tools** in the NeuroCore dashboard. Click **+ New Tool**.

### Required Fields

| Field | Description | Tips |
|-------|-------------|------|
| **Function Name** | Unique identifier (snake_case) | Use descriptive names like `get_weather`, `search_database` |
| **Description** | Clear explanation for the LLM | Be specific: "Calculates square root" not "Math function" |
| **Parameters** | JSON Schema object | Define types, descriptions, and required fields |
| **Python Logic** | Implementation code | Use `args` for input, `result` for output |

### Parameter Schema Example

```json
{
  "type": "object",
  "properties": {
    "location": { 
      "type": "string", 
      "description": "City and state, e.g. San Francisco, CA" 
    },
    "unit": { 
      "type": "string", 
      "enum": ["celsius", "fahrenheit"],
      "description": "Temperature unit"
    }
  },
  "required": ["location"]
}
```

---

## 5. Writing Python Logic

Tool code runs inside the **NeuroCore sandbox** — a restricted Python execution environment. Understanding what is and is not available is essential to writing working tools.

### Pre-injected Globals

These names are available in every tool without any `import` statement:

| Name | Type | Description |
|------|------|-------------|
| `args` | `dict` | Arguments provided by the LLM call |
| `result` | (assign to this) | Set this variable to return a value |
| `json` | module | Standard `json` module |
| `httpx` | `SafeHttpxClient` | Domain-restricted HTTP client (see below) |
| `os` | `SafeEnv` | Environment variable access only (see below) |

You can also use `import httpx` or `import os` at the top of your code — both resolve to the same safe substitutes listed above.

### Allowed Standard Library Imports

The following standard library modules can be imported freely:

```
math, random, datetime, time, calendar, decimal, fractions, numbers,
statistics, itertools, functools, operator, copy, string, re,
collections, enum, typing, hashlib, base64, binascii, uuid, json,
html, html.entities, html.parser, urllib.parse, textwrap, unicodedata,
stringprep, codecs, dataclasses, abc, contextlib, contextvars, types,
weakref, array, bisect, heapq, copyreg, ast, zoneinfo,
smtplib, ssl, email, email.mime.text, email.mime.multipart
```

Additionally, `youtube_transcript_api` is importable if the package is installed.

### `httpx` — Restricted HTTP Client

`httpx` inside the sandbox is a `SafeHttpxClient`, not the real `httpx` library. It enforces:

- **Domain allowlist** — only requests to approved domains succeed. The default allowlist includes:
  - Public weather/data APIs: `wttr.in`, `api.weatherapi.com`, `api.frankfurter.app`
  - Research/media: `en.wikipedia.org`, `export.arxiv.org`, `www.youtube.com`
  - LLM providers: `api.openai.com`, `api.anthropic.com`, `api.mistral.ai`, and others
  - Internal server: `127.0.0.1`, `localhost` (for same-host module APIs)
  - Code hosting: `github.com`, `raw.githubusercontent.com`
- **SSRF protection** — blocks requests to private IP ranges (10.x, 192.168.x, 172.16–31.x, 169.254.x)
- **Response size limit** — responses over 10 MB are rejected
- **Timeout** — capped at the sandbox timeout (default 30 s)

Available methods: `get`, `post`, `put`, `patch`, `delete` (same signature as real `httpx`).

Requests to domains not in the allowlist raise `SecurityError`. If your tool needs a new domain, add it to `SafeHttpxClient.DEFAULT_ALLOWED_DOMAINS` in `modules/tools/sandbox.py`.

```python
# Both of these work identically — httpx is already injected as a global
import httpx
resp = httpx.get("https://wttr.in/London?format=j1", timeout=10)
data = resp.json()
```

### `os` — Environment Variables Only

`os` inside the sandbox is a `SafeEnv` mock. It sees **only** the variables named in the
`env_allowlist` list of `config` in `modules/tools/module.json`; everything else in the host
environment (API keys, bot tokens, cloud credentials) is invisible to tools. For example:

```json
"config": { "env_allowlist": ["MY_API_KEY"] }
```

Only two things work:

```python
import os

# Read a permitted environment variable (returns None or default if not set or not permitted)
api_key = os.getenv("MY_API_KEY", "default")

# Read via environ
api_key = os.environ.get("MY_API_KEY")
```

Accessing any other `os` attribute (`os.path`, `os.system`, `os.listdir`, etc.) raises `SecurityError`. Use environment variables for secrets and configuration; never hardcode credentials in tool code.

### NeuroCore Internal Imports (`modules.*`)

Tools can import from NeuroCore's own package. This is how built-in tools access live app singletons:

```python
from modules.calendar.events import event_manager
events = event_manager.get_upcoming(limit=5)
```

This pass-through is intentional — built-in library tools are first-party trusted code. Custom tools should generally not need internal imports; use the HTTP API (`httpx` → `localhost`) instead.

### Blocked Modules

The following modules are **always blocked**, even if installed:

```
os (real module)*, sys, subprocess, socket, multiprocessing, threading,
ctypes, mmap, resource, signal, pickle, cPickle, marshal, imp, importlib,
site, warnings, traceback, gc, inspect, code, pdb, bdb,
shutil, tempfile, pathlib, glob, fnmatch, posix, nt
```

\* `import os` is allowed but returns `SafeEnv` (not the real `os` module).

Attempting to import a blocked module raises `SecurityError` immediately.

### Unknown / Third-Party Modules

Importing a module that is not in the allowlist and not in the blocklist raises `ImportError` (not `SecurityError`). This means tools can use a graceful fallback:

```python
try:
    import some_optional_library
    result = some_optional_library.do_thing(args['input'])
except ImportError:
    result = "Error: some_optional_library is not installed."
```

### Blocked Builtins

The following built-in functions are removed from the sandbox environment:

`eval`, `exec`, `compile`, `open` (unless file dirs are explicitly allowed), `__import__`, `breakpoint`, `input`, `help`, `quit`, `exit`

### Complete Example: Weather Tool

**Parameters:**
```json
{
  "type": "object",
  "properties": {
    "location": { "type": "string" }
  },
  "required": ["location"]
}
```

**Python Code:**
```python
from urllib.parse import quote

location = args.get('location')
if not location:
    result = "Error: No location provided."
else:
    try:
        # httpx is pre-injected as a SafeHttpxClient — wttr.in is in the domain allowlist
        resp = httpx.get(f"https://wttr.in/{quote(location)}?format=j1", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        current = data['current_condition'][0]
        result = f"{current['temp_C']}°C, {current['weatherDesc'][0]['value']}"
    except Exception as e:
        result = f"Failed to fetch weather: {e}"
```

### Advanced Python Patterns

#### Handling Optional Parameters
```python
limit = args.get('limit', 10)
offset = args.get('offset', 0)
```

#### Type Conversion
```python
count = int(args.get('count', 0))
enabled = bool(args.get('enabled', False))
```

#### Working with Lists
```python
items = args.get('items', [])
if not isinstance(items, list):
    items = [items]  # Normalize single item to list
```

#### Reading Configuration from Environment
```python
import os
# Set it before starting NeuroCore and list it in env_allowlist (modules/tools/module.json)
api_key = os.getenv("MY_SERVICE_API_KEY")
if not api_key:
    result = "Error: MY_SERVICE_API_KEY environment variable is not set."
```

---

## 6. Using Tools in AI Flows

To enable tool usage, configure the **System Prompt** and add a **Tool Dispatcher**.

### Step 1: Enable Tools in System Prompt

1. Open your AI Flow
2. Select the **System Prompt** node
3. In the configuration panel, check the tools you want to enable
4. This injects tool definitions into the LLM context

### Step 2: Add the Tool Dispatcher Node

The LLM requests tool calls, but cannot execute code itself. The **Tool Dispatcher** handles execution.

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────┐
│  System     │────▶│     LLM Core     │────▶│ Conditional │
│  Prompt     │     │  (with tools)    │     │   Router    │
│  (+tools)   │     └──────────────────┘     └──────┬──────┘
└─────────────┘                                     │
                              ┌──────────────────────┘
                              │ check_field: "tool_calls"
                              ▼
                       ┌─────────────┐     ┌─────────────┐
                       │   Tool      │────▶│    LLM      │
                       │  Dispatcher │     │   (interpret)│
                       └─────────────┘     └─────────────┘
```

**Configuration Steps:**

1. Add a **Tool Dispatcher** node to your flow
2. Connect **LLM Core** output to **Tool Dispatcher** input
3. Use a **Conditional Router** to check if `tool_calls` exists
4. Connect **Tool Dispatcher** output back to **LLM Core** for result interpretation

### Tool Dispatcher Configuration

| Option | Description | Default |
|--------|-------------|---------|
| `allowed_tools` | List of permitted tools (empty = all) | `[]` |
| `max_tools_per_turn` | Maximum tools per execution | `5` |

---

## 7. Advanced Features

### Tool Configuration

Some tools support additional configuration:

#### SendEmail SMTP Settings
Configure via environment variables or the tool config UI:
- `SMTP_SERVER` - SMTP server hostname
- `SMTP_PORT` - Server port (default: 587)
- `SMTP_EMAIL` - Sender email address
- `SMTP_PASSWORD` - Authentication password

### Error Handling Strategies

The Tool Dispatcher provides robust error handling:

| Error Type | Behavior |
|------------|----------|
| JSON Parse Error | Returns error message to LLM |
| Tool Not Found | Returns "Tool not found" error |
| Execution Error | Returns exception details |
| Tool Not Allowed | Returns "Tool not enabled" error |

### Rate Limiting

Control tool execution with `max_tools_per_turn`:
- Prevents runaway tool loops
- Remaining tools are queued for next turn
- Tracked via `_tool_count` and `_remaining_tool_calls`

### Sandbox Security Layers

All tool code (standard and custom) executes in `modules/tools/sandbox.py` with these protections:

| Layer | Mechanism |
|-------|-----------|
| Static Analysis | Regex scanning for dangerous patterns (`subprocess`, `sys`, `ctypes`, etc.) before execution |
| Import Blocklist | `RestrictedImport` blocks `sys`, `subprocess`, `socket`, `shutil`, `importlib`, `pickle`, `ctypes`, `mmap`, `multiprocessing`, `pathlib`, and 15+ more |
| Module Mocking | `import httpx` → `SafeHttpxClient`; `import os` → `SafeEnv`. Real modules are never exposed. |
| Module Passthrough | `modules.*` imports pass through to the real NeuroCore package (first-party trusted code) |
| Restricted Builtins | Removes `eval`, `exec`, `open`, `__import__`, `compile`, `breakpoint`, `input` |
| File Access Control | `SafeOpen` — file reads only allowed if specific directories are whitelisted |
| Network Restrictions | `SafeHttpxClient` enforces a domain allowlist; requests to non-listed domains raise `SecurityError` |
| SSRF Protection | Resolves hostnames via DNS and rejects requests to private/internal IP ranges |
| Resource Limits | Timeout (default 30 s), max output size (100 KB), optional memory limit |

**Error types raised by the sandbox:**

| Error | Meaning |
|-------|---------|
| `SecurityError` | Import of a blocked module, access to a blocked `os` attribute, request to a non-whitelisted domain |
| `ImportError` | Import of an unknown module (not blocked, not in allowlist) — allows `except ImportError` fallbacks |
| `ResourceLimitError` | Timeout, output size, or memory limit exceeded |

These are caught by the Tool Dispatcher and returned to the LLM as error strings so the agent can react gracefully.

---

## 8. Importing and Exporting

Share tools between NeuroCore instances, back them up, or download from the Community Marketplace.

### Marketplace Import

Tools published to the **Community Marketplace** can be imported directly from the item detail page. Clicking **Import to NeuroCore** on a tool item:

1. Downloads the tool file (`.json` or `.py`)
2. Parses the definition and code
3. Registers the tool in `modules/tools/tools.json` using `filelock` for thread safety
4. Writes the implementation to `modules/tools/library/{name}.py`
5. The tool is immediately available in the Tools settings page and in flow editors

The import button changes to **Imported** after a successful import and shows **Update Available** if the marketplace version is newer than the local one.

> **Originality note:** Tools that were themselves imported from the marketplace are tagged as `marketplace_import` in their metadata. The marketplace upload form will reject re-uploading these files unchanged to prevent duplication.

### Exporting Tools

| Method | Action | Output |
|--------|--------|--------|
| Single Tool | Click **Export Tool** | `{name}.json` |
| Entire Library | Click **Export Entire Library** | `neurocore_tools.json` |

**Export Format:**
```json
[
  {
    "name": "Weather",
    "enabled": true,
    "description": "Get current weather...",
    "parameters": { ... },
    "code": "import httpx\n..."
  }
]
```

### Importing Tools

| File Type | Handling |
|-----------|----------|
| `.json` | Full tool definition with code |
| `.py` | Wrapped as tool (adjust schema manually) |

**Import Steps:**
1. Click **Import Tool**
2. Select `.json` or `.py` file
3. Review and adjust parameter schema if needed
4. Save

---

## 9. Best Practices

### Writing Good Tool Descriptions

✅ **Good Examples:**
- "Fetches current weather conditions including temperature, humidity, and forecast"
- "Searches the product database by name and returns matching items with price and availability"
- "Converts between 50+ currencies using real-time exchange rates"

❌ **Bad Examples:**
- "Weather tool"
- "Database search"
- "Money converter"

### Parameter Design

1. **Use clear, descriptive names** - `location` not `loc`
2. **Provide examples in descriptions** - "e.g., San Francisco, CA"
3. **Mark truly required fields** - Don't overuse `required`
4. **Use enums for limited options** - `["celsius", "fahrenheit"]`
5. **Set sensible defaults** - Handle missing optional parameters gracefully

### Security Considerations

- Use `allowed_tools` in Tool Dispatcher to restrict access
- Validate all inputs before processing
- Never expose sensitive credentials in tool code
- Use environment variables for API keys
- Sanitize user inputs to prevent injection attacks

### Performance Tips

- Keep tool execution fast (< 5 seconds ideal)
- Use timeouts for external API calls
- Cache results when appropriate
- Handle rate limits gracefully

---

## 10. Troubleshooting

### Common Issues

| Symptom | Cause | Solution |
|---------|-------|----------|
| LLM doesn't call tools | Tools not enabled in System Prompt | Check tool checkboxes in System Prompt config |
| "Tool not found" error | Tool name mismatch | Verify function name matches definition |
| JSON parse errors | Malformed LLM arguments | Tool Dispatcher handles this automatically |
| Tool executes but no result | Missing `result` assignment | Ensure `result = ...` in Python code |
| Import fails | Invalid JSON or encoding | Validate JSON syntax, check file encoding |
| `Import of module 'X' is not permitted` | Module not in sandbox allowlist | Add it to `SAFE_MODULES` in `sandbox.py`, or use an HTTP API via `httpx` instead |
| `Import of module 'X' is not allowed` | Module is in the danger blocklist | Use a safe alternative; blocked modules cannot be enabled |
| HTTP request blocked (domain not whitelisted) | `SafeHttpxClient` domain check | Add the domain to `SafeHttpxClient.DEFAULT_ALLOWED_DOMAINS` in `sandbox.py` |
| `os.X is not permitted` | Accessing a blocked `os` attribute | Only `os.getenv()` and `os.environ.get()` are available, for variables listed in `env_allowlist` |
| Tool times out | Slow external API or infinite loop | Add timeouts to HTTP calls; check for logic errors |

### Debugging Steps

1. **Check Tool Definitions**
   ```bash
   cat modules/tools/tools.json | jq '.'
   ```

2. **Verify Python Code**
   - Check syntax with `python -m py_compile library/{tool_name}.py`
   - Ensure `result` variable is assigned

3. **Test in Isolation**
   - Create a simple flow with just System Prompt → LLM Core → Tool Dispatcher
   - Enable only one tool at a time

4. **Review Logs**
   - Check console output for execution errors
   - Look for `Error executing tool` messages

### Getting Help

- Check the [NeuroCore Documentation](https://docs.neurocore.ai)
- Review example tools in `modules/tools/library/`
- Enable debug mode in settings for verbose logging

---

> 💡 **Pro Tip:** Start with built-in tools to understand patterns, then create custom tools for your specific use cases!
