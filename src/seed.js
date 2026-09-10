const { db } = require("./database");
const graph = require("./graph");

function seedDatabase() {

    const userCount = db
        .prepare("SELECT COUNT(*) AS count FROM users")
        .get();

    if (userCount.count === 0) {

        const insertUser = db.prepare(
            "INSERT INTO users (name) VALUES (?)"
        );

        const users = [
            "Alice",
            "Bob",
            "Charlie",
            "David",
            "Eve",
            "Frank"
        ];

        const userIds = {};

        users.forEach(name => {
            const result = insertUser.run(name);
            userIds[name] = result.lastInsertRowid;
        });

        const insertPost = db.prepare(`
            INSERT INTO posts (user_id, content)
            VALUES (?, ?)
        `);

        insertPost.run(userIds.Alice, "Hello from Alice!");
        insertPost.run(userIds.Bob, "Bob's first post");
        insertPost.run(userIds.Charlie, "Charlie is here!");
        insertPost.run(userIds.David, "David's update");

        const insertFollow = db.prepare(`
            INSERT INTO follows (follower_id, following_id)
            VALUES (?, ?)
        `);

        const relationships = [
            ["Alice", "Bob"],
            ["Alice", "Charlie"],
            ["Alice", "David"],

            ["Bob", "Alice"],
            ["Bob", "Charlie"],
            ["Bob", "David"],

            ["Charlie", "Alice"],
            ["Charlie", "Bob"],
            ["Charlie", "David"],

            ["David", "Alice"],
            ["David", "Bob"],

            ["Eve", "Alice"],
            ["Eve", "Charlie"],

            ["Frank", "Alice"],
            ["Frank", "David"]
        ];

        relationships.forEach(([from, to]) => {
            insertFollow.run(
                userIds[from],
                userIds[to]
            );
        });

        console.log("Demo data inserted.");
    }

    // Load database relationships into graph cache
    const relationships = db.prepare(`
        SELECT follower_id, following_id
        FROM follows
    `).all();

    relationships.forEach(row => {
        graph.addFollow(
            row.follower_id,
            row.following_id
        );
    });

    console.log("Graph cache loaded.");
}

module.exports = seedDatabase;