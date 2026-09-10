const express = require("express");

const router = express.Router();

const { db } = require("../database");
const graph = require("../graph");


function getUsersByIds(ids) {

    if (ids.length === 0) {
        return [];
    }

    const placeholders = ids
        .map(() => "?")
        .join(",");

    return db.prepare(`
        SELECT id, name
        FROM users
        WHERE id IN (${placeholders})
    `).all(...ids);
}


/*
==================================================
USER CRUD
==================================================
*/


/*
    CREATE USER

    POST /api/users

    Body:
    {
        "name": "Abhishek"
    }
*/

router.post("/users", (req, res) => {

    const { name } = req.body;

    if (!name || !name.trim()) {
        return res.status(400).json({
            error: "Name is required"
        });
    }

    const result = db.prepare(`
        INSERT INTO users (name)
        VALUES (?)
    `).run(name.trim());

    const user = db.prepare(`
        SELECT id, name
        FROM users
        WHERE id = ?
    `).get(result.lastInsertRowid);

    // Add new user to graph cache
    graph.addUser(user.id);

    res.status(201).json({
        message: "User created",
        user
    });
});


/*
    GET ALL USERS

    GET /api/users
*/

router.get("/users", (req, res) => {

    const users = db.prepare(`
        SELECT id, name
        FROM users
        ORDER BY id
    `).all();

    res.json({
        count: users.length,
        users
    });
});


/*
    GET ONE USER

    GET /api/users/:id
*/

router.get("/users/:id", (req, res) => {

    const userId = Number(req.params.id);

    const user = db.prepare(`
        SELECT id, name
        FROM users
        WHERE id = ?
    `).get(userId);

    if (!user) {
        return res.status(404).json({
            error: "User not found"
        });
    }

    res.json(user);
});


/*
    UPDATE USER

    PUT /api/users/:id

    Body:
    {
        "name": "New Name"
    }
*/

router.put("/users/:id", (req, res) => {

    const userId = Number(req.params.id);
    const { name } = req.body;

    if (!name || !name.trim()) {
        return res.status(400).json({
            error: "Name is required"
        });
    }

    const result = db.prepare(`
        UPDATE users
        SET name = ?
        WHERE id = ?
    `).run(
        name.trim(),
        userId
    );

    if (result.changes === 0) {
        return res.status(404).json({
            error: "User not found"
        });
    }

    const user = db.prepare(`
        SELECT id, name
        FROM users
        WHERE id = ?
    `).get(userId);

    res.json({
        message: "User updated",
        user
    });
});


/*
    DELETE USER

    DELETE /api/users/:id
*/

router.delete("/users/:id", (req, res) => {

    const userId = Number(req.params.id);

    const user = db.prepare(`
        SELECT id, name
        FROM users
        WHERE id = ?
    `).get(userId);

    if (!user) {
        return res.status(404).json({
            error: "User not found"
        });
    }


    /*
        Delete everything related to this user.

        This keeps the database clean.
    */

    const deleteUser = db.transaction(() => {

        // Delete likes on user's posts
        db.prepare(`
            DELETE FROM likes
            WHERE post_id IN (
                SELECT id
                FROM posts
                WHERE user_id = ?
            )
        `).run(userId);


        // Delete comments made by user
        db.prepare(`
            DELETE FROM comments
            WHERE user_id = ?
        `).run(userId);


        // Delete comments on user's posts
        db.prepare(`
            DELETE FROM comments
            WHERE post_id IN (
                SELECT id
                FROM posts
                WHERE user_id = ?
            )
        `).run(userId);


        // Delete likes made by user
        db.prepare(`
            DELETE FROM likes
            WHERE user_id = ?
        `).run(userId);


        // Delete group memberships
        db.prepare(`
            DELETE FROM group_members
            WHERE user_id = ?
        `).run(userId);


        // Delete follow relationships
        db.prepare(`
            DELETE FROM follows
            WHERE follower_id = ?
               OR following_id = ?
        `).run(
            userId,
            userId
        );


        // Delete user's posts
        db.prepare(`
            DELETE FROM posts
            WHERE user_id = ?
        `).run(userId);


        // Finally delete user
        db.prepare(`
            DELETE FROM users
            WHERE id = ?
        `).run(userId);

    });


    deleteUser();


    /*
        Remove user from graph cache
    */

    graph.following.delete(userId);
    graph.followers.delete(userId);


    // Remove user from other adjacency lists

    for (const set of graph.following.values()) {
        set.delete(userId);
    }

    for (const set of graph.followers.values()) {
        set.delete(userId);
    }


    res.json({
        message: "User deleted",
        user
    });
});


/*
==================================================
SOCIAL GRAPH APIs
==================================================
*/


/*
    GET FOLLOWING

    GET /api/following/:id
*/

