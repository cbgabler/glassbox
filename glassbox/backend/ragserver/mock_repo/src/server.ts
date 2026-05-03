import express from "express";
const app = express();
app.get("/debug", (req, res) => res.send("ok"));
app.listen(3000);
