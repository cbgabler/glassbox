#!/usr/bin/env bash
#
# glassbox-demo: seed-git-history.sh
#
# Run this once after `git init` (and BEFORE the first real commit) to
# plant a secret in git history that has been "removed" from HEAD. This
# exercises GlassBox's git-history secrets scanner.
#
# Usage:
#   cd glassbox-demo
#   git init -b main
#   ./seed-git-history.sh
#   git add -A
#   git commit -m "initial demo files"
#   git remote add origin git@github.com:<you>/glassbox-demo.git
#   git push -u origin main --force

set -euo pipefail

if [ ! -d .git ]; then
  echo "error: run 'git init' first" >&2
  exit 1
fi

cat > .env.production <<'EOF'
# ! fake leaked secret -- will be removed in the next commit
STRIPE_SECRET_KEY=sk_live_51HxAbCdEfGhIjKlMnOpQrStUvWxYz0123456789ABCDEFGHIJKLMNUVWXYZ
DATABASE_URL=postgres://glassbox:Pr0d-S3cr3t!2025@prod-db.glassbox-demo.internal:5432/glassbox_prod
EOF

git add .env.production
git -c user.email=demo@glassbox.local -c user.name="glassbox-demo" \
    commit -m "wip: drop production env file (will revert)" --quiet

git rm .env.production --quiet
git -c user.email=demo@glassbox.local -c user.name="glassbox-demo" \
    commit -m "remove accidentally committed prod env" --quiet

echo "seeded: .env.production added in HEAD~1, removed in HEAD."
echo "git log --oneline:"
git log --oneline
