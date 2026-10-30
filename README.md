# ProofPatch — Local-first AI coding agent

ProofPatch is a prototype that uses Ollama for local model inference and FastAPI + React for the app. It takes a bug description and a Python source file, asks the model for a proposed replacement, then optionally runs a user-supplied test command in a Docker container.

## Requirements
- Python 3.11+
- Node.js 20+
- Ollama: https://ollama.com/download
- Docker Desktop (recommended for isolated test execution)

## 1. Start a coding model
```bash
ollama pull qwen2.5-coder:3b
ollama serve
```
If Ollama is already running as a service, you do not need to run `ollama serve` again.

## 2. Start the backend
Open a terminal in `backend/`:
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```
API docs: http://127.0.0.1:8000/docs

## 3. Start the frontend
Open another terminal in `frontend/`:
```bash
npm install
npm run dev
```
Open the local URL printed by Vite, usually http://localhost:5173.

## How to use
1. Paste a Python file into the source editor.
2. Describe the bug and provide an optional test command such as `pytest -q`.
3. Click **Generate patch**.
4. Review the proposed code and unified diff.
5. If Docker is running, click **Run tests in sandbox**. The source is mounted read-only; the sandbox gets a writable copy of the workspace. The app uses a fixed Docker image and does not run arbitrary host shell commands.
6. Treat all model output as untrusted and review it before applying it to a real repository.

## Safety and limitations
- This is a hackathon MVP, not a production-grade autonomous coding agent.
- Do not upload secrets or sensitive source code.
- The test runner requires Docker and intentionally accepts only a restricted command grammar (`pytest`, `python -m pytest`, or `python -m unittest`).
- The test sandbox has no network access, drops Linux capabilities, and has memory/CPU/PID limits. Docker isolation is not a substitute for a hardened VM for hostile workloads.
- The initial MVP works with one Python file and returns a proposed replacement. It does not modify your original files automatically.
- Model output can be incorrect. A successful test run is evidence for the supplied tests, not proof that a patch is bug-free.
- For hackathon submission, add the required Nebius Token Factory/Nebius AI Cloud and eligible NVIDIA open-source model integration. Local Ollama alone may not satisfy the event rules.

## Project structure
```text
proofpatch/
  backend/app/main.py
  backend/requirements.txt
  frontend/
  docker/Dockerfile.runner
  docker/runner_entrypoint.sh
```
