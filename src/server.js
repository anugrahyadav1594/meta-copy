const express = require("express");
const cors = require("cors");
const path = require("path");

const { initializeDatabase } = require("./database");
const seedDatabase = require("./seed");
const socialRoutes = require("./routes/social");

const app = express();
const PORT = 3000;

app.use(cors());
app.use(express.json());

app.use(express.static(
    path.join(__dirname, "..", "public")
));

initializeDatabase();
seedDatabase();

app.use("/api", socialRoutes);

app.get("/api/health", (req, res) => {
    res.json({
        status: "OK",
        service: "TAO-Inspired Social Graph",
        database: "SQLite",
        graphLayer: "In-memory adjacency lists"
    });
});

app.listen(PORT, () => {
    console.log("");
    console.log("======================================");
    console.log(" TAO-Inspired Social Graph Service");
    console.log("======================================");
    console.log(`Server: http://localhost:${PORT}`);
    console.log(`Frontend: http://localhost:${PORT}`);
    console.log(`API: http://localhost:${PORT}/api`);
    console.log("");
});