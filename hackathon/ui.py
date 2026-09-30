"""Local approval screen: shows the exact review hash and records the human decision.

Binds to 127.0.0.1 only. The reviewer must type the displayed SHA-256 and their
name; a mismatch is rejected. On approval a signed bundle is written. The name is
self-asserted; this screen does not authenticate the person.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from .bundle import write_bundle
from .canonical import sha256
from .schemas import HumanApproval, ReviewPackage
from .workflow import ApprovalError, approve, finalize

PAGE = """<!doctype html><meta charset="utf-8"><title>Human approval</title>
<style>body{{font:16px system-ui;max-width:46rem;margin:2rem auto;padding:0 1rem}}
code{{word-break:break-all;background:#eef;padding:.2rem}}.bad{{color:#b00}}.ok{{color:#070}}
td,th{{text-align:left;padding:.2rem .6rem;vertical-align:top}}</style>
<h1>Human approval</h1>{message}
<h2>Transaction</h2><p>{transaction}</p>
<h2>Agent verdicts</h2><table><tr><th>Role<th>Verdict<th>Rationale</tr>{rows}</table>
<h2>Review SHA-256</h2><p><code>{review_hash}</code></p>
<p>Approvals so far ({count} of {required}): {names}</p>
<form method="post"><p><label>Type the SHA-256 above to confirm you reviewed exactly this:<br>
<input name="sha" size="70" autocomplete="off"></label></p>
<p><label>Your name: <input name="name"></label></p>
<button>Approve</button></form>"""


def render_page(
    review: ReviewPackage,
    review_hash: str,
    message: str = "",
    approvals: list[HumanApproval] | None = None,
    required: int = 1,
) -> str:
    approvals = approvals or []
    tx = review.transaction
    rows = "".join(
        f"<tr><td>{html.escape(v.agent_role)}<td>{html.escape(v.verdict)}"
        f"<td>{html.escape(v.rationale)}</tr>"
        for v in review.verdicts
    )
    return PAGE.format(
        message=message,
        transaction=html.escape(
            f"{tx.amount} {tx.asset} to {tx.destination}: {tx.purpose} ({tx.transaction_id})"
        ),
        rows=rows,
        review_hash=review_hash,
        count=len(approvals),
        required=required,
        names=html.escape(", ".join(a.approver for a in approvals) or "none yet"),
    )


def handle_approval(
    review: ReviewPackage,
    form: dict[str, str],
    out_dir: Path,
    private_key,
    now: datetime | None = None,
    state: list[HumanApproval] | None = None,
    required: int = 1,
) -> tuple[bool, str]:
    """Process one submitted form. Returns (bundle_written, message). Fails closed.

    ``state`` collects approvals across submissions; the bundle is written only when
    ``required`` distinct approvers have each confirmed the exact review hash.
    """
    state = state if state is not None else []
    name = form.get("name", "").strip()
    typed = form.get("sha", "").strip().lower()
    try:
        approval = approve(review, typed, name, now or datetime.now(UTC))
        if any(a.approver.strip().lower() == name.lower() for a in state):
            raise ApprovalError("this approver has already confirmed")
        collected = [*state, approval]
        if len(collected) < required:
            state.append(approval)
            return False, f"Recorded {name}. {len(state)} of {required} approvals so far."
        manifest, manifest_hash = finalize(
            review, collected[0], tuple(collected[1:]), required_approvals=required
        )
    except (ApprovalError, ValueError) as error:
        return False, f"Not approved: {error}"
    state.append(approval)
    write_bundle(out_dir, manifest, manifest_hash, private_key)
    return True, (
        f"Approved by {required} and signed. Bundle written to {out_dir}. "
        f"Manifest SHA-256 {manifest_hash}"
    )


def serve(
    review: ReviewPackage, out_dir: Path, private_key, port: int = 8765, required: int = 1
) -> None:
    review_hash = sha256(review)
    state: list[HumanApproval] = []

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: str, status: int = 200) -> None:
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            self._send(render_page(review, review_hash, "", state, required))

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
            ok, text = handle_approval(review, form, out_dir, private_key, None, state, required)
            css = "ok" if ok else "bad"
            note = f'<p class="{css}">{html.escape(text)}</p>'
            self._send(render_page(review, review_hash, note, state, required))

        def log_message(self, *args) -> None:
            pass

    print(f"Approval screen on http://127.0.0.1:{port}  (Ctrl+C to stop)")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
