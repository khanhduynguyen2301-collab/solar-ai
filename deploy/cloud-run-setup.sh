#!/usr/bin/env bash
# One-time Google Cloud setup so GitHub Actions can deploy the inference API to Cloud Run.
#
# Run it once in Google Cloud Shell (https://shell.cloud.google.com), with the project selected:
#
#   gcloud config set project YOUR_PROJECT_ID
#   bash cloud-run-setup.sh
#
# It creates: an image repository, two service accounts (one that deploys, one the API runs as),
# keyless sign-in for this GitHub repository's main branch only, and the API's secrets.
# It deploys nothing; the CI workflow does that. Safe to run again: existing things are kept.
#
# Costs: Cloud Run and Secret Manager stay inside their free allowances at demo traffic.
# Stored images are billed beyond 0.5 GB, so only the two newest are kept; expect a few cents a month.
set -euo pipefail

GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-khanhduynguyen2301-collab/solar-ai}"
REGION="${REGION:-us-central1}"
IMAGE_REPOSITORY="solar-ai"
DEPLOYER="solar-ai-deployer"
RUNTIME="solar-ai-runtime"
POOL="github"
API_KEY_SECRET="solar-api-key"
LLM_KEY_SECRET="anthropic-api-key"

PROJECT_ID="$(gcloud config get-value project 2>/dev/null)"
if [ -z "$PROJECT_ID" ] || [ "$PROJECT_ID" = "(unset)" ]; then
  echo "No project selected. Run: gcloud config set project YOUR_PROJECT_ID" >&2
  exit 1
fi
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
DEPLOYER_EMAIL="$DEPLOYER@$PROJECT_ID.iam.gserviceaccount.com"
RUNTIME_EMAIL="$RUNTIME@$PROJECT_ID.iam.gserviceaccount.com"
echo "Project $PROJECT_ID, region $REGION, GitHub repository $GITHUB_REPOSITORY"

echo "== Enabling services"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com iamcredentials.googleapis.com secretmanager.googleapis.com

echo "== Image repository"
gcloud artifacts repositories describe "$IMAGE_REPOSITORY" --location="$REGION" >/dev/null 2>&1 \
  || gcloud artifacts repositories create "$IMAGE_REPOSITORY" --repository-format=docker --location="$REGION" --description="Solar Farm AI images deployed to Cloud Run"
policy="$(mktemp)"
cat > "$policy" <<'JSON'
[
  {"name": "keep-two-newest", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 2}},
  {"name": "delete-the-rest", "action": {"type": "Delete"}, "condition": {"tagState": "ANY", "olderThan": "1d"}}
]
JSON
gcloud artifacts repositories set-cleanup-policies "$IMAGE_REPOSITORY" --location="$REGION" --policy="$policy" --no-dry-run \
  || echo "WARNING: could not set the cleanup policy; old images will accumulate and add storage cost."
rm -f "$policy"

echo "== Service accounts"
for account in "$DEPLOYER" "$RUNTIME"; do
  gcloud iam service-accounts describe "$account@$PROJECT_ID.iam.gserviceaccount.com" >/dev/null 2>&1 \
    || gcloud iam service-accounts create "$account" --display-name="$account"
done
# The deployer may manage Cloud Run services, push images to this one repository, and run services as the runtime account.
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$DEPLOYER_EMAIL" --role="roles/run.admin" --condition=None >/dev/null
gcloud artifacts repositories add-iam-policy-binding "$IMAGE_REPOSITORY" --location="$REGION" --member="serviceAccount:$DEPLOYER_EMAIL" --role="roles/artifactregistry.writer" >/dev/null
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_EMAIL" --member="serviceAccount:$DEPLOYER_EMAIL" --role="roles/iam.serviceAccountUser" >/dev/null

echo "== Keyless sign-in from GitHub Actions (main branch of $GITHUB_REPOSITORY only)"
gcloud iam workload-identity-pools describe "$POOL" --location=global >/dev/null 2>&1 \
  || gcloud iam workload-identity-pools create "$POOL" --location=global --display-name="GitHub Actions"
gcloud iam workload-identity-pools providers describe "$POOL" --location=global --workload-identity-pool="$POOL" >/dev/null 2>&1 \
  || gcloud iam workload-identity-pools providers create-oidc "$POOL" --location=global --workload-identity-pool="$POOL" \
       --issuer-uri="https://token.actions.githubusercontent.com" \
       --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
       --attribute-condition="assertion.repository=='$GITHUB_REPOSITORY' && assertion.ref=='refs/heads/main'"
gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER_EMAIL" --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$POOL/attribute.repository/$GITHUB_REPOSITORY" >/dev/null

echo "== Secrets"
secrets="SOLAR_API_KEY=$API_KEY_SECRET:latest"
if gcloud secrets describe "$API_KEY_SECRET" >/dev/null 2>&1; then
  echo "Keeping the existing $API_KEY_SECRET."
else
  # A new random key that callers of the hosted API must send in X-API-Key. It is not printed.
  # printf, not a pipe from cut: the stored value must not end in a line break, or no request header can match it.
  printf '%s' "$(openssl rand -base64 48 | tr -d '=+/\n' | cut -c1-43)" | gcloud secrets create "$API_KEY_SECRET" --replication-policy=automatic --data-file=-
fi
gcloud secrets add-iam-policy-binding "$API_KEY_SECRET" --member="serviceAccount:$RUNTIME_EMAIL" --role="roles/secretmanager.secretAccessor" >/dev/null

if gcloud secrets describe "$LLM_KEY_SECRET" >/dev/null 2>&1; then
  echo "Keeping the existing $LLM_KEY_SECRET."
  llm_key_stored=yes
else
  echo "Optional: paste your Anthropic API key to enable LLM explanations on the hosted API."
  read -r -s -p "Anthropic API key (input hidden; press Enter to skip): " llm_key
  echo
  if [ -n "$llm_key" ]; then
    printf '%s' "$llm_key" | gcloud secrets create "$LLM_KEY_SECRET" --replication-policy=automatic --data-file=-
    llm_key_stored=yes
  else
    llm_key_stored=no
  fi
  unset llm_key
fi
if [ "$llm_key_stored" = yes ]; then
  gcloud secrets add-iam-policy-binding "$LLM_KEY_SECRET" --member="serviceAccount:$RUNTIME_EMAIL" --role="roles/secretmanager.secretAccessor" >/dev/null
  secrets="$secrets,ANTHROPIC_API_KEY=$LLM_KEY_SECRET:latest"
fi

cat <<EOF

Done. Nothing is deployed yet.

Add these as repository VARIABLES (not secrets; none of them is sensitive) at
https://github.com/$GITHUB_REPOSITORY/settings/variables/actions

  GCP_PROJECT_ID                  $PROJECT_ID
  GCP_REGION                      $REGION
  GCP_WORKLOAD_IDENTITY_PROVIDER  projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$POOL/providers/$POOL
  GCP_DEPLOYER_SERVICE_ACCOUNT    $DEPLOYER_EMAIL
  GCP_RUNTIME_SERVICE_ACCOUNT     $RUNTIME_EMAIL
  CLOUD_RUN_SECRETS               $secrets

The next push to main then deploys the API. To read the API key callers must send:

  gcloud secrets versions access latest --secret=$API_KEY_SECRET
EOF
