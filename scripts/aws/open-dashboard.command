#!/bin/bash
# =============================================================================
# open-dashboard.command - double-click to open the live dashboard as the Group 3 operator (macOS)
# -----------------------------------------------------------------------------
# Runs scripts/aws/dashboard-signin.py with the project Python. If the AWS
# session has expired, it first re-runs "aws login" for the is459-g3 profile.
# Changes nothing in AWS and stores no credentials. configure-aws.ps1 is
# Windows-only, so on a Mac this also creates the is459-g3-tools profile the
# helper uses when it is missing (same credential_process setup).
# Windows equivalent: scripts\aws\open-dashboard.cmd
# =============================================================================
set -u
cd "$(dirname "$0")/../.." || exit 1
PROFILE=is459-g3
TOOLS=is459-g3-tools
REGION=ap-southeast-1

# Keep the Terminal window open on errors so the message can be read.
fail() {
    echo
    echo "$1"
    echo
    read -r -p "Press Return to close." _
    exit 1
}

PY=.venv/bin/python
[ -x "$PY" ] || fail "Project Python not found. From the repository folder run:
    python3 -m venv .venv
    .venv/bin/python -m pip install -e '.[cloud]'"
"$PY" -c "import boto3" >/dev/null 2>&1 || fail "The project Python is missing boto3. From the repository folder run:
    .venv/bin/python -m pip install -e '.[cloud]'"

AWS_BIN=$(command -v aws) || fail "AWS CLI v2 not found. Install it from https://aws.amazon.com/cli/ and run this launcher again."

# The helper reads credentials through the tools profile, which asks the CLI
# for the logged-in profile's temporary credentials.
if ! "$AWS_BIN" configure get credential_process --profile "$TOOLS" >/dev/null 2>&1; then
    echo "Creating the $TOOLS AWS profile (no credentials are stored)."
    "$AWS_BIN" configure set credential_process \
        "$AWS_BIN configure export-credentials --profile $PROFILE --format process --region $REGION" --profile "$TOOLS"
    "$AWS_BIN" configure set region "$REGION" --profile "$TOOLS"
fi

# Renew the session only when the tools profile can no longer reach AWS.
if ! "$AWS_BIN" sts get-caller-identity --profile "$TOOLS" --region "$REGION" --no-cli-pager >/dev/null 2>&1; then
    echo "Your AWS session has expired or is not set up. Opening AWS sign-in..."
    "$AWS_BIN" login --profile "$PROFILE" --region "$REGION" \
        || fail "AWS sign-in failed. Browser login needs AWS CLI 2.32.0 or newer."
    "$AWS_BIN" sts get-caller-identity --profile "$TOOLS" --region "$REGION" --no-cli-pager >/dev/null 2>&1 \
        || fail "Still not signed in. Check the AWS message above and try again."
fi

"$PY" scripts/aws/dashboard-signin.py --profile "$TOOLS" \
    || fail "The dashboard sign-in helper failed; see the message above."
# Leave the message on screen briefly before the window closes.
sleep 3
