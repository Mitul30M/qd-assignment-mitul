"""Ops console: HTML page + approve/reject endpoints.

All rider-supplied text is HTML-escaped (XSS). If `OPS_TOKEN` is set, ops
endpoints require it via `Authorization: Bearer <token>` or `?token=`; if
omitted, the page is unauthenticated (documented scope decision in README).
"""

from __future__ import annotations

import html
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app import notices
from app.config import settings
from app.main import get_conn
from app.ops import approve_item, reject_item
from app.payswift import ClientConfig, PaySwiftClient

router = APIRouter()


def _authorized(request: Request) -> bool:
    if not settings.ops_token:
        return True
    auth = request.headers.get("authorization", "")
    if auth == f"Bearer {settings.ops_token}":
        return True
    return request.query_params.get("token") == settings.ops_token


def _guard(request: Request) -> None:
    if not _authorized(request):
        raise HTTPException(status_code=401, detail="ops token required")


@router.post("/ops/{ops_id}/approve")
async def ops_approve(ops_id: str, request: Request):
    _guard(request)
    conn = get_conn()
    client = PaySwiftClient(ClientConfig(base_url=settings.payswift_base_url))
    result = approve_item(conn, ops_id, client)
    if result["state"] == "not_found":
        raise HTTPException(status_code=404, detail="ops item not found")
    if "form" in request.headers.get("content-type", ""):
        from fastapi.responses import RedirectResponse

        return RedirectResponse("/ops", status_code=303)
    return result


@router.post("/ops/{ops_id}/reject")
async def ops_reject(ops_id: str, request: Request):
    _guard(request)
    reason = ""
    ctype = request.headers.get("content-type", "")
    if "form" in ctype:
        form = await request.form()
        reason = str(form.get("reason") or "").strip()
    else:
        try:
            body = json.loads((await request.body()) or b"{}")
            reason = str(body.get("reason") or "").strip() if isinstance(body, dict) else ""
        except ValueError:
            reason = ""
    if not reason:
        raise HTTPException(status_code=422, detail="reason required for rejection")
    conn = get_conn()
    result = reject_item(conn, ops_id, reason)
    if result["state"] == "not_found":
        raise HTTPException(status_code=404, detail="ops item not found")
    if "form" in ctype:
        from fastapi.responses import RedirectResponse

        return RedirectResponse("/ops", status_code=303)
    return result


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<html><head><title>{_esc(title)}</title>"
        "<style>body{font-family:sans-serif;margin:24px;max-width:1100px}"
        "table{border-collapse:collapse;width:100%;margin:12px 0}"
        "td,th{border:1px solid #ccc;padding:6px;text-align:left;font-size:14px}"
        "th{background:#eee}.warn{background:#ffe9e9}.ok{background:#e9ffe9}"
        ".pill{display:inline-block;padding:1px 8px;border-radius:10px;background:#eee}"
        "textarea{width:320px}button{margin-top:4px}</style></head>"
        f"<body><h1>{_esc(title)}</h1>{body}</body></html>"
    )


@router.get("/ops", response_class=HTMLResponse)
def ops_page(request: Request):
    _guard(request)
    conn = get_conn()
    rider = request.query_params.get("rider")
    undelivered = notices.undelivered_count(conn)
    banner = (f"<p>{undelivered} decision notice(s) not yet delivered to their rider "
              "(delivered on the rider's next message).</p>")
    if rider:
        return _page(f"Rider {rider}", banner + _rider_detail(conn, rider))
    return _page("Ops console", banner + _pending_table(conn) + _conversations(conn))


def _pending_table(conn) -> str:
    rows = conn.execute(
        "SELECT * FROM ops_items WHERE status = 'pending' ORDER BY created_at"
    ).fetchall()
    if not rows:
        return "<h2>Pending queue</h2><p>No pending items.</p>"
    trs = []
    for r in rows:
        action = ""
        if r["type"] == "approval" and r["claim"]:
            action = (f'<form method="post" action="/ops/{_esc(r["id"])}/approve">'
                      '<button type="submit">Approve</button></form>'
                      f'<form method="post" action="/ops/{_esc(r["id"])}/reject">'
                      '<textarea name="reason" rows="2" placeholder="rejection reason">'
                      "</textarea><button type=\"submit\">Reject</button></form>")
        elif r["type"] == "approval":
            action = "<span class='pill'>needs claim re-check</span>"
        claim_short = ""
        if r["claim"]:
            claim_short = _esc(json.dumps(r["claim"])[:120])
        trs.append(
            f"<tr class={'warn' if r['type'] == 'approval' else ''}>"
            f"<td>{_esc(r['id'])}</td><td>{_esc(r['rider_id'])}</td>"
            f"<td>{_esc(r['type'])}</td><td>{_esc(r['amount'])}</td>"
            f"<td>{_esc(r['category'])}</td><td>{_esc(r['reason'])}</td>"
            f"<td>{_esc(r['created_at'])}</td><td>{claim_short}</td>"
            f"<td>{action}</td></tr>"
        )
    head = ("<tr><th>id</th><th>rider</th><th>type</th><th>amount</th><th>category</th>"
            "<th>reason</th><th>created</th><th>claim</th><th>action</th></tr>")
    return f"<h2>Pending queue ({len(rows)})</h2><table>{head}{''.join(trs)}</table>"


