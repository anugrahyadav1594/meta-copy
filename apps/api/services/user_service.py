"""User/follow business logic (mode-agnostic: depends on abstract repos)."""

from __future__ import annotations

from typing import Any

from common.exceptions import ConflictError, NotFoundError
from schemas.user import UserCreate, UserUpdate
from sqlalchemy.exc import IntegrityError

from api.services.security import hash_password


class UserService:
    def __init__(self, user_repo: Any, event_bus: Any = None) -> None:
        self.repo = user_repo
        self.events = event_bus

    async def _publish(self, event_type: str, aggregate_id: int, **payload: Any) -> None:
        if self.events is None:
            return
        from events.bus import DomainEvent

        try:
            await self.events.publish(
                DomainEvent(
                    event_type=event_type,
                    aggregate="user",
                    aggregate_id=int(aggregate_id),
                    payload=dict(payload),
                )
            )
        except Exception:  # noqa: BLE001 - derived systems must never break writes
            pass

    async def get(self, user_id: int) -> Any:
        user = await self.repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError(f"user {user_id} not found", {"user_id": user_id})
        return user

    async def create(self, payload: UserCreate) -> Any:
        encoded = hash_password(payload.password)
        try:
            user = await self.repo.create(
                user_id=0,  # repository assigns an application/global id
                username=payload.username,
                email=payload.email,
                password_hash=encoded,
                display_name=payload.display_name,
            )
        except IntegrityError as exc:
            raise ConflictError(
                "username or email already exists",
                {"constraint": str(getattr(exc.orig, "diag", getattr(exc.orig, "msg", "")))[:120]},
            ) from exc
        await self._publish(
            "USER_CREATED",
            user.user_id,
            user_id=user.user_id,
            username=user.username,
            display_name=payload.display_name,
        )
        return user

    async def update(self, user_id: int, payload: UserUpdate) -> Any:
        data = payload.model_dump(exclude_unset=True)
        data.pop("password", None)
        if "email" in data and data["email"] is None:
            data.pop("email")
        if "username" in data and data["username"] is None:
            data.pop("username")
        try:
            return await self.repo.update(user_id, **data)
        except LookupError as exc:
            raise NotFoundError(str(exc), {"user_id": user_id}) from exc
        except IntegrityError as exc:
            raise ConflictError("username or email already exists") from exc

    async def delete(self, user_id: int) -> bool:
        deleted = await self.repo.delete(user_id)
        if not deleted:
            raise NotFoundError(f"user {user_id} not found", {"user_id": user_id})
        return True

    async def list(self, limit: int = 50, offset: int = 0) -> list[Any]:
        if hasattr(self.repo, "list_users"):
            return await self.repo.list_users(limit=limit, offset=offset)
        return []

    async def followers(self, user_id: int, limit: int = 50) -> list[Any]:
        await self.get(user_id)  # 404 if the target user does not exist
        return await self.repo.list_followers(user_id, limit=limit)

    async def follow(self, follower_id: int, following_id: int) -> bool:
        if follower_id == following_id:
            from common.exceptions import ValidationError

            raise ValidationError("a user cannot follow themselves")
        # add_follow() returns True only when a NEW edge was created.
        created = await self.repo.add_follow(follower_id, following_id)
        if created:
            await self._publish(
                "FOLLOW_CREATED",
                following_id,
                follower_id=follower_id,
                following_id=following_id,
            )
        return bool(created)

    async def unfollow(self, follower_id: int, following_id: int) -> bool:
        removed = await self.repo.remove_follow(follower_id, following_id)
        if removed:
            await self._publish(
                "FOLLOW_DELETED",
                following_id,
                follower_id=follower_id,
                following_id=following_id,
            )
            if self.events is not None:
                # the fan-out/graph projections cached this edge
                pass
        return bool(removed)

    async def following(self, user_id: int, limit: int = 50) -> list[Any]:
        await self.get(user_id)
        return await self.repo.list_following(user_id, limit=limit)
