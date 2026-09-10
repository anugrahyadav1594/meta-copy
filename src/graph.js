class SocialGraph {
    constructor() {
        // userId -> Set of users they follow
        this.following = new Map();

        // userId -> Set of users who follow them
        this.followers = new Map();
    }

    addUser(userId) {
        if (!this.following.has(userId)) {
            this.following.set(userId, new Set());
        }

        if (!this.followers.has(userId)) {
            this.followers.set(userId, new Set());
        }
    }

    addFollow(followerId, followingId) {
        this.addUser(followerId);
        this.addUser(followingId);

        this.following.get(followerId).add(followingId);
        this.followers.get(followingId).add(followerId);
    }

    removeFollow(followerId, followingId) {
        if (this.following.has(followerId)) {
            this.following.get(followerId).delete(followingId);
        }

        if (this.followers.has(followingId)) {
            this.followers.get(followingId).delete(followerId);
        }
    }

    getFollowing(userId) {
        return Array.from(
            this.following.get(userId) || []
        );
    }

    getFollowers(userId) {
        return Array.from(
            this.followers.get(userId) || []
        );
    }

    getFriends(userId) {
        const following = this.following.get(userId) || new Set();
        const followers = this.followers.get(userId) || new Set();

        return Array.from(
            [...following].filter(
                user => followers.has(user)
            )
        );
    }

    getMutual(userId, otherUserId) {
        const first = this.following.get(userId) || new Set();
        const second = this.following.get(otherUserId) || new Set();

        return Array.from(
            [...first].filter(
                user => second.has(user)
            )
        );
    }

    getGraph(userId) {
        const following = this.getFollowing(userId);
        const followers = this.getFollowers(userId);

        const nodes = new Set([
            userId,
            ...following,
            ...followers
        ]);

        const edges = [];

        following.forEach(target => {
            edges.push({
                from: userId,
                to: target,
                type: "FOLLOWS"
            });
        });

        followers.forEach(source => {
            edges.push({
                from: source,
                to: userId,
                type: "FOLLOWS"
            });
        });

        return {
            nodes: Array.from(nodes),
            edges
        };
    }
}

const graph = new SocialGraph();

module.exports = graph;