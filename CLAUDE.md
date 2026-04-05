# Development Agent Instructions

This file is for coding agents only and is not part of the product runtime prompt. Build for a non-technical insurance operator: startup, restart, recovery, and error handling should feel simple, stable, and easy to understand. Protect client privacy by default, keep runtime data structured and local under the project directory, and never move development guidance into `AGENTS.md`, `SOUL.md`, `USER.md`, or `TOOLS.md` because those files affect cloud-sent product behavior.
| `state/` | Activity journal, runtime toggles |
| `skills/` | Custom agent skills (product runtime) |
| `memory/` | Per-client memory dirs + GLOBAL.md (product runtime) |
| `whatsapp-auth/` | Baileys credentials |
| `whatsapp-web/` | Chrome profile for CDP |

## Client Data Isolation

All client-scoped data flows must go through `ClientKey` (see `nanobot/session/client_key.py`). Read `ISOLATION.md` for the full invariant, architecture, and forbidden patterns before modifying any data path.
