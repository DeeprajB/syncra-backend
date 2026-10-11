import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from bson import ObjectId
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

from app.api.routes.comments import router as comments_router
from app.api.routes.notifications import router as notifications_router
from app.api.routes.tasks import router as tasks_router
from app.core.events import subscribe, unsubscribe
from app.models.auth.user import User
from app.models.project import Project, ProjectStatus
from app.models.workspace import Workspace
from app.models.workspace_member import MemberRole, WorkspaceMember
from app.realtime.manager import manager
from app.services.auth import sync_current_user


class _WriteResult:
    def __init__(self, inserted_id=None, matched_count=0, modified_count=0):
        self.inserted_id = inserted_id
        self.matched_count = matched_count
        self.modified_count = modified_count


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
                for key, amount in update.get("$inc", {}).items():
                    document[key] = document.get(key, 0) + amount
                return _WriteResult(matched_count=1, modified_count=1)
        return _WriteResult()

    async def update_many(self, query, update, session=None):
        count = 0
        for document in self.docs:
            if _matches(document, query):
                document.update(update.get("$set", {}))
                count += 1
        return _WriteResult(matched_count=count, modified_count=count)

    async def count_documents(self, query, session=None):
        return sum(1 for document in self.docs if _matches(document, query))

    async def create_index(self, keys, **kwargs):
        self.indexes.append(kwargs.get("name"))
        return kwargs.get("name")


class _Database:
    def __init__(self):
        self.tasks = _Collection()
        self.comments = _Collection()
        self.activities = _Collection()
        self.notifications = _Collection()


class FakeSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


class _Immediate:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *_args):
        return False


def _transaction():
    return _Immediate()


