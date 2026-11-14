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


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "cleanup"
    {"cleanup": cleanup, "usage": usage}[mode]()
