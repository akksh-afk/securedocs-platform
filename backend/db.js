const { Pool } = require("pg");

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
});

pool.on("error", (err) => {
  console.error("Unexpected database error", err);
});

// query() is the only way the rest of the app talks to Postgres.
// Always pass values as the second argument, never string-concatenate
// them into the SQL. That is what prevents SQL injection.
async function query(text, params) {
  return pool.query(text, params);
}

// Run fn(client) inside BEGIN/COMMIT, rolling back if it throws.
// Anything that changes data and must be audited goes through here, so
// the change and its audit entry share one transaction.
async function transaction(fn) {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    const result = await fn(client);
    await client.query("COMMIT");
    return result;
  } catch (err) {
    await client.query("ROLLBACK").catch(() => {});
    throw err;
  } finally {
    client.release();
  }
}

module.exports = { query, transaction, pool };