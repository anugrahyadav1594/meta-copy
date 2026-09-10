# Normalization Notes

## Why normalize first?

The goal is to create a clean source-of-truth database before adding distributed optimizations.

The project studies what happens when this normalized design later becomes expensive for large-scale read workloads.

---

## 1NF — First Normal Form

A table should contain atomic values.

Bad example:

```text
post_id | hashtags
1       | database,postgresql,distributed
```

Better:

```text
hashtags
--------
1 | database
2 | postgresql
3 | distributed
```

and connect them through:

```text
post_hashtags
```

Now each field contains one logical value.

---

## 2NF — Second Normal Form

2NF mainly matters when a table has a composite key.

For `likes`:

```text
PRIMARY KEY (user_id, post_id)
```

The non-key attribute `created_at` describes the complete like event.

It does not depend only on `user_id` or only on `post_id`.

Therefore there is no partial dependency.

The same idea applies to `follows` and `post_hashtags`.

---

## 3NF — Third Normal Form

The main rule used here is:

> Non-key attributes should depend on the key, the whole key, and nothing but the key.

Example of a bad `posts` table:

```text
post_id
user_id
username
email
caption
```

`username` and `email` depend on `user_id`, not directly on `post_id`.

So this introduces a transitive dependency.

Correct design:

```text
users
-----
user_id
email

profiles
--------
user_id
username

posts
-----
post_id
user_id
caption
```

Now post information belongs to `posts`, while user information belongs to `users`/`profiles`.

---

# Anomalies avoided

## Update anomaly

If Alice changes her username, we update her profile in one place.

We do not need to update every post.

## Insert anomaly

We can create a user/profile before that user creates any post.

## Delete anomaly

Deleting a post does not delete the user's account.

Foreign-key rules control dependent records explicitly.

---

# Why this matters to the larger project

This normalized schema is not the final scaling solution.

It is the baseline.

Later members can create faster read models using:

- denormalization
- sharding
- replication
- caching
- specialized indexes

The important idea is:

```text
Normalized source of truth
          ↓
Performance optimizations
          ↓
Distributed architecture
```
