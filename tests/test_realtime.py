import asyncio
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from bson import ObjectId
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.routes.activity import router as activity_router
from app.api.routes.comments import router as comments_router
from app.api.routes.realtime import router as realtime_router
from app.core import events
from app.core.config import settings
from app.core.events import subscribe, unsubscribe
from app.models.auth.user import User
from app.models.invitation import Invitation
from app.models.project import Project, ProjectStatus
from app.models.task import Task, TaskPriority, TaskStatus
from app.models.workspace import Workspace
from app.models.workspace_member import MemberRole, WorkspaceMember
from app.realtime.manager import manager
from app.realtime.tickets import tickets
from app.services.auth import sync_current_user
from app.services.invitation import join_invitation
from app.services.workspace import remove_member


class _WriteResult:
    def __init__(self, inserted_id=None, matched_count=0, deleted_count=0):
        self.inserted_id = inserted_id
        self.matched_count = matched_count
        self.deleted_count = deleted_count


class _Cursor:
    def __init__(self, documents):
        self._documents = documents

    async def to_list(self, length=None):
        return list(self._documents)


def _matches(document, query):
    for key, expected in query.items():
        if document.get(key) != expected:
            return False
    return True


class _Collection:
    def __init__(self):
        self.docs = []
        self.indexes = []

    async def insert_one(self, document, session=None):
        stored = dict(document)
        if isinstance(stored.get("metadata"), dict):
            stored["metadata"] = dict(stored["metadata"])
        stored["_id"] = ObjectId()
        self.docs.append(stored)
        return _WriteResult(inserted_id=stored["_id"])

    async def find_one(self, query, session=None):
        for document in self.docs:
            if _matches(document, query):
                return dict(document)
        return None

    def find(self, query, session=None):
        return _Cursor([dict(document) for document in self.docs if _matches(document, query)])

    async def update_one(self, query, update, session=None):
        for document in self.docs:
            if _matches(document, query):
                document.update(update.get("$set", {}))
                return _WriteResult(matched_count=1)
        return _WriteResult(matched_count=0)

    async def delete_one(self, query, session=None):
        for index, document in enumerate(self.docs):
            if _matches(document, query):
                del self.docs[index]
                return _WriteResult(deleted_count=1)
        return _WriteResult(deleted_count=0)

    async def create_index(self, keys, **kwargs):
        self.indexes.append(kwargs.get("name"))
        return kwargs.get("name")


class _Database:
    def __init__(self):
        self.comments = _Collection()
        self.activities = _Collection()


class FakeSocket:
    def __init__(self, fail=False):
        self.sent = []
        self.closed = None
        self.fail = fail

    async def send_json(self, data):
        if self.fail:
            raise RuntimeError("send failed")
        self.sent.append(data)

    async def close(self, code=1000, reason=None):
        self.closed = code


@asynccontextmanager
async def _immediate_transaction():
    yield None


