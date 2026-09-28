# Schema Explanation

## 1. users

Stores account-level information.

Why separate it?

Authentication/account facts are different from profile information.

Example:

```text
users
1 | alice@example.com | ...
```

The email belongs to the user account.

---

## 2. profiles

Stores public profile information.

`user_id` is both the primary key and a foreign key to `users`.

This creates a one-to-one relationship:

```text
users 1 ───── 1 profiles
```

---

## 3. posts

Every post belongs to one user.

```text
users 1 ───── many posts
```

We store `user_id`, not the username or email.

Why?

Because a user's username/email may change, while the relationship to the user remains identified by `user_id`.

---

## 4. media

A post can contain one or more media records.

```text
posts 1 ───── many media
```

The database stores metadata and a URL/path. The future media-storage member can place the real file in object storage.

---

## 5. follows

This is a many-to-many relationship between users.

A user can follow many users, and a user can be followed by many users.

The composite primary key:

```text
(follower_id, following_id)
```

prevents the same follow relationship from being inserted twice.

---

## 6. likes

This is another many-to-many relationship:

```text
users ↔ posts
```

The composite primary key:

```text
(user_id, post_id)
```

means one user can like a post only once.

---

## 7. comments

A comment belongs to a user and a post.

`parent_comment_id` allows replies to comments.

Because `parent_comment_id` points back to `comments.comment_id`, this is a self-referencing relationship.

---

## 8. hashtags and post_hashtags

Hashtags and posts are many-to-many.

Instead of storing:

```text
post.hashtags = "#database #postgresql #distributed"
```

we use separate tables.

This keeps the data structured and searchable.

---

## 9. notifications

A notification belongs to a receiving user and is caused by another user.

For example:

```text
Bob liked Alice's post
```

The receiving user is Alice.

The actor is Bob.

The post reference is optional because a follow notification does not need a post.

---

# Relationship summary

```text
users 1 ─── 1 profiles
users 1 ─── N posts
posts 1 ─── N media

users N ─── N users      (follows)
users N ─── N posts      (likes)

users 1 ─── N comments
posts 1 ─── N comments
comments 1 ─── N comments (replies)

posts N ─── N hashtags

users 1 ─── N notifications
```
