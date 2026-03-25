<?php
/**
 * GitHub Webhook Auto-Deploy
 * Remote File Manager
 * Version: 1.0
 *
 * Only deploys when a commit message contains [deploy].
 * Normal pushes are ignored.
 *
 * Setup:
 * 1. Upload this file to your web root on the cloud server
 * 2. Go to GitHub repo → Settings → Webhooks → Add webhook
 *    - Payload URL: https://argonar.co/filemanager/webhook-deploy.php
 *    - Content type: application/json
 *    - Secret: (set the same secret below)
 *    - Events: Just the push event
 * 3. Set the WEBHOOK_SECRET below to match the GitHub secret
 * 4. Make sure the web server user (www-data) can run git pull
 *    Run once on server:
 *    sudo chown -R www-data:www-data /var/www/argonar/filemanager/.git
 *    git config --global --add safe.directory /var/www/argonar/filemanager
 */

// ============================================================
// CONFIGURATION
// ============================================================
define('WEBHOOK_SECRET', 'kirfenia123');
define('DEPLOY_KEYWORD', '[deploy]');
define('WEB_ROOT', '/var/www/argonar/filemanager');
define('LOG_DIR', __DIR__ . '/logs');
define('LOG_FILE', LOG_DIR . '/deploy.log');
define('ALLOWED_BRANCH', 'refs/heads/master');

// ============================================================
// HELPERS
// ============================================================
function verifySignature($payload, $signature) {
    if (empty($signature)) return false;
    $hash = 'sha256=' . hash_hmac('sha256', $payload, WEBHOOK_SECRET);
    return hash_equals($hash, $signature);
}

function logMessage($message) {
    if (!is_dir(LOG_DIR)) {
        @mkdir(LOG_DIR, 0775, true);
    }
    $timestamp = date('Y-m-d H:i:s');
    $line = "[{$timestamp}] {$message}\n";
    @file_put_contents(LOG_FILE, $line, FILE_APPEND | LOCK_EX);
}

// ============================================================
// PROCESS WEBHOOK
// ============================================================

// Only accept POST requests
if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    exit('Method Not Allowed');
}

// Read payload
$rawPayload = file_get_contents('php://input');

// Handle both JSON and form-urlencoded content types
$contentType = $_SERVER['CONTENT_TYPE'] ?? $_SERVER['HTTP_CONTENT_TYPE'] ?? '';

if (stripos($contentType, 'application/x-www-form-urlencoded') !== false) {
    parse_str($rawPayload, $parsed);
    $jsonPayload = $parsed['payload'] ?? '';
    $signaturePayload = $rawPayload;
} else {
    $jsonPayload = $rawPayload;
    $signaturePayload = $rawPayload;
}

// Verify GitHub signature
$signature = '';
if (isset($_SERVER['HTTP_X_HUB_SIGNATURE_256'])) {
    $signature = $_SERVER['HTTP_X_HUB_SIGNATURE_256'];
}

if (!verifySignature($signaturePayload, $signature)) {
    http_response_code(403);
    logMessage("REJECTED: Invalid signature. Content-Type: {$contentType}");
    exit('Forbidden');
}

// Parse payload
$data = json_decode($jsonPayload, true);
if (!$data) {
    http_response_code(400);
    logMessage("REJECTED: Invalid JSON. Content-Type: {$contentType}. Raw length: " . strlen($rawPayload));
    exit('Bad Request');
}

// Check branch
$ref = $data['ref'] ?? '';
if ($ref !== ALLOWED_BRANCH) {
    http_response_code(200);
    logMessage("SKIPPED: Push to {$ref} (not master)");
    exit("Ignored: not master branch");
}

// Check for [deploy] keyword in any commit message
$shouldDeploy = false;
$deployCommit = '';
$commits = $data['commits'] ?? [];

foreach ($commits as $commit) {
    $message = $commit['message'] ?? '';
    if (stripos($message, DEPLOY_KEYWORD) !== false) {
        $shouldDeploy = true;
        $deployCommit = substr($message, 0, 80);
        break;
    }
}

if (!$shouldDeploy) {
    http_response_code(200);
    $count = count($commits);
    logMessage("SKIPPED: {$count} commit(s) pushed without [deploy] keyword");
    exit("OK: No [deploy] keyword found. Skipping deployment.");
}

// ============================================================
// DEPLOY: Run git fetch + reset
// ============================================================
logMessage("DEPLOYING: \"{$deployCommit}\"");

$output = [];
$returnCode = 0;

chdir(WEB_ROOT);

putenv('GIT_CONFIG_GLOBAL=/dev/null');

exec('git -c safe.directory=' . WEB_ROOT . ' -C ' . WEB_ROOT . ' fetch origin master 2>&1', $output, $returnCode);

if ($returnCode === 0) {
    exec('git -c safe.directory=' . WEB_ROOT . ' -C ' . WEB_ROOT . ' reset --hard origin/master 2>&1', $output, $returnCode);
}

$outputStr = implode("\n", $output);

if ($returnCode === 0) {
    exec('chown -R www-data:www-data ' . WEB_ROOT . ' 2>&1');
    logMessage("SUCCESS: deploy completed\n{$outputStr}");
    http_response_code(200);
    echo "Deployed successfully.\n{$outputStr}";
} else {
    logMessage("FAILED: deploy failed (code {$returnCode})\n{$outputStr}");
    http_response_code(500);
    echo "Deploy failed.\n{$outputStr}";
}
