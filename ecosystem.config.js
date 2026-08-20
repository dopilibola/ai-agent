// pm2 process definitions for the ai-sales tenant entrypoints.
//
// Each app is a console script installed into the project venv
// (.venv/bin/*, see [project.scripts] in pyproject.toml). They have a
// shebang pointing at the venv python, so pm2 runs them directly with
// interpreter: "none". cwd is pinned to the repo root so load_dotenv()
// finds .env and the Telethon *.session files under data/ resolve.
//
//   pm2 start ecosystem.config.js
//   pm2 logs anfa-all
//   pm2 save           # persist across reboots (pm2 startup must be set up)

const cwd = __dirname;
const interpreter = "none";
// The venv's editable-install .pth is not honoured by this Python, so the
// console scripts cannot import `apps`/`core` on their own. Put the repo
// root on PYTHONPATH explicitly — works regardless of how the venv was built.
const env = { PYTHONPATH: cwd };

module.exports = {
  apps: [
    {
      name: "oygul-customer",
      script: "./.venv/bin/oygul-customer",
      cwd,
      interpreter,
      env,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
    },
    {
      name: "oygul-merchant",
      script: "./.venv/bin/oygul-merchant",
      cwd,
      interpreter,
      env,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
    },
    {
      // Bundles the anfa catalog-advisor bot + userbot + catalog→vector sync loop.
      name: "anfa-all",
      script: "./.venv/bin/anfa-all",
      cwd,
      interpreter,
      env,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
    },
    {
      // Bundles the BYD customer userbot (AI sales agent) + operator bot
      // (manager agent, funnel notifications, inline callbacks) + the deal
      // scheduler that fires the funnel's time-delayed actions — one process.
      name: "byd-all",
      script: "./.venv/bin/byd-all",
      cwd,
      interpreter,
      env,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
    },
    {
      // Bundles the Maskan client userbot (AI grave-care agent) + operator bot
      // (manager agent, funnel notifications, inline callbacks) + the care
      // scheduler + the order watcher that polls the Maskan Django backend for
      // payment/work-status changes — one process.
      name: "maskan-all",
      script: "./.venv/bin/maskan-all",
      cwd,
      interpreter,
      env,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
    },
    {
      // Internal admin panel API (FastAPI/uvicorn). Reads the shared Postgres;
      // requires `uv sync --extra admin` so the script exists in the venv.
      // Reads ADMIN_* + DATABASE_URL from .env (cwd pinned to repo root).
      name: "admin-api",
      script: "./.venv/bin/admin-api",
      cwd,
      interpreter,
      env,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
    },
  ],
};
