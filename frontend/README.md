# M4 frontend (React + TypeScript + Vite)

Proposed user interface for the M4 backend. It contains **no business logic and no hard-coded results**:
every number is fetched from the FastAPI backend (`../M4-Mahdi`).

Stack: React 18, TypeScript, Vite 5, Tailwind CSS 3, React Router 6, Recharts 2, Axios, lucide-react icons.
Requires Node.js **18.18+** (tested with Node 18.19).

```powershell
cd frontend
npm install
Copy-Item .env.example .env
npm run dev          # http://localhost:5173 (proxies /api to http://127.0.0.1:8000)
npm test             # Vitest
npm run build        # type-check + production build in dist/
```

The backend must be running (`M4-Mahdi`, port 8000). `VITE_BACKEND_URL` sets the proxy target;
`VITE_API_BASE_URL` can point the browser at another API origin (then add the origin to the backend `CORS_ORIGINS`).

Pages: login, energy dashboard, planning agent (conversation, clarification, formulation approval, workflow status,
investigation report), scenario builder, optimization results, strategies, strategy roadmap and admin decision panel,
comparison, history, data sources & assumptions, admin dashboard, review queue, decision history, users,
access denied.

Security: the access token is kept in memory; the refresh token is an httpOnly cookie handled by the backend.
Route guards only improve navigation; permissions are enforced by the backend.

Design: black / white / gold system (tokens in `tailwind.config.js`). Chart colors for technologies were checked for
colour-vision-deficiency separation; every chart has a legend and a "Show table" view.
