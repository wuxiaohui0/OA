import { spawn } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

// npm is only a development task runner; all API code runs in Python.
const root = fileURLToPath(new URL("../", import.meta.url));
const api = join(root, "apps", "api");
const python = process.env.OA_PYTHON || join(api, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
if (!existsSync(python)) {
  console.error("Python environment not found. Create apps/api/.venv and install requirements-dev.txt; see README.md.");
  process.exit(1);
}
const task = process.argv[2] || "dev";
const tasks = {
  dev: ["-m", "oa", "--reload"],
  start: ["-m", "oa"],
  check: ["-m", "ruff", "check", "oa", "tests"],
  build: ["-m", "compileall", "-q", "oa"],
  test: ["-m", "pytest", "-q", "--basetemp", join(root, ".tmp", `pytest-${process.pid}-${Date.now()}`)],
};
if (!tasks[task]) {
  console.error(`Unknown API task: ${task}`);
  process.exit(1);
}
if (task === "test") mkdirSync(join(root, ".tmp"), { recursive: true });
const child = spawn(python, [...tasks[task], ...process.argv.slice(3)], {
  cwd: api,
  stdio: "inherit",
  env: { ...process.env, PYTHONUTF8: "1", PYTHONUNBUFFERED: "1" },
});
child.on("error", (error) => { console.error(error.message); process.exitCode = 1; });
child.on("exit", (code) => { process.exitCode = code ?? 1; });
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => child.kill(signal));
