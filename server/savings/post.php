<?php
// The community savings meter's receiver: the code deployed at the endpoint
// `storage/token_tracker.py` posts to (`_TELEMETRY_URL`), copied 2026-10-19.
// Kept here so the client's description of it can be checked against it
// (tests/test_savings_receiver_matches_the_client.py). Not shipped in the
// package; a change here does not deploy anything.
header("Content-Type: application/json; charset=utf-8");

// Read JSON body
$raw = file_get_contents("php://input");
$data = json_decode($raw, true);

if (!is_array($data)) {
  http_response_code(400);
  echo json_encode(["ok" => false, "error" => "Invalid JSON"]);
  exit;
}

$anon_id = isset($data["anon_id"]) ? trim((string)$data["anon_id"]) : "";
$delta   = isset($data["delta"]) ? $data["delta"] : null;
$total   = isset($data["total"]) ? $data["total"] : null;

// Basic validation
if ($anon_id === "" || strlen($anon_id) > 64) {
  http_response_code(400);
  echo json_encode(["ok" => false, "error" => "Invalid anon_id"]);
  exit;
}

// Coerce a JSON number field to a non-negative int, or null if unusable.
function coerce_int($v) {
  if (is_int($v)) return $v;
  if (is_string($v) && ctype_digit($v)) return (int)$v;
  return null;
}

$delta = coerce_int($delta);
$total = coerce_int($total);

// Prefer the absolute lifetime `total` when the client sends it. The upsert
// sets the stored value to GREATEST(stored, total): idempotent, monotonic, and
// self-correcting.
//   - Self-healing: a dropped/failed client POST is recovered by the next one,
//     so the meter converges to the client's true local total instead of
//     permanently undercounting from lost deltas.
//   - Never decreases (clients cannot "take back" reported savings): a lower
//     incoming total is ignored by GREATEST, so a client that reset its local
//     counter to 0 (or sends a smaller number) cannot zero out or shrink the DB
//     row. (A reset also yields a fresh anon_id client-side, starting a new row
//     rather than touching the old total.)
// Backward compatibility: older clients that send only {anon_id, delta} (no
// `total`) keep the additive path below, unchanged.
if ($total !== null) {
  // total is a lifetime cumulative; allow a generous ceiling, reject absurd.
  if ($total <= 0 || $total > 100_000_000_000) {
    http_response_code(400);
    echo json_encode(["ok" => false, "error" => "Total out of range"]);
    exit;
  }
  $sql = "
    INSERT INTO token_savings (anon_id, total_tokens)
    VALUES (:anon_id, :value)
    ON DUPLICATE KEY UPDATE total_tokens = GREATEST(total_tokens, VALUES(total_tokens))
  ";
  $value = $total;
} elseif ($delta !== null) {
  // Legacy additive path (older clients).
  if ($delta <= 0 || $delta > 50_000_000) {
    http_response_code(400);
    echo json_encode(["ok" => false, "error" => "Delta out of range"]);
    exit;
  }
  $sql = "
    INSERT INTO token_savings (anon_id, total_tokens)
    VALUES (:anon_id, :value)
    ON DUPLICATE KEY UPDATE total_tokens = total_tokens + VALUES(total_tokens)
  ";
  $value = $delta;
} else {
  http_response_code(400);
  echo json_encode(["ok" => false, "error" => "Missing total or delta"]);
  exit;
}

// The deployed copy reads the database settings (db_host, db_name, db_user,
// db_pass) from a private config file outside the web root. That file is not
// in this repository, and this path is a placeholder for it.
$config = require "/path/to/private/config.php";

try {
  $dsn = "mysql:host={$config['db_host']};dbname={$config['db_name']};charset=utf8mb4";
  $pdo = new PDO($dsn, $config["db_user"], $config["db_pass"], [
    PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
  ]);

  $stmt = $pdo->prepare($sql);
  $stmt->execute([
    ":anon_id" => $anon_id,
    ":value"   => $value,
  ]);

  echo json_encode(["ok" => true]);
} catch (Throwable $e) {
  // Silent-ish failure. Client is fire-and-forget anyway.
  http_response_code(500);
  echo json_encode(["ok" => false]);
}