class RealtimeApiTests(unittest.TestCase):
    def setUp(self):
        self.db = _Database()
        self.workspaces = {}
        self.members = {}
        self.projects = {}
        self.tasks = {}
        self.people = {}
        self.events = []
        self.now = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
        manager.reset()
        tickets.reset()
        previous_origins = settings.ws_allowed_origins
        settings.ws_allowed_origins = "http://testserver"
        self.addCleanup(lambda: setattr(settings, "ws_allowed_origins", previous_origins))

        async def find_workspace(workspace_id):
            return self.workspaces.get(workspace_id)

        async def find_member(workspace_id, user_id):
            return self.members.get((workspace_id, user_id))

        async def find_project(project_id):
            return self.projects.get(project_id)

        async def find_task(task_id, session=None):
            return self.tasks.get(task_id)

        async def find_user(user_id):
            return self.people.get(user_id)

        async def find_users(user_ids):
            return [self.people[user_id] for user_id in user_ids if user_id in self.people]

        def collect(event):
            if event.get("type") == "comment.created":
                comment_id = event["data"]["id"]
                self.assertTrue(any(str(document["_id"]) == comment_id for document in self.db.comments.docs))
                self.assertTrue(any(document["action"] == "comment.created" for document in self.db.activities.docs))
            self.events.append(event)

        subscribe(collect)
        subscribe(manager.publish)
        self.addCleanup(lambda: unsubscribe(collect))
        self.addCleanup(lambda: unsubscribe(manager.publish))
        self.addCleanup(manager.reset)
        self.addCleanup(tickets.reset)

        self.patches = [
            patch("app.repositories.comments.database", self.db),
            patch("app.repositories.activities.database", self.db),
            patch("app.repositories.workspaces.find_active_by_id", new=find_workspace),
            patch("app.repositories.workspaces.find_by_id", new=find_workspace),
            patch("app.repositories.workspace_members.find_by_workspace_and_user", new=find_member),
            patch("app.repositories.projects.find_active_by_id", new=find_project),
            patch("app.repositories.tasks.find_by_id", new=find_task),
            patch("app.repositories.users.find_by_id", new=find_user),
            patch("app.repositories.users.find_by_ids", new=find_users),
            patch("app.services.comment.transaction", _immediate_transaction),
            patch("app.services.invitation.transaction", _immediate_transaction),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

        self.add_workspace("ws-1")
        self.add_workspace("ws-2")
        self.add_member("ws-1", "owner-1", MemberRole.OWNER)
        self.add_member("ws-1", "admin-1", MemberRole.ADMIN)
        self.add_member("ws-1", "member-1", MemberRole.MEMBER)
        self.add_member("ws-1", "member-3", MemberRole.MEMBER)
        self.add_member("ws-1", "viewer-1", MemberRole.VIEWER)
        self.add_member("ws-2", "member-2", MemberRole.MEMBER)
        self.add_project("proj-1", "ws-1")
        self.add_task("task-1", "proj-1", "member-1")
        for user_id, given_name in (
            ("owner-1", "Olivia"),
            ("admin-1", "Avery"),
            ("member-1", "Ada"),
            ("member-3", "Mina"),
            ("viewer-1", "Vera"),
            ("member-2", "Noah"),
        ):
            self.people[user_id] = User(
                id=user_id,
                sub="auth0|{0}".format(user_id),
                given_name=given_name,
                family_name="Lane",
                picture="https://example.com/{0}.png".format(user_id),
            )

    def add_workspace(self, workspace_id):
        self.workspaces[workspace_id] = Workspace(
            id=workspace_id,
            title="Studio",
            description=None,
            owner_id="owner-1",
            created_at=self.now,
            updated_at=self.now,
        )

    def add_member(self, workspace_id, user_id, role):
        self.members[(workspace_id, user_id)] = WorkspaceMember(
            id="mem-{0}-{1}".format(workspace_id, user_id),
            workspace_id=workspace_id,
            user_id=user_id,
            role=role,
            joined_at=self.now,
            updated_at=self.now,
        )

    def add_project(self, project_id, workspace_id):
        self.projects[project_id] = Project(
            id=project_id,
            workspace_id=workspace_id,
            title="Board",
            description=None,
            status=ProjectStatus.ACTIVE,
            owner_id="owner-1",
            created_by="owner-1",
            created_at=self.now,
            updated_at=self.now,
        )

    def add_task(self, task_id, project_id, created_by):
        self.tasks[task_id] = Task(
            id=task_id,
            project_id=project_id,
            title="Design API schema",
            status=TaskStatus.TODO,
            priority=TaskPriority.MEDIUM,
            created_by=created_by,
            position=0,
            created_at=self.now,
            updated_at=self.now,
        )

    def client_for(self, user_id):
        user = User(id=user_id, sub="auth0|{0}".format(user_id), email="{0}@example.com".format(user_id))
        application = FastAPI()
        application.include_router(comments_router)
        application.include_router(activity_router)
        application.include_router(realtime_router)
        application.dependency_overrides[sync_current_user] = lambda: user
        return TestClient(application)

    def test_ticket_is_single_use_and_workspace_bound(self):
        token, expires_in = tickets.issue("member-1", "ws-1")
        self.assertGreaterEqual(expires_in, 5)
        self.assertIsNone(tickets.redeem(token, "ws-2"))
        self.assertEqual(tickets.redeem(token, "ws-1"), "member-1")
        self.assertIsNone(tickets.redeem(token, "ws-1"))

        expired, _ttl = tickets.issue("member-1", "ws-1")
        user_id, workspace_id, _expires = tickets._tickets[expired]
        tickets._tickets[expired] = (user_id, workspace_id, 0)
        self.assertIsNone(tickets.redeem(expired, "ws-1"))

    def test_ticket_endpoint_rejects_non_members(self):
        allowed = self.client_for("member-1").post("/api/v1/workspaces/ws-1/ws-ticket")
        denied = self.client_for("member-2").post("/api/v1/workspaces/ws-1/ws-ticket")
        self.assertEqual(allowed.status_code, 200)
        self.assertTrue(allowed.json()["ticket"])
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.events, [])

    def test_socket_rejects_bad_origin_ticket_and_then_delivers_workspace_events(self):
        client = self.client_for("member-1")
        with self.assertRaises(WebSocketDisconnect):
            with client.websocket_connect("/ws/workspaces/ws-1", headers={"origin": "https://evil.example"}):
                pass
        self.assertEqual(manager.connection_count("ws-1"), 0)

        ticket = client.post("/api/v1/workspaces/ws-1/ws-ticket").json()["ticket"]
        with client.websocket_connect("/ws/workspaces/ws-1", headers={"origin": "http://testserver"}) as socket:
            socket.send_json({"type": "connection.auth", "ticket": "not-a-ticket", "actor_id": "owner-1"})
            error = socket.receive_json()
            self.assertEqual(error["type"], "connection.error")
            with self.assertRaises(WebSocketDisconnect):
                socket.receive_json()
        self.assertEqual(manager.connection_count("ws-1"), 0)

        other = FakeSocket()
        manager.connect("ws-1", "admin-1", other, "Avery Lane", "https://example.com/admin-1.png")
        outsider = FakeSocket()
        manager.connect("ws-2", "member-2", outsider, "Noah Lane", "")
        ticket = client.post("/api/v1/workspaces/ws-1/ws-ticket").json()["ticket"]
        with client.websocket_connect("/ws/workspaces/ws-1", headers={"origin": "http://testserver"}) as socket:
            socket.send_json({"type": "connection.auth", "ticket": ticket, "actor_id": "owner-1"})
            ready = socket.receive_json()
            snapshot = socket.receive_json()
            self.assertEqual(ready["type"], "connection.ready")
            self.assertEqual(ready["actor_id"], "member-1")
            self.assertEqual(snapshot["type"], "presence.snapshot")
            self.assertEqual({member["user_id"] for member in snapshot["data"]["members"]}, {"admin-1", "member-1"})
            self.assertEqual(other.sent[-1]["type"], "presence.joined")
            self.assertEqual(other.sent[-1]["actor_id"], "member-1")
            self.assertNotIn("email", other.sent[-1]["data"])
            self.assertEqual(outsider.sent, [])

            socket.send_json({"type": "task.viewing", "task_id": "task-1", "actor_id": "owner-1"})
            viewing = socket.receive_json()
            self.assertEqual(viewing["type"], "task.viewing")
            self.assertEqual(viewing["actor_id"], "member-1")
            self.assertEqual(viewing["data"]["task_id"], "task-1")
            self.assertEqual(other.sent[-1]["type"], "task.viewing")

            socket.send_json({"type": "not-json"})
            invalid = socket.receive_json()
            self.assertEqual(invalid["type"], "connection.error")
            socket.send_text("x" * 9000)
            oversized = socket.receive_json()
            self.assertEqual(oversized["data"]["message"], "Message is too large")

            created = client.post("/api/v1/tasks/task-1/comments", json={"content": "  Hello from Ada  "})
            self.assertEqual(created.status_code, 201)
            delivered = socket.receive_json()
            self.assertEqual(delivered["type"], "comment.created")
            self.assertEqual(delivered["data"]["content"], "Hello from Ada")
            self.assertEqual(outsider.sent, [])

        self.assertEqual(other.sent[-1]["type"], "presence.left")
        self.assertEqual(manager.connection_count("ws-1"), 1)

    def test_subprotocol_ticket_and_multi_tab_presence(self):
        client = self.client_for("member-1")
        ticket = client.post("/api/v1/workspaces/ws-1/ws-ticket").json()["ticket"]
        with client.websocket_connect(
            "/ws/workspaces/ws-1",
            subprotocols=["syncra.ticket.{0}".format(ticket)],
            headers={"origin": "http://testserver"},
        ) as socket:
            ready = socket.receive_json()
            snapshot = socket.receive_json()
            self.assertEqual(ready["type"], "connection.ready")
            self.assertEqual(snapshot["data"]["members"][0]["name"], "Ada Lane")

        first, _started = manager.connect("ws-1", "member-1", FakeSocket(), "Ada Lane", "")
        second, started = manager.connect("ws-1", "member-1", FakeSocket(), "Ada Lane", "")
        self.assertTrue(_started)
        self.assertFalse(started)
        manager.set_viewing("ws-1", first.connection_id, "proj-1", "task-1")
        manager.set_viewing("ws-1", second.connection_id, "proj-1", "task-1")
        self.assertIsNone(manager.clear_viewing("ws-1", first.connection_id, "task-1"))
        cleared = manager.clear_viewing("ws-1", second.connection_id, "task-1")
        self.assertIsNotNone(cleared)
        closed = manager.disconnect("ws-1", first.connection_id)
        self.assertFalse(closed.last_for_user)
        last = manager.disconnect("ws-1", second.connection_id)
        self.assertTrue(last.last_for_user)

    def test_failed_send_and_revocation_close_sockets(self):
        broken = FakeSocket(fail=True)
        watcher = FakeSocket()
        manager.connect("ws-1", "member-1", broken, "Ada Lane", "")
        manager.connect("ws-1", "admin-1", watcher, "Avery Lane", "")

        async def broadcast():
            await manager.broadcast_event(
                {
                    "type": "task.updated",
                    "workspace_id": "ws-1",
                    "actor_id": "admin-1",
                    "data": {"fields": ["title"]},
                }
            )

        asyncio.run(broadcast())
        self.assertEqual(manager.connection_count("ws-1"), 1)
        self.assertEqual(watcher.sent[-1]["type"], "presence.left")

        primary = FakeSocket()
        extra = FakeSocket()
        observer = FakeSocket()
        manager.connect("ws-1", "member-3", primary, "Mina Lane", "")
        manager.connect("ws-1", "member-3", extra, "Mina Lane", "")
        manager.connect("ws-1", "admin-1", observer, "Avery Lane", "")

        async def revoke():
            await manager.disconnect_user("ws-1", "member-3")

        asyncio.run(revoke())
        self.assertEqual(primary.closed, 4403)
        self.assertEqual(extra.closed, 4403)
        left = [message for message in observer.sent if message["type"] == "presence.left"]
        self.assertEqual(len(left), 1)
        self.assertEqual(left[0]["data"]["user_id"], "member-3")

    def test_comments_permissions_activity_and_no_event_on_rejection(self):
        viewer = self.client_for("viewer-1")
        member = self.client_for("member-1")
        other = self.client_for("member-3")
        admin = self.client_for("admin-1")
        stranger = self.client_for("member-2")

        denied = viewer.post("/api/v1/tasks/task-1/comments", json={"content": "Nope"})
        blank = member.post("/api/v1/tasks/task-1/comments", json={"content": "   "})
        missing = member.post("/api/v1/tasks/missing/comments", json={"content": "Hello"})
        outsider = stranger.get("/api/v1/tasks/task-1/comments")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(blank.status_code, 422)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(outsider.status_code, 403)
        self.assertEqual(self.db.comments.docs, [])
        self.assertEqual(self.events, [])

        first = member.post("/api/v1/tasks/task-1/comments", json={"content": "First note"})
        second = member.post("/api/v1/tasks/task-1/comments", json={"content": "Second note"})
        self.assertEqual(first.status_code, 201)
        body = first.json()
        self.assertEqual(body["author_name"], "Ada Lane")
        self.assertEqual(body["avatar_url"], "https://example.com/member-1.png")
        self.assertNotIn("email", body)
        self.assertEqual(self.db.activities.docs[-1]["metadata"]["excerpt"], "Second note")
        self.assertNotIn("content", self.db.activities.docs[-1]["metadata"])
        self.assertEqual([event["type"] for event in self.events], ["comment.created", "comment.created"])

        forbidden_edit = other.patch(
            "/api/v1/comments/{0}".format(body["id"]),
            json={"content": "Changed"},
        )
        self.assertEqual(forbidden_edit.status_code, 403)
        edited = member.patch("/api/v1/comments/{0}".format(body["id"]), json={"content": "Changed note"})
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(edited.json()["content"], "Changed note")

        forbidden_delete = other.delete("/api/v1/comments/{0}".format(body["id"]))
        viewer_delete = viewer.delete("/api/v1/comments/{0}".format(second.json()["id"]))
        self.assertEqual(forbidden_delete.status_code, 403)
        self.assertEqual(viewer_delete.status_code, 403)
        removed = admin.delete("/api/v1/comments/{0}".format(body["id"]))
        self.assertEqual(removed.status_code, 204)
        self.assertEqual(self.events[-1]["type"], "comment.deleted")
        self.assertEqual(self.events[-1]["data"]["comment_id"], body["id"])

        listed = viewer.get("/api/v1/tasks/task-1/comments")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([item["content"] for item in listed.json()["items"]], ["Second note"])

        page = member.get("/api/v1/workspaces/ws-1/activity?limit=1")
        self.assertEqual(page.status_code, 200)
        payload = page.json()
        self.assertEqual(payload["items"][0]["action"], "comment.deleted")
        self.assertEqual(payload["items"][0]["actor_name"], "Avery Lane")
        self.assertIsNotNone(payload["next_cursor"])
        older = member.get("/api/v1/workspaces/ws-1/activity?limit=1&cursor={0}".format(payload["next_cursor"]))
        self.assertEqual(older.status_code, 200)
        self.assertNotEqual(older.json()["items"][0]["id"], payload["items"][0]["id"])
        invalid = member.get("/api/v1/workspaces/ws-1/activity?cursor=nope")
        self.assertEqual(invalid.status_code, 422)

    def test_member_join_and_removal_are_recorded_once(self):
        invitation = Invitation(
            id=str(ObjectId()),
            workspace_id="ws-1",
            token_hash="hash",
            created_by="owner-1",
            expires_at=self.now + timedelta(days=1),
            created_at=self.now,
        )

        async def find_invitation(_token_hash):
            return invitation

        async def insert_member(document, session=None):
            self.add_member(document["workspace_id"], document["user_id"], MemberRole(document["role"]))
            return self.members[(document["workspace_id"], document["user_id"])]

        async def mark_used(*_args, **_kwargs):
            return None

        self.people["joiner"] = User(
            id="joiner",
            sub="auth0|joiner",
            given_name="Jules",
            family_name="Lane",
            picture="https://example.com/joiner.png",
        )
        with patch("app.repositories.invitations.find_by_token_hash", new=find_invitation), patch(
            "app.repositories.workspace_members.insert", new=insert_member
        ), patch("app.repositories.invitations.mark_used", new=mark_used):
            asyncio.run(join_invitation("raw-token", self.people["joiner"]))

        self.assertEqual(self.events[-1]["type"], "member.joined")
        self.assertEqual(self.events[-1]["data"]["name"], "Jules Lane")
        self.assertNotIn("email", self.events[-1]["data"])
        self.assertEqual(self.db.activities.docs[-1]["action"], "member.joined")
        self.assertIsNone(self.db.activities.docs[-1]["project_id"])

        socket = FakeSocket()
        manager.connect("ws-1", "member-1", socket, "Ada Lane", "")

        async def find_in_workspace(workspace_id, member_id):
            for member in self.members.values():
                if member.workspace_id == workspace_id and member.id == member_id:
                    return member
            return None

        async def delete_member(workspace_id, member_id):
            for key, member in list(self.members.items()):
                if member.workspace_id == workspace_id and member.id == member_id:
                    del self.members[key]
                    return True
            return False

        with patch("app.repositories.workspace_members.find_in_workspace", new=find_in_workspace), patch(
            "app.repositories.workspace_members.delete", new=delete_member
        ):
            asyncio.run(remove_member("ws-1", "mem-ws-1-member-1", self.people["owner-1"]))

        self.assertEqual(socket.closed, 4403)
        self.assertEqual(self.events[-1]["type"], "member.left")
        self.assertEqual(self.events[-1]["data"]["user_id"], "member-1")
        self.assertNotIn("email", self.events[-1]["data"])
        self.assertEqual(manager.connection_count("ws-1"), 0)

    def test_subscriber_failure_does_not_raise(self):
        def explode(_event):
            raise RuntimeError("subscriber failed")

        subscribe(explode)
        self.addCleanup(lambda: unsubscribe(explode))
        events.publish({"type": "task.updated", "workspace_id": "ws-1", "data": {}})

    def test_routes_are_registered(self):
        from app.main import app

        paths = {getattr(route, "path", "") for route in app.routes}
        self.assertIn("/api/v1/workspaces/{workspace_id}/ws-ticket", paths)
        self.assertIn("/ws/workspaces/{workspace_id}", paths)
        self.assertIn("/api/v1/tasks/{task_id}/comments", paths)
        self.assertIn("/api/v1/comments/{comment_id}", paths)
        self.assertIn("/api/v1/workspaces/{workspace_id}/activity", paths)


if __name__ == "__main__":
    unittest.main()
