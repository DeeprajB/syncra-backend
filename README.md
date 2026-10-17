# Syncra Backend

FastAPI API for [Syncra](https://syncra-frontend.deeprajb012.workers.dev), a collaborative project app. It stores workspaces, projects, tasks, comments, activity, and notifications in MongoDB, authenticates with Auth0, and pushes live updates over a workspace WebSocket.

The sibling frontend lives in `syncra-frontend`. This service is deployed on Render at `https://syncra-backend-zj6t.onrender.com`. Interactive docs are at `/docs` when the server is running.

## What it does

A signed-in user belongs to workspaces. Each workspace has members, projects, and a Kanban board of tasks. Members can comment, see who is online, see who is viewing a task or hovering a column, and read an activity feed. Assignments and new comments also create a personal notification inbox.

REST is the source of truth. The WebSocket tells connected clients that something changed. A missed event is recovered by refetching, not by replaying a backlog.

## Stack

- Python 3.9+
- FastAPI and Pydantic v2
- PyMongo async against MongoDB (`syncra-db` by default)
- Auth0 RS256 access tokens, verified with the tenant JWKS
- In-process WebSocket registry. One Render instance only

## Layout

```
app/
  api/routes/     HTTP and WebSocket routes
  services/       permissions and business rules
  repositories/   MongoDB access
  models/         stored documents
  schemas/        request and response models
  realtime/       ticket store, connection registry, event envelopes
  core/           settings, database, Auth0, in-process event bus
tests/            unittest suites with in-memory collections
```

IDs are MongoDB ObjectId strings. `GET /auth/me` returns the user id as `_id`. Tasks, projects, workspaces, comments, activity, and notifications return `id`.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Copy the variables below into `.env`. The file is gitignored. Startup creates indexes and subscribes the WebSocket registry to the in-process event bus. `GET /` returns a liveness message. `GET /db-health` reports whether MongoDB answered a ping.

## Configuration

| Variable                | Required | Purpose                                                                                                                           |
| ----------------------- | -------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `MONGODB_URI`           | yes      | Atlas connection string                                                                                                           |
| `MONGODB_DATABASE`      | no       | Database name. Defaults to `syncra-db`                                                                                            |
| `AUTH0_DOMAIN`          | yes      | Auth0 tenant domain, without `https://`                                                                                           |
| `AUTH0_AUDIENCE`        | yes      | API identifier. Production value is `https://syncra-backend-zj6t.onrender.com`                                                    |
| `APP_BASE_URL`          | no       | Frontend origin used in invite links. Defaults to `https://syncra-frontend.deeprajb012.workers.dev`. A trailing slash is stripped |
| `INVITATION_TTL_DAYS`   | no       | Invite lifetime. Defaults to 7                                                                                                    |
| `WS_TICKET_TTL_SECONDS` | no       | WebSocket ticket lifetime, 5 to 120. Defaults to 30                                                                               |
| `WS_ALLOWED_ORIGINS`    | no       | Comma-separated browser origins allowed to open a socket. If empty, only `APP_BASE_URL` is allowed                                |

REST CORS is `*`. That does not authorize the WebSocket. The socket checks the `Origin` header against `WS_ALLOWED_ORIGINS`. Local `next dev` needs `http://localhost:3000` in that list:

```
APP_BASE_URL=https://syncra-frontend.deeprajb012.workers.dev
WS_ALLOWED_ORIGINS=http://localhost:3000,https://syncra-frontend.deeprajb012.workers.dev
```

## Authentication

Every REST route except `GET /` and `GET /db-health` expects:

```
Authorization: Bearer <Auth0 access token>
```

The token must be RS256, for `AUTH0_AUDIENCE`, issued by `https://AUTH0_DOMAIN/`. The first accepted call upserts a user from the token claims and, when the profile is thin, from the Auth0 userinfo endpoint.

The browser cannot attach that header to a WebSocket, and the Auth0 cookie belongs to the frontend origin. A member instead calls `POST /api/v1/workspaces/{workspace_id}/ws-ticket`, then connects and sends the ticket as the first message. Tickets last about 30 seconds, are single-use, are bound to that user and workspace, stay in memory, and are not logged. Do not put a token or a ticket in the socket URL.

## Roles

Workspace roles are `owner`, `admin`, `member`, and `viewer`.

| Action                                                                | Who                                                        |
| --------------------------------------------------------------------- | ---------------------------------------------------------- |
| View the workspace, projects, tasks, comments, and activity           | Any member, including a viewer                             |
| Create a project or task, edit a task, assign, move, reorder, comment | Owner, admin, member                                       |
| Update or archive a workspace or project                              | Owner or admin                                             |
| Delete a workspace                                                    | Owner. This archives it                                    |
| Change member roles                                                   | Owner                                                      |
| Invite, remove a member                                               | Owner or admin. Admins can remove members and viewers only |
| Leave                                                                 | Any member except the owner                                |
| Delete a task                                                         | Owner or admin, or the member who created it               |
| Edit a comment                                                        | The author                                                 |
| Delete a comment                                                      | The author, or an owner or admin                           |
| Delete a task that still has subtasks                                 | Nobody. The API returns 409                                |

A non-member receives `403` with `You do not have access to this workspace`. An archived workspace or project hides its children as `404`.

Joining an invite always grants `member`. The raw invite token is returned once. Only its SHA-256 hash is stored. Invites expire, are single-use, and can be revoked.

## REST API

Unless noted, list endpoints that paginate return `{ "items": [], "next_cursor": null }`. Follow `next_cursor` until it is null. A bad cursor is `422` with `Invalid cursor`.

### Auth

| Method | Path       | Notes                               |
| ------ | ---------- | ----------------------------------- |
| GET    | `/auth/me` | Current user. The id field is `_id` |

### Workspaces and members

| Method | Path                                                    | Notes                                                                     |
| ------ | ------------------------------------------------------- | ------------------------------------------------------------------------- |
| POST   | `/api/v1/workspaces`                                    | Creates a workspace and makes the caller the owner                        |
| GET    | `/api/v1/workspaces`                                    | Workspaces the caller belongs to                                          |
| GET    | `/api/v1/workspaces/{workspace_id}`                     |                                                                           |
| PATCH  | `/api/v1/workspaces/{workspace_id}`                     | Owner or admin                                                            |
| DELETE | `/api/v1/workspaces/{workspace_id}`                     | Owner. Sets `archived_at` and returns 204                                 |
| GET    | `/api/v1/workspaces/{workspace_id}/members`             | `user_id` is the person id used for assignees. `id` is the membership row |
| PATCH  | `/api/v1/workspaces/{workspace_id}/members/{member_id}` | Owner changes role to `admin`, `member`, or `viewer`                      |
| DELETE | `/api/v1/workspaces/{workspace_id}/members/{member_id}` | Removes a member and closes their sockets                                 |
| POST   | `/api/v1/workspaces/{workspace_id}/leave`               | Closes the caller's sockets                                               |
| POST   | `/api/v1/workspaces/{workspace_id}/invitations`         | Returns `{ "invite_url" }`                                                |
| POST   | `/api/v1/invitations/{invite_token}/join`               | Joins as `member`                                                         |

Invite links look like `{APP_BASE_URL}/invite/{token}`.

### Projects

| Method | Path                                         | Notes                                                                                             |
| ------ | -------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| POST   | `/api/v1/workspaces/{workspace_id}/projects` |                                                                                                   |
| GET    | `/api/v1/workspaces/{workspace_id}/projects` | Active projects only                                                                              |
| GET    | `/api/v1/projects/{project_id}`              |                                                                                                   |
| PATCH  | `/api/v1/projects/{project_id}`              | Owner or admin                                                                                    |
| DELETE | `/api/v1/projects/{project_id}`              | Owner or admin. Sets `archived_at` and returns 204. Does not change `status` and does not cascade |

Project status is a label: `planning`, `active`, `completed`, or `archived`. Visibility is `archived_at`, not the label. Project dates are `YYYY-MM-DD`.

### Tasks

| Method | Path                                          | Notes                                                                             |
| ------ | --------------------------------------------- | --------------------------------------------------------------------------------- |
| POST   | `/api/v1/projects/{project_id}/tasks`         | Lands at the end of `todo`                                                        |
| GET    | `/api/v1/projects/{project_id}/tasks`         | Ordered by column, then position. Filters: `status`, `assignee_id`, `priority`    |
| PUT    | `/api/v1/projects/{project_id}/tasks/reorder` | Body `{ "status", "task_ids" }`. The ids must be exactly the tasks in that column |
| GET    | `/api/v1/tasks/{task_id}`                     |                                                                                   |
| PATCH  | `/api/v1/tasks/{task_id}`                     | Partial update. Empty body is 400                                                 |
| PATCH  | `/api/v1/tasks/{task_id}/move`                | Body `{ "status", "position", "version"? }`. Returns the moved task only          |
| DELETE | `/api/v1/tasks/{task_id}`                     | Hard delete. 204. 409 if subtasks remain                                          |
| GET    | `/api/v1/tasks/{task_id}/activity`            | Newest 50. Shape `{ "items" }`                                                    |

Task status is `todo`, `in_progress`, `review`, or `done`. That is separate from project status. Priority is `low`, `medium`, `high`, or `urgent`. `position` is per column and starts at 0. Task dates are UTC datetimes.

`version` starts at 1 and increments on each write. Sending a stale `version` on patch or move returns `409` and `Task has been modified. Refresh and try again.` Omitting `version` is last-write-wins. A status-only patch appends the card to the end of the new column. Move `position` is the index after the card has been removed from its source column.

An assignee must be a workspace member. The assignee id is `user_id` from the members list, not the membership row id.

### Comments

| Method | Path                               | Notes                                                    |
| ------ | ---------------------------------- | -------------------------------------------------------- |
| POST   | `/api/v1/tasks/{task_id}/comments` | Body `{ "content" }`. Trimmed, 1 to 4000 characters. 201 |
| GET    | `/api/v1/tasks/{task_id}/comments` | Oldest first. Viewers can read                           |
| PATCH  | `/api/v1/comments/{comment_id}`    | Author only                                              |
| DELETE | `/api/v1/comments/{comment_id}`    | Author, or owner or admin. 204                           |

A comment response has `id`, `task_id`, `author_id`, `author_name`, `avatar_url`, `content`, `created_at`, and `updated_at`. There is no email field. `name` is the given name plus family name, otherwise the email.

### Activity

| Method | Path                                         | Notes                    |
| ------ | -------------------------------------------- | ------------------------ |
| GET    | `/api/v1/workspaces/{workspace_id}/activity` | Newest first. Any member |

Items include `id`, `project_id`, `task_id`, `actor_id`, `actor_name`, `avatar_url`, `action`, `metadata`, and `created_at`. `member.joined` and `member.left` have `project_id: null`. Grouping by day is a client concern.

Task activity actions: `task.created`, `task.updated`, `task.assigned`, `task.status_changed`, `task.moved`, `task.deleted`. A status change is only `task.status_changed`. A move is `task.moved` even when the column changes. Reordering a column is one `task.moved` with `metadata.scope` of `column` and `metadata.task_ids`. Comment rows store `comment_id` and an excerpt of about 140 characters.

### Notifications

All four routes return only the signed-in user's notifications.

| Method | Path                                           | Notes                                           |
| ------ | ---------------------------------------------- | ----------------------------------------------- |
| GET    | `/api/v1/notifications`                        | Newest first. Default limit 20, max 50          |
| GET    | `/api/v1/notifications/unread-count`           | `{ "count" }`                                   |
| PATCH  | `/api/v1/notifications/{notification_id}/read` | 404 if it is missing or belongs to someone else |
| POST   | `/api/v1/notifications/read-all`               | 204                                             |

A notification is created when someone else assigns you a task, and when someone else comments on a task you are assigned to or have already commented on. You are not notified about your own action. Mentions and invitation emails are not implemented. A failed notification write does not fail the task or comment request.

## Realtime

Connect to:

```
wss://<host>/ws/workspaces/{workspace_id}
```

Immediately after the socket opens, send:

```json
{ "type": "connection.auth", "ticket": "<ticket>" }
```

The ticket can also be offered as the subprotocol `syncra.ticket.<ticket>`. Prefer the first message. The server ignores any `actor_id` sent by the client.

Every server message uses this envelope:

```json
{
  "event_id": "hex",
  "schema_version": 1,
  "type": "task.created",
  "workspace_id": "…",
  "project_id": "… or null",
  "task_id": "… or null",
  "actor_id": "user id",
  "occurred_at": "2026-10-17T12:00:00Z",
  "data": {}
}
```

`notification.created` also has top-level `recipient_id`. That event is sent only to the recipient's sockets in that workspace. Other events go to every connection in the workspace, including the actor.

Client messages after auth:

| Type                   | Body                                            | Effect                                                         |
| ---------------------- | ----------------------------------------------- | -------------------------------------------------------------- |
| `presence.ping`        | `{}`                                            | Keeps the socket open. Send about every 20 seconds             |
| `task.viewing`         | `{ "task_id" }`                                 | Marks this tab as viewing that task                            |
| `task.stopped_viewing` | `{ "task_id" }`                                 | Clears it when no other tab of yours still has it open         |
| `presence.cursor`      | `{ "task_id" }` or `{ "project_id", "column" }` | Ephemeral pointer on a card or a column. Omit both to clear it |

`column` must be `todo`, `in_progress`, `review`, or `done`. Pointers are not stored in MongoDB. A `presence.snapshot` for a new socket includes each member's current task and cursor. `presence.joined` fires on a user's first tab. `presence.left` fires when their last tab closes.

Other server-only types: `connection.ready`, `connection.error`, `connection.ping`, `presence.snapshot`, `presence.joined`, `presence.left`, `presence.cursor`, `task.viewing`, `task.stopped_viewing`, plus the task, comment, member, and notification events above.

The server sends `connection.ping` after 30 seconds of silence and closes with `1001` after three misses. `1008` means a bad origin, a bad ticket, or no membership at connect time. `4403` means the member was removed or left while connected. Events are published only after the database write commits. One mutation produces one activity event. A `403`, `409`, or `422` does not emit an event. A failed broadcast does not fail the REST call.

The registry lives in this process. A second Render instance will not see these sockets or tickets. Do not add a shared broker until the service actually runs more than one instance.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests
```

The suites cover projects, tasks, realtime, and notifications. They use FastAPI's test client and in-memory collections. Do not point `TestClient` at `app.main` as a lifespan context manager. That startup path pings Atlas and creates indexes.
