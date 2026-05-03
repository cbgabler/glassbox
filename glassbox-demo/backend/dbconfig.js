// glassbox-demo: backend/dbconfig.js
//
// Hardcoded production database credentials. Should produce a HIGH
// secrets finding and a recommendation to (a) rotate, (b) move to env,
// (c) audit git history for prior exposure.

const { Pool } = require('pg');

const pool = new Pool({
  host: 'prod-db.glassbox-demo.internal',
  port: 5432,
  user: 'glassbox_app',
  password: 'Pr0d-S3cr3t!2025',
  database: 'glassbox_prod',
  ssl: false,
});

module.exports = pool;
