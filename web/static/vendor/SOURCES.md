# Vendored front-end scripts (M-SEC-7)

Pinned copies — re-download only to bump versions, then update the table.

| File | Upstream URL | Version banner | SHA-256 |
|---|---|---|---|
| `htmx-2.0.2.min.js` | `https://unpkg.com/htmx.org@2.0.2/dist/htmx.min.js` | htmx 2.0.2 | `e1746d9759ec0d43c5c284452333a310bb5fd7285ebac4b2dc9bf44d72b5a887` |
| `Sortable-1.15.2.min.js` | `https://cdn.jsdelivr.net/npm/sortablejs@1.15.2/Sortable.min.js` | Sortable 1.15.2 MIT | `ca68430703c4f5960e90735867c6e94d29b5a3de37107d8100e5a301007e9e6e` |
| `marked-12.0.2.min.js` | `https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js` | marked v12.0.2 | `15fabce5b65898b32b03f5ed25e9f891a729ad4c0d6d877110a7744aa847a894` |
| `purify-3.1.6.min.js` | `https://cdn.jsdelivr.net/npm/dompurify@3.1.6/dist/purify.min.js` | DOMPurify 3.1.6, Cure53 | `c0845096a7c4a6741f362ac506c94c1c7d27dc603bcc1bf64a587f76f2dbe3a1` |

Full hashes: recompute with
`python -c "import hashlib; print(hashlib.sha256(open('web/static/vendor/<file>','rb').read()).hexdigest())"`
and compare against the upstream release before upgrading.

Not vendored: the Tailwind Play CDN (`https://cdn.tailwindcss.com`), which
compiles utilities in the browser and cannot be vendored without adding a
CSS build step. It remains the only remote script host allowed by the
Content-Security-Policy.
