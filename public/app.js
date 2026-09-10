/* ═══════════════════════════════════════════════
   TAO Social Graph  ·  app.js
   ═══════════════════════════════════════════════ */

"use strict";

/* ──────────── DOM references ──────────── */
const $ = id => document.getElementById(id);

const userSelect       = $("userSelect");
const updateUserSelect = $("updateUserSelect");
const deleteUserSelect = $("deleteUserSelect");
const followerSelect   = $("followerSelect");
const followingSelect  = $("followingSelect");
const mutualSelect     = $("mutualSelect");

const addUserInput    = $("addUserInput");
const updateUserInput = $("updateUserInput");

const addUserBtn     = $("addUserBtn");
const updateUserBtn  = $("updateUserBtn");
const deleteUserBtn  = $("deleteUserBtn");
const refreshUsersBtn= $("refreshUsersBtn");
const followBtn      = $("followBtn");
const unfollowBtn    = $("unfollowBtn");
const loadGraphBtn   = $("loadGraphBtn");
const mutualBtn      = $("mutualBtn");

const statusBadge    = $("statusBadge");
const toastContainer = $("toastContainer");
const graphSvg       = $("graphSvg");
const graphNodes     = $("graphNodes");
const graphEmpty     = $("graphEmpty");
const graphContainer = $("graphContainer");
const relPanel       = $("relPanel");
const relPanelTitle  = $("relPanelTitle");
const relPanelIcon   = $("relPanelIcon");
const relPanelBody   = $("relPanelBody");
const relPanelClose  = $("relPanelClose");
const mutualResult   = $("mutualResult");

const followingCount  = $("followingCount");
const followersCount  = $("followersCount");
const friendsCount    = $("friendsCount");

/* ──────────── State ──────────── */
let allUsers   = [];       // [{id, name}, …]
let activeType = null;     // currently expanded relationship type

/* ═══════════════════════════════════════════════
   UTILITIES
   ═══════════════════════════════════════════════ */

function setStatus(type) {
    statusBadge.className = "status-badge status-" + type;
    const labels = { idle:"Idle", loading:"Loading…", success:"Success", error:"Error" };
    statusBadge.textContent = labels[type] || type;
}

function showToast(message, type = "info") {
    const icons = { success:"✅", error:"❌", info:"ℹ️" };
    const el = document.createElement("div");
    el.className = "toast " + type;
    el.innerHTML = `<span class="toast-icon">${icons[type]}</span><span class="toast-msg">${message}</span>`;
    toastContainer.prepend(el);
    setTimeout(() => {
        el.classList.add("fade-out");
        el.addEventListener("animationend", () => el.remove());
    }, 4000);
}

function setLoading(btn, loading) {
    if (!btn) return;
    btn.disabled = loading;
    if (loading) {
        btn._origText = btn.innerHTML;
        btn.innerHTML = `<span class="btn-spinner"></span>`;
    } else {
        btn.innerHTML = btn._origText || btn.innerHTML;
    }
}

function avatarInitials(name) {
    return name.split(" ").map(w => w[0]).join("").toUpperCase().slice(0, 2);
}

function buildUserPills(users, emptyMsg) {
    if (!users || users.length === 0) {
        return `<span class="rel-empty">${emptyMsg || "None found."}</span>`;
    }
    return users.map(u => `
        <div class="user-pill">
            <div class="user-pill-avatar">${avatarInitials(u.name)}</div>
            <div>
                <div class="user-pill-name">${escHtml(u.name)}</div>
                <div class="user-pill-id">ID #${u.id}</div>
            </div>
        </div>
    `).join("");
}

