// glassbox-demo: backend/server.js
//
// Intentionally vulnerable Express server demonstrating multiple
// "exposed endpoint" findings GlassBox should report:
//
//   - 0.0.0.0 bind                         (network exposure)
//   - cors({ origin: '*' })                (open CORS)
//   - GET /api/users/:id                   (returns password + api_key)
//   - GET /api/admin/dump                  (no auth, dumps env)
//   - error handler                        (echoes stack trace to client)
//   - hardcoded JWT secret                 (secrets finding)

const express = require('express');
const cors = require('cors');
const jwt = require('jsonwebtoken');

const app = express();

app.use(cors({ origin: '*' }));
app.use(express.json());

const JWT_SECRET = 'super-secret-jwt-key-do-not-rotate-2025';

const users = [
  {
    id: 1,
    username: 'tj',
    password: 'hunter2',
    api_key: 'sk_live_51HxAbCdEfGhIjKlMnOpQrStUvWxYz0123456789',
    role: 'admin',
  },
];

app.get('/api/users/:id', (req, res) => {
  const user = users.find((u) => u.id === Number(req.params.id));
  if (!user) return res.status(404).json({ error: 'not found' });
  res.json(user);
});

app.get('/api/admin/dump', (req, res) => {
  res.json({
    env: process.env,
    users,
    jwt_secret: JWT_SECRET,
  });
});

app.post('/api/login', (req, res) => {
  const { username, password } = req.body;
  const user = users.find((u) => u.username === username);
  if (!user) return res.status(401).json({ error: 'bad creds' });

  if (user.password === password) {
    return res.json({ token: jwt.sign({ sub: user.id }, JWT_SECRET) });
  }
  res.status(401).json({ error: 'bad creds' });
});

app.use((err, req, res, next) => {
  res.status(500).json({
    error: err.message,
    stack: err.stack,
  });
});

app.listen(8080, '0.0.0.0', () => {
  console.log('listening on 0.0.0.0:8080');
});