def _conversations(conn) -> str:
    rows = conn.execute(
        "SELECT rider_id, COUNT(*) AS msgs, MAX(received_at) AS last_seen, "
        "(array_agg(text ORDER BY received_at DESC))[1] AS latest_text "
        "FROM inbound_messages GROUP BY rider_id ORDER BY last_seen DESC LIMIT 30"
    ).fetchall()
    links = " ".join(
        f'<a href="/ops?rider={_esc(r["rider_id"])}">{_esc(r["rider_id"])}</a> '
        f'({_esc(r["msgs"])}): {_esc(r["latest_text"])}' for r in rows)
    return f"<h2>Conversations</h2><p>{links or 'no messages yet'}</p>"


def _rider_detail(conn, rider: str) -> str:
    parts = ["<p><a href=\"/ops\">&larr; back to console</a></p>"]

    msgs = conn.execute(
        "SELECT * FROM inbound_messages WHERE rider_id = %s ORDER BY received_at",
        [rider]).fetchall()
    msg_rows = "".join(
        f"<tr><td>{_esc(m['message_id'])}</td><td>{_esc(m['received_at'])}</td>"
        f"<td>{_esc(m['text'])}</td><td>{_esc(m['reply'])}</td>"
        f"<td>{_esc(m['ok'])}</td></tr>" for m in msgs)
    parts.append(f"<h2>Messages</h2><table><tr><th>id</th><th>at</th><th>text</th>"
                 f"<th>reply</th><th>ok</th></tr>{msg_rows}</table>")

    traces = conn.execute(
        "SELECT * FROM trace_steps WHERE rider_id = %s ORDER BY step_id", [rider]).fetchall()
    trace_rows = "".join(
        f"<tr><td>{_esc(t['at'])}</td><td>{_esc(t['type'])}</td><td>{_esc(t['name'])}</td>"
        f"<td><code>{_esc(json.dumps(t['input'] or {})[:200])}</code></td>"
        f"<td><code>{_esc(json.dumps(t['output'] or {})[:300])}</code></td></tr>"
        for t in traces)
    parts.append(f"<h2>Trace timeline</h2><table><tr><th>at</th><th>type</th>"
                 f"<th>name</th><th>input</th><th>output</th></tr>{trace_rows}</table>")

    pays = conn.execute(
        "SELECT * FROM payments WHERE rider_id = %s ORDER BY created_at",
        [rider]).fetchall()
    pay_rows = "".join(
        f"<tr class={'ok' if p['state'] == 'confirmed' else ''}>"
        f"<td>{_esc(p['payment_id'])}</td><td>{_esc(p['dispute_id'])}</td>"
        f"<td>{_esc(p['purpose'])}</td><td>{_esc(p['amount'])}</td>"
        f"<td>{_esc(p['state'])}</td><td>{_esc(p['ist_day'])}</td>"
        f"<td>{_esc(p['idempotency_key'])}</td>"
        f"<td>{_esc((p['details'] or {}).get('shortfalls'))}</td></tr>" for p in pays)
    parts.append(f"<h2>Payments</h2><table><tr><th>id</th><th>dispute</th>"
                 f"<th>purpose</th><th>amount</th><th>state</th><th>ist_day</th>"
                 f"<th>idem key</th><th>shortfalls</th></tr>{pay_rows}</table>")

    its = conn.execute(
        "SELECT * FROM issues WHERE rider_id = %s ORDER BY updated_at", [rider]).fetchall()
    issue_rows = "".join(
        f"<tr><td>{_esc(i['issue_id'])}</td><td>{_esc(i['kind'])}</td>"
        f"<td>{_esc(i['ist_day'])}</td><td>{_esc(i['trip_ids'])}</td>"
        f"<td>{_esc(i['outcome'])}</td><td>{_esc(i['status'])}</td>"
        f"<td>{_esc(i['ask_count'])}</td></tr>" for i in its)
    parts.append(f"<h2>Issues</h2><table><tr><th>id</th><th>kind</th><th>day</th>"
                 f"<th>trips</th><th>outcome</th><th>status</th><th>asks</th></tr>"
                 f"{issue_rows}</table>")

    nts = conn.execute(
        "SELECT * FROM notices WHERE rider_id = %s ORDER BY created_at", [rider]).fetchall()
    notice_rows = "".join(
        f"<tr><td>{_esc(n['decision'])}</td><td>{_esc(n['amount'])}</td>"
        f"<td>{_esc(n['category'])}</td>"
        f"<td>{'delivered' if n['delivered_at'] else 'PENDING DELIVERY'}</td></tr>"
        for n in nts)
    parts.append(f"<h2>Notices</h2><table><tr><th>decision</th><th>amount</th>"
                 f"<th>category</th><th>state</th></tr>{notice_rows}</table>")
    return "".join(parts)