function escHtml(str) {
    return String(str)
        .replace(/&/g,"&amp;")
        .replace(/</g,"&lt;")
        .replace(/>/g,"&gt;")
        .replace(/"/g,"&quot;");
}

/* ═══════════════════════════════════════════════
   API HELPERS
   ═══════════════════════════════════════════════ */

async function apiFetch(url, options = {}) {
    const res = await fetch(url, {
        headers: { "Content-Type": "application/json" },
        ...options
    });
    const data = await res.json();
    return { ok: res.ok, status: res.status, data };
}

/* ═══════════════════════════════════════════════
   POPULATE DROPDOWNS
   ═══════════════════════════════════════════════ */

function populateDropdowns(users, preserveSelectedId) {
    const selects = [userSelect, updateUserSelect, deleteUserSelect,
                     followerSelect, followingSelect, mutualSelect];

    selects.forEach(sel => {
        const prev = preserveSelectedId !== undefined
            ? preserveSelectedId
            : Number(sel.value) || null;

        // Keep first placeholder option
        const placeholder = sel.options[0];
        sel.innerHTML = "";
        sel.appendChild(placeholder);

        users.forEach(u => {
            const opt = document.createElement("option");
            opt.value = u.id;
            opt.textContent = `${u.name}  (ID #${u.id})`;
            sel.appendChild(opt);
        });

        if (prev) sel.value = prev;
    });
}

/* ═══════════════════════════════════════════════
   FETCH ALL USERS
   ═══════════════════════════════════════════════ */

async function fetchUsers(selectNewId) {
    setStatus("loading");
    try {
        const { ok, data } = await apiFetch("/api/users");
        if (!ok) throw new Error(data.error || "Failed to load users");
        allUsers = data.users || [];
        populateDropdowns(allUsers, selectNewId);
        setStatus("idle");
        return allUsers;
    } catch (err) {
        setStatus("error");
        showToast("Could not load users: " + err.message, "error");
        return [];
    }
}

/* ═══════════════════════════════════════════════
   USER MANAGEMENT
   ═══════════════════════════════════════════════ */

/* ── Add User ── */
async function addUser() {
    const name = addUserInput.value.trim();
    if (!name) { showToast("Please enter a name.", "error"); return; }

    setLoading(addUserBtn, true);
    setStatus("loading");

    try {
        const { ok, data } = await apiFetch("/api/users", {
            method: "POST",
            body: JSON.stringify({ name })
        });

        if (!ok) throw new Error(data.error || "Failed to create user");

        addUserInput.value = "";
        showToast(`User "${data.user.name}" created (ID #${data.user.id})`, "success");
        setStatus("success");

        // Refresh all dropdowns and select the new user
        await fetchUsers(data.user.id);
        userSelect.value = data.user.id;

    } catch (err) {
        showToast(err.message, "error");
        setStatus("error");
    } finally {
        setLoading(addUserBtn, false);
    }
}

/* ── Update User ── */
async function updateUser() {
    const id   = Number(updateUserSelect.value);
    const name = updateUserInput.value.trim();
    if (!id)   { showToast("Select a user to update.", "error"); return; }
    if (!name) { showToast("Enter a new name.", "error"); return; }

    setLoading(updateUserBtn, true);
    setStatus("loading");

    try {
        const { ok, data } = await apiFetch(`/api/users/${id}`, {
            method: "PUT",
            body: JSON.stringify({ name })
        });

        if (!ok) throw new Error(data.error || "Update failed");

        updateUserInput.value = "";
        showToast(`User updated → "${data.user.name}"`, "success");
        setStatus("success");

        const prevMain = Number(userSelect.value) || null;
        await fetchUsers(prevMain);
        if (prevMain === id) refreshStats();

    } catch (err) {
        showToast(err.message, "error");
        setStatus("error");
    } finally {
        setLoading(updateUserBtn, false);
    }
}

/* ── Delete User ── */
async function deleteUser() {
    const id = Number(deleteUserSelect.value);
    if (!id) { showToast("Select a user to delete.", "error"); return; }

    const user = allUsers.find(u => u.id === id);
    const userName = user ? user.name : `ID #${id}`;

    if (!confirm(`Delete "${userName}"? This will also remove all their relationships.`)) return;

    setLoading(deleteUserBtn, true);
    setStatus("loading");

    try {
        const { ok, data } = await apiFetch(`/api/users/${id}`, { method: "DELETE" });
        if (!ok) throw new Error(data.error || "Delete failed");

        showToast(`User "${userName}" deleted.`, "success");
        setStatus("success");

        // If deleted user was selected, clear stats
        if (Number(userSelect.value) === id) {
            resetStats();
            clearGraph();
            relPanel.style.display = "none";
        }

        await fetchUsers();

    } catch (err) {
        showToast(err.message, "error");
        setStatus("error");
    } finally {
        setLoading(deleteUserBtn, false);
    }
}

/* ═══════════════════════════════════════════════
   FOLLOW / UNFOLLOW
   ═══════════════════════════════════════════════ */

async function follow() {
    const follower_id  = Number(followerSelect.value);
    const following_id = Number(followingSelect.value);
    if (!follower_id || !following_id) {
        showToast("Select both Follower and Following users.", "error"); return;
    }
    if (follower_id === following_id) {
        showToast("A user cannot follow themselves.", "error"); return;
    }

    setLoading(followBtn, true);
    setStatus("loading");

    try {
        const { ok, data } = await apiFetch("/api/follow", {
            method: "POST",
            body: JSON.stringify({ follower_id, following_id })
        });

        if (!ok) throw new Error(data.error || "Follow failed");

        const rel = data.relationship;
        showToast(`${rel.from.name} → now follows → ${rel.to.name}`, "success");
        setStatus("success");

        afterRelationshipChange();

    } catch (err) {
        showToast(err.message, "error");
        setStatus("error");
    } finally {
        setLoading(followBtn, false);
    }
}

async function unfollow() {
    const follower_id  = Number(followerSelect.value);
    const following_id = Number(followingSelect.value);
    if (!follower_id || !following_id) {
        showToast("Select both Follower and Following users.", "error"); return;
    }

    setLoading(unfollowBtn, true);
    setStatus("loading");

    try {
        const { ok, data } = await apiFetch("/api/follow", {
            method: "DELETE",
            body: JSON.stringify({ follower_id, following_id })
        });

        if (!ok) throw new Error(data.error || "Unfollow failed");

        showToast("Follow relationship removed.", "success");
        setStatus("success");

        afterRelationshipChange();

    } catch (err) {
        showToast(err.message, "error");
        setStatus("error");
    } finally {
        setLoading(unfollowBtn, false);
    }
}

function afterRelationshipChange() {
    const mainId = Number(userSelect.value);
    if (mainId) {
        refreshStats(mainId);
        if (activeType) loadRelationship(activeType);
        loadGraph();
    }
}

/* ═══════════════════════════════════════════════
   STATS CARDS
   ═══════════════════════════════════════════════ */

async function refreshStats(userId) {
    userId = userId || Number(userSelect.value);
    if (!userId) return;

    try {
        const [fw, fr, fd] = await Promise.all([
            apiFetch(`/api/following/${userId}`),
            apiFetch(`/api/followers/${userId}`),
            apiFetch(`/api/friends/${userId}`)
        ]);

        followingCount.textContent = fw.ok ? fw.data.count : "—";
        followersCount.textContent = fr.ok ? fr.data.count : "—";
        friendsCount.textContent   = fd.ok ? fd.data.count : "—";
    } catch (_) {
        resetStats();
    }
}

function resetStats() {
    followingCount.textContent = "—";
    followersCount.textContent = "—";
    friendsCount.textContent   = "—";
    document.querySelectorAll(".stat-card").forEach(c => c.classList.remove("active"));
}

/* ═══════════════════════════════════════════════
   RELATIONSHIP DETAIL PANEL
   ═══════════════════════════════════════════════ */

const relMeta = {
    following: { icon:"➡️",  label:"Following",  key:"following" },
    followers: { icon:"⬅️",  label:"Followers",  key:"followers" },
    friends:   { icon:"🤝",  label:"Friends",    key:"friends"   }
};

async function loadRelationship(type) {
    const userId = Number(userSelect.value);
    if (!userId) { showToast("Select an active user first.", "error"); return; }

    // Toggle off
    if (activeType === type) {
        activeType = null;
        relPanel.style.display = "none";
        document.querySelectorAll(".rel-tab").forEach(b => b.classList.remove("active"));
        document.querySelectorAll(".stat-card").forEach(c => c.classList.remove("active"));
        return;
    }

    activeType = type;
    document.querySelectorAll(".rel-tab").forEach(b => {
        b.classList.toggle("active", b.dataset.type === type);
    });
    document.querySelectorAll(".stat-card").forEach(c => c.classList.remove("active"));
    const statMap = { following:"statFollowing", followers:"statFollowers", friends:"statFriends" };
    if (statMap[type]) $(statMap[type]).classList.add("active");

    const meta = relMeta[type];
    relPanelIcon.textContent  = meta.icon;
    relPanelTitle.textContent = "Loading…";
    relPanelBody.innerHTML    = `<div class="graph-loading"><div class="spinner"></div></div>`;
    relPanel.style.display    = "block";
    relPanel.scrollIntoView({ behavior:"smooth", block:"nearest" });

    try {
        const { ok, data } = await apiFetch(`/api/${type}/${userId}`);
        if (!ok) throw new Error(data.error || "Request failed");

        const users = data[meta.key] || [];
        relPanelTitle.textContent = `${data.user.name}'s ${meta.label} (${users.length})`;
        relPanelBody.innerHTML    = buildUserPills(users, `No ${meta.label.toLowerCase()} yet.`);

    } catch (err) {
        relPanelTitle.textContent = meta.label;
        relPanelBody.innerHTML    = `<span class="rel-empty">Error: ${escHtml(err.message)}</span>`;
        showToast(err.message, "error");
    }
}

/* ── Mutual ── */
async function loadMutual() {
    const userId  = Number(userSelect.value);
    const otherId = Number(mutualSelect.value);
    if (!userId)  { showToast("Select an active user first.", "error"); return; }
    if (!otherId) { showToast("Select a user to compare with.", "error"); return; }
    if (userId === otherId) { showToast("Select two different users.", "error"); return; }

    mutualResult.innerHTML = `<div class="graph-loading"><div class="spinner"></div></div>`;

    try {
        const { ok, data } = await apiFetch(`/api/mutual/${userId}/${otherId}`);
        if (!ok) throw new Error(data.error || "Request failed");

        const users = data.mutual || [];
        mutualResult.innerHTML = buildUserPills(
            users,
            `No mutual friends between ${data.user1.name} and ${data.user2.name}.`
        );
    } catch (err) {
        mutualResult.innerHTML = `<span class="rel-empty">Error: ${escHtml(err.message)}</span>`;
        showToast(err.message, "error");
    }
}

/* ═══════════════════════════════════════════════
   GRAPH
   ═══════════════════════════════════════════════ */

function clearGraph() {
    graphSvg.style.display = "none";
    graphSvg.innerHTML     = "";
    graphNodes.innerHTML   = "";
    graphEmpty.style.display = "flex";
}

async function loadGraph() {
    const userId = Number(userSelect.value);
    if (!userId) { showToast("Select a user to view the graph.", "error"); return; }

    setLoading(loadGraphBtn, true);
    setStatus("loading");

    // Show spinner inside graph area
    graphEmpty.style.display = "none";
    graphSvg.style.display   = "none";
    graphSvg.innerHTML        = "";
    graphNodes.innerHTML      = `
        <div class="graph-loading">
            <div class="spinner"></div>
            <span>Building graph…</span>
        </div>`;

    try {
        const [graphRes] = await Promise.all([
            apiFetch(`/api/graph/${userId}`),
            refreshStats(userId)
        ]);

        const { ok, data } = graphRes;
        if (!ok) throw new Error(data.error || "Graph load failed");

        renderGraph(data);
        setStatus("success");

    } catch (err) {
        graphNodes.innerHTML = `<div class="graph-loading"><span>⚠️ ${escHtml(err.message)}</span></div>`;
        showToast("Graph error: " + err.message, "error");
        setStatus("error");
    } finally {
        setLoading(loadGraphBtn, false);
    }
}

/* ── Render Graph ── */
function renderGraph(data) {
    graphNodes.innerHTML = "";
    graphSvg.innerHTML   = "";

    const nodes    = data.nodes || [];
    const edges    = data.edges || [];
    const centerId = data.center.id;

    if (nodes.length === 0) {
        graphEmpty.style.display = "flex";
        graphSvg.style.display   = "none";
        return;
    }

    graphEmpty.style.display = "none";
    graphSvg.style.display   = "block";

    const W = graphContainer.clientWidth  || 700;
    const H = Math.max(graphContainer.clientHeight, 520);

    graphSvg.setAttribute("viewBox", `0 0 ${W} ${H}`);

    const cx = W / 2;
    const cy = H / 2;

    // Build positions
    const positions = {};
    positions[centerId] = { x: cx, y: cy };

    const others = nodes.filter(n => n.id !== centerId);
    const radius = Math.min(W, H) * 0.33;

    others.forEach((node, i) => {
        const angle = (2 * Math.PI * i) / others.length - Math.PI / 2;
        positions[node.id] = {
            x: cx + radius * Math.cos(angle),
            y: cy + radius * Math.sin(angle)
        };
    });

    // Determine mutual pairs for colouring
    const edgeMap = new Map();
    edges.forEach(e => {
        const key = [Math.min(e.from, e.to), Math.max(e.from, e.to)].join("-");
        edgeMap.set(key, (edgeMap.get(key) || 0) + 1);
    });

    // SVG defs (arrowheads)
    const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    defs.innerHTML = `
        <marker id="arrow-follow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
            <path d="M0,0 L0,6 L8,3 z" fill="#94a3b8"/>
        </marker>
        <marker id="arrow-mutual" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
            <path d="M0,0 L0,6 L8,3 z" fill="#4f46e5"/>
        </marker>
        <filter id="glow">
            <feGaussianBlur in="SourceGraphic" stdDeviation="3" result="blur"/>
            <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
    `;
    graphSvg.appendChild(defs);

    // Draw edges
    const drawnPairs = new Set();
    edges.forEach(edge => {
        const from = positions[edge.from];
        const to   = positions[edge.to];
        if (!from || !to) return;

        const key = [Math.min(edge.from, edge.to), Math.max(edge.from, edge.to)].join("-");
        const isMutual = edgeMap.get(key) >= 2;

        // Avoid drawing duplicates for mutual edges
        if (isMutual && drawnPairs.has(key)) return;
        drawnPairs.add(key);

        // Shorten line to not overlap node circles
        const rFrom = edge.from === centerId ? 55 : 41;
        const rTo   = edge.to   === centerId ? 55 : 41;

        const dx = to.x - from.x;
        const dy = to.y - from.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const ux = dx / dist;
        const uy = dy / dist;

        const x1 = from.x + ux * rFrom;
        const y1 = from.y + uy * rFrom;
        const x2 = to.x   - ux * rTo;
        const y2 = to.y   - uy * rTo;

        const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
        line.setAttribute("x1", x1);
        line.setAttribute("y1", y1);
        line.setAttribute("x2", x2);
        line.setAttribute("y2", y2);

        if (isMutual) {
            line.setAttribute("stroke", "#4f46e5");
            line.setAttribute("stroke-width", "2.5");
            line.setAttribute("stroke-dasharray", "none");
            line.setAttribute("marker-end", "url(#arrow-mutual)");
        } else {
            line.setAttribute("stroke", "#94a3b8");
            line.setAttribute("stroke-width", "1.8");
            line.setAttribute("stroke-dasharray", "5,3");
            line.setAttribute("marker-end", "url(#arrow-follow)");
        }

        graphSvg.appendChild(line);

        // Edge label (midpoint)
        const mx = (x1 + x2) / 2;
        const my = (y1 + y2) / 2;
        const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
        label.setAttribute("x", mx);
        label.setAttribute("y", my - 5);
        label.setAttribute("text-anchor", "middle");
        label.setAttribute("font-size", "10");
        label.setAttribute("fill", isMutual ? "#4f46e5" : "#94a3b8");
        label.setAttribute("font-family", "Segoe UI, system-ui, sans-serif");
        label.setAttribute("font-weight", "600");
        label.textContent = isMutual ? "mutual" : edge.type || "follows";

        const bg = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        bg.setAttribute("x", mx - 18);
        bg.setAttribute("y", my - 16);
        bg.setAttribute("width", 36);
        bg.setAttribute("height", 14);
        bg.setAttribute("rx", 3);
        bg.setAttribute("fill", "white");
        bg.setAttribute("opacity", "0.8");

        graphSvg.appendChild(bg);
        graphSvg.appendChild(label);
    });

    // Draw nodes
    nodes.forEach(node => {
        const pos     = positions[node.id];
        const isCenter = node.id === centerId;

        const div = document.createElement("div");
        div.className = "gnode " + (isCenter ? "center-node" : "peer-node");
        div.style.left = pos.x + "px";
        div.style.top  = pos.y + "px";
        div.title      = `${node.name} (ID #${node.id})`;
        div.innerHTML  = `
            <div class="gnode-name">${escHtml(node.name)}</div>
            <div class="gnode-id">#${node.id}</div>
        `;

        // Click peer node → make it the active user
        if (!isCenter) {
            div.style.cursor = "pointer";
            div.addEventListener("click", () => {
                userSelect.value = node.id;
                refreshStats(node.id);
                loadGraph();
            });
        }

        graphNodes.appendChild(div);
    });
}

/* ═══════════════════════════════════════════════
   EVENT LISTENERS
   ═══════════════════════════════════════════════ */

addUserBtn.addEventListener("click", addUser);
addUserInput.addEventListener("keydown", e => { if (e.key === "Enter") addUser(); });

updateUserBtn.addEventListener("click", updateUser);
updateUserInput.addEventListener("keydown", e => { if (e.key === "Enter") updateUser(); });

deleteUserBtn.addEventListener("click", deleteUser);

refreshUsersBtn.addEventListener("click", async () => {
    setLoading(refreshUsersBtn, true);
    await fetchUsers(Number(userSelect.value) || undefined);
    showToast("Users refreshed.", "info");
    setLoading(refreshUsersBtn, false);
});

followBtn.addEventListener("click", follow);
unfollowBtn.addEventListener("click", unfollow);

loadGraphBtn.addEventListener("click", loadGraph);

// Relationship tabs
document.querySelectorAll(".rel-tab").forEach(btn => {
    btn.addEventListener("click", () => loadRelationship(btn.dataset.type));
});

// Stat cards → open relationship panel
$("statFollowing").addEventListener("click", () => loadRelationship("following"));
$("statFollowers").addEventListener("click", () => loadRelationship("followers"));
$("statFriends").addEventListener("click",   () => loadRelationship("friends"));

relPanelClose.addEventListener("click", () => {
    relPanel.style.display = "none";
    activeType = null;
    document.querySelectorAll(".rel-tab").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".stat-card").forEach(c => c.classList.remove("active"));
});

mutualBtn.addEventListener("click", loadMutual);

// When main user changes, refresh stats automatically
userSelect.addEventListener("change", () => {
    const id = Number(userSelect.value);
    if (id) {
        resetStats();
        refreshStats(id);
        relPanel.style.display = "none";
        activeType = null;
        document.querySelectorAll(".rel-tab").forEach(b => b.classList.remove("active"));
    } else {
        resetStats();
    }
});

/* ═══════════════════════════════════════════════
   INIT
   ═══════════════════════════════════════════════ */

(async function init() {
    await fetchUsers();

    // Auto-select first user and show their graph
    if (allUsers.length > 0) {
        userSelect.value = allUsers[0].id;
        await refreshStats(allUsers[0].id);
        await loadGraph();
    }
})();
