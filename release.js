// One command to publish an update:  node release.js 2.1.1 "what changed"
// 1) sets the new version in public/index.html and public/version.json
// 2) deploys to Firebase  3) pushes to GitHub
// Every open web page / APK / EXE then shows "New version available - Update now".
const fs = require("fs");
const { execSync } = require("child_process");

const [, , ver, ...rest] = process.argv;
const notes = rest.join(" ");
if (!/^\d+\.\d+\.\d+$/.test(ver || "")) {
  console.log('Usage: node release.js 2.1.1 "what changed"');
  process.exit(1);
}
let html = fs.readFileSync("public/index.html", "utf8");
if (!/const APP_VERSION = "[^"]*";/.test(html)) { console.log("APP_VERSION line not found in public/index.html"); process.exit(1); }
html = html.replace(/const APP_VERSION = "[^"]*";/, `const APP_VERSION = "${ver}";`);
fs.writeFileSync("public/index.html", html);
fs.writeFileSync("public/version.json", JSON.stringify({ version: ver, notes, force: false }, null, 2) + "\n");

const run = (c, soft) => { try { execSync(c, { stdio: "inherit" }); } catch (e) { if (!soft) process.exit(1); } };
run("firebase deploy");
run("git add -A", true);
run(`git commit -m "v${ver}"`, true);
run("git push", true);
console.log(`\nDone. Version ${ver} is live. Users will see the Update button.`);
