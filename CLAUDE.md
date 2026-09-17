# Start Page

A self-hosted homelab start page. `backend/` is FastAPI + SQLAlchemy + SQLite on port 8000.
`frontend/` is React 19 + Vite + react-grid-layout. See README.md for features and the API.

## Run
- Docker: `docker compose up -d --build` → http://localhost:8000 (data in `./data`)
- Dev: `./dev.ps1` (uvicorn on 8000 plus Vite on 5173, which proxies `/api`)
- Node is managed by nvm-windows. If `node` isn't on PATH, prepend `C:\nvm4w\nodejs`.

## Tests
- Backend: `cd backend; python -m pytest -q`
- MCP server tests (from the repo root): `uv run --no-project --with "mcp>=1.10,<2" --with-requirements backend/requirements.txt --with-requirements backend/requirements-dev.txt pytest backend/tests -q`
- Frontend: `cd frontend; npm run test` then `npm run build`
- After changing a widget in `frontend/src/widgets/`, run `npm run catalog` to refresh `mcp/widget_catalog.json`.

## Building the page (the user's dashboard)
When the user asks to add, change or arrange widgets, pages, services or the look of their
start page, they mean the live dashboard. Use the `startpage` MCP tools, not source edits.
Source edits are only for new widget *types* or app behaviour.

1. `get_overview` first: pages, services, integrations, settings. If the app isn't reachable,
   tell the user how to start it rather than editing files.
2. `list_widget_types(type)` before adding or configuring a type. Only use its field keys and
   option values.
3. Grid: a page has `columns` (default 24) and `row_height` px rows (default 40). x/y/w/h are
   cells. Omit x/y to auto-place. Lay out with intent: align rows, give related widgets
   matching widths, and use `heading`/`divider`/`box` to group.
4. Some widgets use registry services. A `service` widget takes `service_id`, and `group`
   selects services by category or ids in its config. Create missing services with
   `create_service`, and look up icons with `search_icons`.
5. Call `create_backup` before deleting widgets or pages, restoring, or changing global
   settings. Confirm deletions with the user first.
6. The user's open tab refreshes within about 3 s. After a batch of changes, summarise what
   changed and where. Use `get_page` to double-check placement.
