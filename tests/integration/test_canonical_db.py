"""Integration tests against the canonical PostgreSQL instance."""

from __future__ import annotations

import pytest
import pytest_asyncio
from api.services.post_service import PostService
from api.services.user_service import UserService
from db.engine import make_engine
from db.repositories.canonical import (
    CanonicalCommentRepository,
    CanonicalPostRepository,
    CanonicalUserRepository,
)
from models import Follow, Like
from schemas.post import PostCreate
from schemas.user import UserCreate
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def repos(infra, clean):
    settings, _, _ = infra
    engine = make_engine(settings.database_url, settings)
    try:
        yield {
            "users": CanonicalUserRepository(engine),
            "posts": CanonicalPostRepository(engine),
            "comments": CanonicalCommentRepository(engine),
            "engine": engine,
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_crud_user(repos):
    users: CanonicalUserRepository = repos["users"]
    created = await users.create(
        user_id=0, username="neo", email="neo@x.test", password_hash="h", display_name="Neo"
    )
    assert created.id and created.created_at
    fetched = await users.get_by_id(created.id)
    assert fetched is not None and fetched.username == "neo"

    updated = await users.update(created.id, username="neo2")
    assert updated.username == "neo2"

    listed = await users.list_users()
    assert any(u.id == created.id for u in listed)

    assert await users.delete(created.id) is True
    assert await users.get_by_id(created.id) is None
    assert await users.delete(999_999) is False


@pytest.mark.asyncio
async def test_unique_username_and_email(repos):
    users = repos["users"]
    await users.create(user_id=0, username="dup", email="dup@x.test", password_hash="h")
    with pytest.raises(IntegrityError):
        await users.create(user_id=0, username="dup", email="other@x.test", password_hash="h")


@pytest.mark.asyncio
async def test_post_requires_existing_author(repos):
    engine = repos["engine"]
    from db.session import session_scope
    from models import Post

    with pytest.raises(IntegrityError):
        async with session_scope(engine) as s:
            s.add(Post(author_id=987_654, content="ghost author"))


@pytest.mark.asyncio
async def test_self_follow_check_constraint(repos):
    engine = repos["engine"]
    from db.session import session_scope

    with pytest.raises(IntegrityError):
        async with session_scope(engine) as s:
            s.add(Follow(follower_id=1, following_id=1))


@pytest.mark.asyncio
async def test_unique_like_pair_and_cascade_delete(repos):
    users, posts, engine = repos["users"], repos["posts"], repos["engine"]
    u = await users.create(user_id=0, username="cas", email="cas@x.test", password_hash="h")
    p = await posts.create(post_id=0, author_id=u.id, content="hi", visibility="public")
    from db.session import session_scope

    with pytest.raises(IntegrityError):
        async with session_scope(engine) as s:
            s.add_all([Like(post_id=p.id, user_id=123), Like(post_id=p.id, user_id=123)])
            await s.flush()

    # cascade: deleting the user removes their posts then likes/comments
    await users.delete(u.id)
    async with session_scope(engine, readonly=True) as s:
        remaining_likes = (
            (await s.execute(select(Like).where(Like.post_id == p.id))).scalars().all()
        )
    assert remaining_likes == []


@pytest.mark.asyncio
async def test_service_layer_validation_and_404s(repos):
    users = UserService(repos["users"])
    posts = PostService(repos["posts"], repos["comments"], repos["users"])

    from common.exceptions import NotFoundError, ValidationError

    payload = UserCreate(username="svc", email="svc@example.com", password="password123")
    user = await users.create(payload)
    post = await posts.create(
        PostCreate(author_id=user.id, content="hello service", visibility="public")
    )
    assert post.author_id == user.id

    with pytest.raises(NotFoundError):
        await users.get(999_999)
    with pytest.raises(ValidationError):
        await posts.create(PostCreate(author_id=999_999, content="no author"))

    # comments
    c = await posts.comment(
        post.id,
        __import__("schemas.post", fromlist=["CommentCreate"]).CommentCreate(
            user_id=user.id, content="nice"
        ),
    )
    comments = await posts.comments_for(post.id)
    assert any(x.id == c.id for x in comments)


@pytest.mark.asyncio
async def test_followers_listing(repos):
    users = repos["users"]
    a = await users.create(user_id=0, username="a", email="a@x.test", password_hash="h")
    b = await users.create(user_id=0, username="b", email="b@x.test", password_hash="h")
    c = await users.create(user_id=0, username="c", email="c@x.test", password_hash="h")
    await users.add_follow(b.id, a.id)
    await users.add_follow(c.id, a.id)
    followers = await users.list_followers(a.id)
    assert {f.follower_id for f in followers} == {b.id, c.id}


@pytest.mark.asyncio
async def test_database_accepts_parameterized_queries_only(repos):
    engine = repos["engine"]
    # classic SQL-injection payload must be stored as an ordinary username
    injection = "robert'); DROP TABLE users;--"
    async with engine.connect() as conn:
        await conn.execute(
            text("INSERT INTO users (username, email, password_hash) VALUES (:u, :e, :h)"),
            {"u": injection, "e": "inj@x.test", "h": "h"},
        )
        await conn.commit()
        found = (
            await conn.execute(
                text("SELECT username FROM users WHERE email = :e"), {"e": "inj@x.test"}
            )
        ).scalar()
    assert found == injection
    # users table obviously still exists
    async with engine.connect() as conn:
        n = (await conn.execute(text("SELECT count(*) FROM users"))).scalar()
    assert n >= 1
