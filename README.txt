TASK MANAGER v2 - Firebase FREE (Spark) edition
================================================
Web app (HTML/JS) on Firebase Hosting + Firestore + Auth. No card needed. No file attachments.
Your phone APK and PC EXE just show this web address.

WHAT CHANGED FROM THE OLD FLET VERSION
- Login is now by EMAIL + password (User ID login removed). Admin resets passwords by "Send reset email".
- Only the admin can create users (no public sign-up). Users are deactivated, not deleted.
- New status: Rework (plus Pending / In Progress / On Hold / Completed / Rejected).
- Attachments removed (Firebase free plan has no file storage).
- Old SQLite data is NOT migrated automatically.

STEP 1 - Firebase project (console.firebase.google.com)
 a) Add project (stay on the free Spark plan; do NOT click upgrade).
 b) Build > Authentication > Get started > enable "Email/Password".
 c) Build > Firestore Database > Create database > production mode > region asia-south1 (Mumbai).
 d) Project settings > General > Your apps > Web (</>) > register app > copy the firebaseConfig values.
 e) Do NOT enable Storage / Functions (they need a paid plan).

STEP 2 - Edit two files
 a) public/index.html : paste your firebaseConfig, and set ADMIN_EMAIL (your own email).
 b) firestore.rules   : set the same email in adminEmail().

STEP 3 - Deploy (needs Node.js)
   npm install -g firebase-tools
   firebase login
   firebase use --add          (pick your project)
   firebase deploy
 Your app: https://YOUR_PROJECT.web.app

STEP 4 - First admin
 Open the app > "First-time admin setup" > use the ADMIN_EMAIL > create password.
 Then Admin > Branches (create) > Users (create each user; tell them to use "Forgot password" to set their own).

STEP 5 - Weekly backup + auto delete + limit warnings (free, GitHub Actions)
 a) Create a PRIVATE GitHub repo and upload this folder (keep .github and backup folders).
 b) Firebase console > Project settings > Service accounts > Generate new private key (JSON file).
 c) Gmail: turn on 2-step verification, then create an App Password (myaccount.google.com/apppasswords).
 d) GitHub repo > Settings > Secrets and variables > Actions > add 4 secrets:
      FIREBASE_SERVICE_ACCOUNT = whole contents of the JSON file
      SMTP_USER = your Gmail address that sends the mail
      SMTP_PASS = the Gmail app password
      MAIL_TO   = where the backup should be delivered (can be the same Gmail)
 e) Actions tab > "Task Manager maintenance" > Run workflow (mode "usage") to test the email.
 Daily 03:00 IST: tasks completed more than 7 days ago (with subtasks and history) are exported to Excel,
 emailed to MAIL_TO, and ONLY THEN deleted. If the email fails, nothing is deleted.
 Every 6 hours: you get an email when reads/writes/deletes pass 70% and 90% of the free daily limit.
 The Admin > Usage tab and a red banner show the same numbers inside the app.

STEP 6 - Phone APK and PC EXE
 In 2_PHONE_APK\main.py and 3_PC_EXE\desktop_app.py change SERVER_URL to https://YOUR_PROJECT.web.app
 then run build_apk.bat and build_desktop.bat as before.
 (You can also just open the web address in Chrome/Edge and "Add to Home screen / Install app".)
 Stop using start_server.bat and start_internet.bat - the PC no longer needs to run.

FREE LIMITS (Spark, per day): 50,000 reads, 20,000 writes, 20,000 deletes, 1 GiB data.
For 40-50 users this app stays far below them (it uses live listeners + local cache, tasks are short-lived).
Quotas reset at midnight Pacific time. Verify current limits in the Firebase console.

NOT TESTED HERE - I could not run Firebase from my workspace. Before giving it to everyone, test with the admin
+ 2 test users: create task, subtask, Rework, complete, branch manager view, and run the maintenance workflow once.

KNOWN LIMITS
- A task remembers the branches of its creator/assignee at creation time. If you move a user to another branch,
  old tasks stay with the old branch's manager view.
- A task's creator can only delete subtasks that they can see; otherwise ask the admin.