class NotificationApiTests(unittest.TestCase):
    def setUp(self):
        self.db = _Database()
        self.workspaces = {}
        self.members = {}
        self.projects = {}
        self.events = []
        self.now = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
        manager.reset()
        self.addCleanup(manager.reset)

        async def find_workspace(workspace_id):
            return self.workspaces.get(workspace_id)

        async def find_member(workspace_id, user_id):
            return self.members.get((workspace_id, user_id))

        async def find_project(project_id):
            return self.projects.get(project_id)

        def collect(event):
            self.events.append(event)

        subscribe(collect)
        self.addCleanup(lambda: unsubscribe(collect))
        self.patches = [
            patch("app.repositories.tasks.database", self.db),
            patch("app.repositories.comments.database", self.db),
            patch("app.repositories.activities.database", self.db),
            patch("app.repositories.notifications.database", self.db),
            patch("app.repositories.workspaces.find_active_by_id", new=find_workspace),
            patch("app.repositories.workspace_members.find_by_workspace_and_user", new=find_member),
            patch("app.repositories.projects.find_active_by_id", new=find_project),
            patch("app.services.task.transaction", _transaction),
            patch("app.services.comment.transaction", _transaction),
            patch("app.services.task.utc_now", side_effect=lambda: self.now),
            patch("app.repositories.tasks.utc_now", side_effect=lambda: self.now),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

        self.add_workspace("ws-1")
        self.add_member("ws-1", "member-1", MemberRole.MEMBER)
        self.add_member("ws-1", "admin-1", MemberRole.ADMIN)
        self.add_member("ws-1", "member-3", MemberRole.MEMBER)
        self.add_member("ws-1", "viewer-1", MemberRole.VIEWER)
        self.add_project("proj-1", "ws-1")

    def add_workspace(self, workspace_id):
        self.workspaces[workspace_id] = Workspace(
            id=workspace_id,
            title="Studio",
            description=None,
            owner_id="admin-1",
            created_at=self.now,
            updated_at=self.now,
        )

    def add_member(self, workspace_id, user_id, role):
        self.members[(workspace_id, user_id)] = WorkspaceMember(
            id="mem-{0}".format(user_id),
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
            owner_id="admin-1",
            created_by="admin-1",
            created_at=self.now,
            updated_at=self.now,
        )

    def client_for(self, user_id):
        user = User(
            id=user_id,
            sub="auth0|{0}".format(user_id),
            email="{0}@example.com".format(user_id),
            given_name=user_id,
        )
        application = FastAPI()
        application.include_router(tasks_router)
        application.include_router(comments_router)
        application.include_router(notifications_router)
        application.dependency_overrides[sync_current_user] = lambda: user
        return TestClient(application)

    def test_assignment_notifies_only_the_new_assignee(self):
        created = self.client_for("member-1").post(
            "/api/v1/projects/proj-1/tasks",
            json={"title": "Design API schema", "assignee_id": "admin-1"},
        )
        self_assigned = self.client_for("member-1").post(
            "/api/v1/projects/proj-1/tasks",
            json={"title": "Mine", "assignee_id": "member-1"},
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(self_assigned.status_code, 201)

        task_id = created.json()["id"]
        reassigned = self.client_for("member-1").patch(
            "/api/v1/tasks/{0}".format(task_id),
            json={"assignee_id": "member-3"},
        )
        self.assertEqual(reassigned.status_code, 200)

        admin = self.client_for("admin-1").get("/api/v1/notifications")
        actor = self.client_for("member-1").get("/api/v1/notifications")
        newest = self.client_for("member-3").get("/api/v1/notifications")
        self.assertEqual(admin.status_code, 200)
        self.assertEqual(len(admin.json()["items"]), 1)
        self.assertEqual(admin.json()["items"][0]["type"], "task.assigned")
        self.assertEqual(admin.json()["items"][0]["task_id"], task_id)
        self.assertEqual(actor.json()["items"], [])
        self.assertEqual(len(newest.json()["items"]), 1)
        self.assertEqual(newest.json()["items"][0]["title"], "member-1 assigned you a task")
        self.assertNotIn("email", newest.json()["items"][0])

        hidden = self.client_for("member-1").patch(
            "/api/v1/notifications/{0}/read".format(newest.json()["items"][0]["id"])
        )
        self.assertEqual(hidden.status_code, 404)

    def test_comments_notify_assignee_and_participants_except_the_author(self):
        created = self.client_for("member-1").post(
            "/api/v1/projects/proj-1/tasks",
            json={"title": "Design API schema", "assignee_id": "admin-1"},
        )
        task_id = created.json()["id"]
        first = self.client_for("member-1").post(
            "/api/v1/tasks/{0}/comments".format(task_id),
            json={"content": "Looks good"},
        )
        second = self.client_for("member-3").post(
            "/api/v1/tasks/{0}/comments".format(task_id),
            json={"content": "I can take the next step"},
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)

        admin = self.client_for("admin-1").get("/api/v1/notifications")
        author = self.client_for("member-1").get("/api/v1/notifications")
        commenter = self.client_for("member-3").get("/api/v1/notifications")
        self.assertEqual(
            [item["type"] for item in admin.json()["items"]],
            ["comment.created", "comment.created", "task.assigned"],
        )
        self.assertEqual(len(author.json()["items"]), 1)
        self.assertEqual(author.json()["items"][0]["body"], "I can take the next step")
        self.assertEqual(commenter.json()["items"], [])
        self.assertEqual(self.client_for("admin-1").get("/api/v1/notifications/unread-count").json()["count"], 3)

        denied = self.client_for("viewer-1").post(
            "/api/v1/tasks/{0}/comments".format(task_id),
            json={"content": "Nope"},
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.client_for("viewer-1").get("/api/v1/notifications").json()["items"], [])

    def test_read_state_and_pagination_belong_to_the_recipient(self):
        created = self.client_for("member-1").post(
            "/api/v1/projects/proj-1/tasks",
            json={"title": "Design API schema", "assignee_id": "admin-1"},
        )
        task_id = created.json()["id"]
        for text in ("One", "Two", "Three"):
            posted = self.client_for("member-1").post(
                "/api/v1/tasks/{0}/comments".format(task_id),
                json={"content": text},
            )
            self.assertEqual(posted.status_code, 201)

        inbox = self.client_for("admin-1")
        page = inbox.get("/api/v1/notifications?limit=1")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.json()["items"][0]["body"], "Three")
        self.assertIsNotNone(page.json()["next_cursor"])
        older = inbox.get("/api/v1/notifications?limit=1&cursor={0}".format(page.json()["next_cursor"]))
        self.assertEqual(older.json()["items"][0]["body"], "Two")
        self.assertEqual(inbox.get("/api/v1/notifications?cursor=nope").status_code, 422)

        marked = inbox.patch("/api/v1/notifications/{0}/read".format(page.json()["items"][0]["id"]))
        self.assertEqual(marked.status_code, 200)
        self.assertIsNotNone(marked.json()["read_at"])
        self.assertEqual(inbox.get("/api/v1/notifications/unread-count").json()["count"], 3)
        cleared = inbox.post("/api/v1/notifications/read-all")
        self.assertEqual(cleared.status_code, 204)
        self.assertEqual(inbox.get("/api/v1/notifications/unread-count").json()["count"], 0)

    def test_notification_failure_does_not_fail_assignment(self):
        async def explode(_document):
            raise RuntimeError("notifications down")

        with patch("app.repositories.notifications.insert", new=explode):
            created = self.client_for("member-1").post(
                "/api/v1/projects/proj-1/tasks",
                json={"title": "Still saved", "assignee_id": "admin-1"},
            )
        self.assertEqual(created.status_code, 201)
        self.assertEqual([event["type"] for event in self.events], ["task.created"])

    def test_duplicate_notification_is_not_published_twice(self):
        calls = {"count": 0}

        async def once(document):
            calls["count"] += 1
            if calls["count"] > 1:
                raise DuplicateKeyError("duplicate")
            stored = dict(document)
            stored["_id"] = ObjectId()
            self.db.notifications.docs.append(stored)
            from app.repositories.notifications import serialize_notification

            return serialize_notification(stored)

        with patch("app.repositories.notifications.insert", new=once):
            first = self.client_for("member-1").post(
                "/api/v1/projects/proj-1/tasks",
                json={"title": "Once", "assignee_id": "admin-1"},
            )
            self.assertEqual(first.status_code, 201)
            from app.services import notification as notification_service

            asyncio.run(
                notification_service.notify_task_assigned(
                    workspace_id="ws-1",
                    project_id="proj-1",
                    task_id=first.json()["id"],
                    task_title="Once",
                    actor=User(id="member-1", sub="auth0|member-1", given_name="member-1"),
                    recipient_id="admin-1",
                    version=1,
                )
            )
        published = [event for event in self.events if event["type"] == "notification.created"]
        self.assertEqual(len(published), 1)
        self.assertEqual(len(self.db.notifications.docs), 1)

    def test_notification_event_reaches_only_the_recipient(self):
        assignee = FakeSocket()
        stranger = FakeSocket()
        manager.connect("ws-1", "admin-1", assignee, "Admin", "")
        manager.connect("ws-1", "member-3", stranger, "Mina", "")

        async def deliver():
            await manager.broadcast_event(
                {
                    "type": "notification.created",
                    "workspace_id": "ws-1",
                    "recipient_id": "admin-1",
                    "actor_id": "member-1",
                    "data": {"title": "member-1 assigned you a task"},
                }
            )

        asyncio.run(deliver())
        self.assertEqual(assignee.sent[0]["type"], "notification.created")
        self.assertEqual(stranger.sent, [])


if __name__ == "__main__":
    unittest.main()
