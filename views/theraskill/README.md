# TheraSkillAgent browser workspaces

The application serves four built interfaces from one process on port 5176:

| Workspace | Browser path |
| --- | --- |
| Main workbench, skills, extraction, transcripts, knowledge, evaluations and courses | `/` |
| Support assistant | `/theraskill/support/` |
| Scenario practice | `/theraskill/simulation/` |
| Expert review and governance | `/theraskill/expert/` |

Build all four from the repository root with Node 22.12 or newer:

```bash
npm ci
npm run build
theraskill-agent ui
```

A wheel already contains these built assets; deployment does not require Node.
All six domain APIs run in process. Browser requests use the same application
origin, for example `/api/support` and `/api/expert`. Domain ports 8876, 8878,
8879, 8881, 8891 and 8892 are not required for normal deployment.

Shared localization lives in `shared/`; the preference is stored under
`theraskill-locale`. Language switching updates interface text and output-language
choices while preserving source text, quotations and existing user content.

For frontend development, run the Python application on 5186 and Vite on 5176
in two terminals:

```bash
theraskill-agent ui --port 5186
# In another terminal at the repository root:
npm run dev:workbench
```

The development proxy forwards `/api` to the one Python application. Change
`workbench/vite.config.ts` if you choose another backend port. Normal deployment
only needs `theraskill-agent ui` and serves the production build directly.

See the [deployment guide](../../docs/deployment.en.md) and
[workflow guide](../../docs/workflows.en.md) for application setup and operations.