router.get("/following/:id", (req, res) => {

    const userId = Number(req.params.id);

    const user = db.prepare(`
        SELECT id, name
        FROM users
        WHERE id = ?
    `).get(userId);

    if (!user) {
        return res.status(404).json({
            error: "User not found"
        });
    }

    const ids = graph.getFollowing(userId);

    const users = getUsersByIds(ids);

    res.json({
        user,
        count: users.length,
        following: users
    });
});


/*
    GET FOLLOWERS

    GET /api/followers/:id
*/

router.get("/followers/:id", (req, res) => {

    const userId = Number(req.params.id);

    const user = db.prepare(`
        SELECT id, name
        FROM users
        WHERE id = ?
    `).get(userId);

    if (!user) {
        return res.status(404).json({
            error: "User not found"
        });
    }

    const ids = graph.getFollowers(userId);

    const users = getUsersByIds(ids);

    res.json({
        user,
        count: users.length,
        followers: users
    });
});


/*
    GET FRIENDS

    Friends = people who follow each other

    GET /api/friends/:id
*/

router.get("/friends/:id", (req, res) => {

    const userId = Number(req.params.id);

    const user = db.prepare(`
        SELECT id, name
        FROM users
        WHERE id = ?
    `).get(userId);

    if (!user) {
        return res.status(404).json({
            error: "User not found"
        });
    }

    const ids = graph.getFriends(userId);

    const users = getUsersByIds(ids);

    res.json({
        user,
        count: users.length,
        friends: users
    });
});


/*
    GET MUTUAL

    GET /api/mutual/:id/:otherId
*/

router.get(
    "/mutual/:id/:otherId",
    (req, res) => {

        const userId = Number(req.params.id);
        const otherId = Number(req.params.otherId);

        const user1 = db.prepare(`
            SELECT id, name
            FROM users
            WHERE id = ?
        `).get(userId);

        const user2 = db.prepare(`
            SELECT id, name
            FROM users
            WHERE id = ?
        `).get(otherId);

        if (!user1 || !user2) {
            return res.status(404).json({
                error: "One or both users not found"
            });
        }

        const ids = graph.getMutual(
            userId,
            otherId
        );

        const users = getUsersByIds(ids);

        res.json({
            user1,
            user2,
            count: users.length,
            mutual: users
        });
    }
);


/*
    POST FOLLOW

    POST /api/follow

    Body:
    {
        "follower_id": 1,
        "following_id": 5
    }
*/

router.post("/follow", (req, res) => {

    const followerId = Number(req.body.follower_id);
    const followingId = Number(req.body.following_id);

    if (!followerId || !followingId) {
        return res.status(400).json({
            error: "follower_id and following_id are required"
        });
    }

    if (followerId === followingId) {
        return res.status(400).json({
            error: "A user cannot follow themselves"
        });
    }

    const follower = db.prepare(`
        SELECT id, name
        FROM users
        WHERE id = ?
    `).get(followerId);

    const following = db.prepare(`
        SELECT id, name
        FROM users
        WHERE id = ?
    `).get(followingId);

    if (!follower || !following) {
        return res.status(404).json({
            error: "User not found"
        });
    }

    const existing = db.prepare(`
        SELECT *
        FROM follows
        WHERE follower_id = ?
        AND following_id = ?
    `).get(
        followerId,
        followingId
    );

    if (existing) {
        return res.status(409).json({
            error: "Already following"
        });
    }

    db.prepare(`
        INSERT INTO follows
        (follower_id, following_id)
        VALUES (?, ?)
    `).run(
        followerId,
        followingId
    );


    // Update graph cache

    graph.addFollow(
        followerId,
        followingId
    );


    res.status(201).json({

        message: "Follow relationship created",

        relationship: {
            from: follower,
            to: following,
            type: "FOLLOWS"
        }

    });
});


/*
    DELETE FOLLOW

    DELETE /api/follow

    Body:
    {
        "follower_id": 1,
        "following_id": 5
    }
*/

router.delete("/follow", (req, res) => {

    const followerId = Number(req.body.follower_id);
    const followingId = Number(req.body.following_id);

    const result = db.prepare(`
        DELETE FROM follows
        WHERE follower_id = ?
        AND following_id = ?
    `).run(
        followerId,
        followingId
    );

    if (result.changes === 0) {
        return res.status(404).json({
            error: "Follow relationship not found"
        });
    }


    // Update graph cache

    graph.removeFollow(
        followerId,
        followingId
    );


    res.json({
        message: "Follow relationship removed"
    });
});


/*
    GET GRAPH

    GET /api/graph/:id
*/

router.get("/graph/:id", (req, res) => {

    const userId = Number(req.params.id);

    const user = db.prepare(`
        SELECT id, name
        FROM users
        WHERE id = ?
    `).get(userId);

    if (!user) {
        return res.status(404).json({
            error: "User not found"
        });
    }

    const graphData = graph.getGraph(userId);

    const nodes = getUsersByIds(
        graphData.nodes
    );

    res.json({
        center: user,
        nodes,
        edges: graphData.edges
    });
});


module.exports = router;