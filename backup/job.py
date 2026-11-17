"""
Task Manager maintenance job (runs free on GitHub Actions).

  python backup/job.py cleanup   -> completed > 7 days: build Excel backup, EMAIL it, then delete from Firestore
  python backup/job.py usage     -> email the admin if today's Firestore usage passes 70% / 90% of the free limits

Nothing is deleted unless the email was sent successfully.
Environment: FIREBASE_SERVICE_ACCOUNT (json text), SMTP_USER, SMTP_PASS (Gmail app password), MAIL_TO
"""
import io
import json
import os
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import firebase_admin
from firebase_admin import credentials, firestore
from openpyxl import Workbook

RETENTION_DAYS = 7
LIMITS = {"reads": 50000, "writes": 20000, "deletes": 20000}
LEVELS = (0.7, 0.9)

firebase_admin.initialize_app(credentials.Certificate(json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"])))
db = firestore.client()


def pacific_day():
    return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")


def send_mail(subject, body, attachment=None, filename=None):
    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["MAIL_TO"]
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment:
        msg.add_attachment(attachment, maintype="application",
                           subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=filename)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(msg)


def bump(**kw):
    """Count this job's own usage so the in-app counter stays honest."""
    data = {k: firestore.Increment(v) for k, v in kw.items() if v}
    if data:
        db.collection("usage").document(pacific_day()).set(data, merge=True)


def cleanup():
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    reads = 0
    found = list(db.collection("tasks").where("completedAt", "<=", cutoff).stream())
    reads += len(found)
    roots = [d for d in found if d.to_dict().get("status") == "Completed" and d.to_dict().get("parentId") is None]
    if not roots:
        print("Nothing to clean up.")
        bump(reads=max(reads, 1))
        return

    # collect each root with all of its descendants; skip trees that still have open work
    trees, skipped = [], 0
    for r in roots:
        members, frontier, ok = [r], [r.id], True
        while frontier:
            nxt = []
            for pid in frontier:
                kids = list(db.collection("tasks").where("parentId", "==", pid).stream())
                reads += max(len(kids), 1)
                for k in kids:
                    members.append(k)
                    nxt.append(k.id)
                    if k.to_dict().get("status") not in ("Completed", "Rejected"):
                        ok = False
            frontier = nxt
        if ok:
            trees.append(members)
        else:
            skipped += 1
    if not trees:
        print(f"{skipped} tree(s) still have open subtasks - nothing deleted.")
        bump(reads=reads)
        return

    users = {u.id: u.to_dict() for u in db.collection("users").stream()}
    reads += len(users)
    name = lambda uid: (users.get(uid) or {}).get("name", "?")

    wb = Workbook()
    ws = wb.active
    ws.title = "Tasks"
    ws.append(["Task ID", "Parent ID", "Title", "Description", "Created by", "Assigned to", "Status", "Priority",
               "Due", "Created", "Completed", "Rework count", "Last remark"])
    wh = wb.create_sheet("History")
    wh.append(["Task ID", "Task title", "Time", "By", "Action", "Note"])
    refs = []
    for members in trees:
        for d in members:
            t = d.to_dict()
            refs.append(d.reference)
            ws.append([d.id, t.get("parentId") or "", t.get("title"), t.get("description"), name(t.get("createdBy")),
                       name(t.get("assignedTo")), t.get("status"), t.get("priority"), t.get("due"), t.get("createdAt"),
                       t.get("completedAt") or "", t.get("reworkCount", 0), t.get("remarks")])
            for h in t.get("history", []):
                wh.append([d.id, t.get("title"), h.get("ts"), h.get("byName") or name(h.get("by")), h.get("action"), h.get("note")])
    for sheet in (ws, wh):
        for col in sheet.columns:
            sheet.column_dimensions[col[0].column_letter].width = min(50, max(10, max(len(str(c.value or "")) for c in col) + 2))
    buf = io.BytesIO()
    wb.save(buf)

    today = datetime.now().strftime("%Y-%m-%d")
    # 1) email first. If this raises, nothing is deleted.
    send_mail(f"Task Manager backup {today}: {len(refs)} completed task(s)",
              f"Attached: {len(refs)} task(s) (completed more than {RETENTION_DAYS} days ago) with their history.\n"
              f"They have now been removed from Firebase to stay within the free limits.",
              buf.getvalue(), f"task_backup_{today}.xlsx")
    print(f"Backup mailed ({len(refs)} tasks).")

    # 2) delete only after the mail was sent
    for i in range(0, len(refs), 400):
        b = db.batch()
        for r in refs[i:i + 400]:
            b.delete(r)
        b.commit()
    print(f"Deleted {len(refs)} tasks. Skipped {skipped} tree(s) with open work.")
    bump(reads=reads, deletes=len(refs))


def usage():
    day = pacific_day()
    ref = db.collection("usage").document(day)
    snap = ref.get()
    data = snap.to_dict() if snap.exists else {}
    alerted = dict(data.get("alerted", {}))
    lines, changed = [], False
    for k, limit in LIMITS.items():
        frac = data.get(k, 0) / limit
        for lvl in LEVELS:
            key = f"{k}_{int(lvl * 100)}"
            if frac >= lvl and not alerted.get(key):
                alerted[key] = True
                changed = True
                lines.append(f"{k}: {data.get(k, 0):,} of {limit:,} ({frac * 100:.0f}%)")
                break  # one line per metric per run
    if lines:
        send_mail("Task Manager: Firebase free limit warning",
                  f"Usage for {day} (Pacific day, quotas reset at midnight Pacific):\n\n" + "\n".join(lines) +
                  "\n\nThe app counts usage itself, so treat these as estimates and check the Firebase console "
                  "(Firestore > Usage). If this keeps happening, ask users to refresh less often.")
        ref.set({"alerted": alerted}, merge=True)
        print("Warning mailed:", lines)
    else:
        print("Usage OK:", {k: data.get(k, 0) for k in LIMITS})
    if not changed:
        pass


def push():
    """Free push sender (runs every ~5 min on GitHub Actions). Sends a push for new task events to installed web apps."""
    from firebase_admin import messaging
    meta = db.collection("meta").document("push")
    snap = meta.get()
    m = snap.to_dict() if snap.exists else {}
    now = datetime.now(timezone.utc)
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"
    if not m.get("lastRun"):
        meta.set({"lastRun": iso(now), "sent": []})
        print("Push watcher started.")
        return
    since = iso(datetime.fromisoformat(m["lastRun"].replace("Z", "+00:00")) - timedelta(minutes=10))
    sent = list(m.get("sent", []))
    seen = set(sent)
    changed = list(db.collection("tasks").where("updatedAt", ">", since).stream())
    reads = len(changed) + 1
    by_uid, new_ids = {}, []

    def add(uid, text, tid):
        by_uid.setdefault(uid, []).append((text, tid))

    for d in changed:
        t = d.to_dict()
        parts, title = t.get("participants", []), t.get("title", "Task")
        for h in t.get("history", []):
            if (h.get("ts") or "") <= since:
                continue
            nid = f"{d.id}|{h.get('ts')}|{h.get('action')}"
            if nid in seen:
                continue
            seen.add(nid)
            new_ids.append(nid)
            who, act = h.get("byName") or "Someone", h.get("action")
            others = [u for u in parts if u != h.get("by")]
            if act == "Created":
                for u in others:
                    if u == t.get("assignedTo"):
                        add(u, f"\U0001F4E5 New task from {who}: {title}", d.id)
                    elif t.get("parentId"):
                        add(u, f"\u2795 {who} added a subtask: {title}", d.id)
            elif act == "Status \u2192 Completed":
                for u in others:
                    add(u, f"\u2705 {who} completed: {title}", d.id)
            elif act == "Status \u2192 Rework":
                if t.get("assignedTo") != h.get("by"):
                    note = f" \u2013 {h.get('note')}" if h.get("note") else ""
                    add(t.get("assignedTo"), f"\u21A9 Rework requested by {who}: {title}{note}", d.id)
            elif act == "Status \u2192 Rejected":
                for u in others:
                    add(u, f"\u26D4 {who} rejected: {title}", d.id)

    sent_ok = 0
    if by_uid:
        uids = list(by_uid)
        messages, refs = [], []
        for i in range(0, len(uids), 10):
            devs = list(db.collection("devices").where("uid", "in", uids[i:i + 10]).stream())
            reads += len(devs)
            for dev in devs:
                items = by_uid[dev.get("uid")]
                messages.append(messaging.Message(
                    token=dev.id,
                    data={"title": "Task Manager", "body": "\n".join(x[0] for x in items), "taskId": items[0][1], "tag": items[0][1]},
                    webpush=messaging.WebpushConfig(headers={"Urgency": "high", "TTL": "86400"})))
                refs.append(dev.reference)
        if messages:
            res = messaging.send_each(messages)
            for r, ref in zip(res.responses, refs):
                if r.success:
                    sent_ok += 1
                elif isinstance(r.exception, messaging.UnregisteredError):
                    ref.delete()                      # phone uninstalled the app / revoked permission
    meta.set({"lastRun": iso(now), "sent": (sent + new_ids)[-300:]})
    bump(reads=reads, writes=2)
    print(f"Push: {len(new_ids)} new event(s), {sent_ok} notification(s) sent.")


def resetpw(user_id, new_pw):
    """Admin resets a user's password (users have no real email, so the app cannot do this itself)."""
    from firebase_admin import auth
    if not (user_id or "").isdigit() or len(user_id) != 4:
        sys.exit("User ID must be 4 digits")
    if len(new_pw or "") < 6:
        sys.exit("Password must be at least 6 characters")
    found = list(db.collection("users").where("userId", "==", user_id).limit(1).stream())
    if not found:
        sys.exit(f"No user with ID {user_id}")
    auth.update_user(found[0].id, password=new_pw)
    print(f"Password reset for user {user_id}.")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "cleanup"
    if mode == "resetpw":
        resetpw(sys.argv[2] if len(sys.argv) > 2 else "", sys.argv[3] if len(sys.argv) > 3 else "")
    else:
        {"cleanup": cleanup, "usage": usage, "push": push}[mode]()
