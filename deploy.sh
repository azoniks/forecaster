#!/usr/bin/env bash
set -Eeuo pipefail

DEPLOY_HOST="${DEPLOY_HOST:-84.201.132.48}"
DEPLOY_USER="${DEPLOY_USER:-azoniks}"
DEPLOY_DIR="${DEPLOY_DIR:-/home/azoniks/forecaster}"
DEPLOY_SERVICE="${DEPLOY_SERVICE:-forecaster}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-$(git branch --show-current)}"
DEPLOY_KEY="${DEPLOY_KEY:-}"

if [[ -z "$DEPLOY_BRANCH" ]]; then
  echo "Ошибка: невозможно определить текущую ветку Git." >&2
  exit 1
fi

if [[ ! "$DEPLOY_BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  echo "Ошибка: небезопасное имя ветки: $DEPLOY_BRANCH" >&2
  exit 1
fi

if [[ ! "$DEPLOY_SERVICE" =~ ^[A-Za-z0-9_.@-]+$ ]]; then
  echo "Ошибка: небезопасное имя systemd-сервиса: $DEPLOY_SERVICE" >&2
  exit 1
fi

DEPLOY_COMMIT="$(git rev-parse HEAD)"
DEPLOY_TARGET="${DEPLOY_USER}@${DEPLOY_HOST}"
SSH_OPTIONS=(-o IdentitiesOnly=yes)
if [[ -n "$DEPLOY_KEY" ]]; then
  if [[ ! -f "$DEPLOY_KEY" ]]; then
    echo "Ошибка: приватный ключ не найден: $DEPLOY_KEY" >&2
    exit 1
  fi
  SSH_OPTIONS+=(-i "$DEPLOY_KEY")
fi

echo "Проверяем проект локально..."
python3 -m unittest discover -s tests -q
node --check static/app.js

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Внимание: есть незакоммиченные изменения; они не попадут на сервер."
fi

echo "Отправляем ${DEPLOY_BRANCH} в origin..."
git push origin "$DEPLOY_BRANCH"

echo "Разворачиваем ${DEPLOY_COMMIT:0:12} на ${DEPLOY_TARGET}..."
ssh "${SSH_OPTIONS[@]}" "$DEPLOY_TARGET" bash -s -- \
  "$DEPLOY_DIR" "$DEPLOY_BRANCH" "$DEPLOY_COMMIT" "$DEPLOY_SERVICE" <<'REMOTE_DEPLOY'
set -Eeuo pipefail

deploy_dir="$1"
deploy_branch="$2"
deploy_commit="$3"
deploy_service="$4"

cd "$deploy_dir"

if [[ -f data/forecaster.sqlite3 ]]; then
  backup="data/forecaster.sqlite3.backup-$(date +%Y%m%d-%H%M%S)"
  cp data/forecaster.sqlite3 "$backup"
  echo "Резервная копия БД: $backup"
fi

git fetch origin "$deploy_branch"
git switch "$deploy_branch"
git pull --ff-only origin "$deploy_branch"

actual_commit="$(git rev-parse HEAD)"
if [[ "$actual_commit" != "$deploy_commit" ]]; then
  echo "Ошибка: сервер получил $actual_commit вместо $deploy_commit" >&2
  exit 1
fi

sudo systemctl restart "$deploy_service"
sudo systemctl is-active --quiet "$deploy_service"
sudo systemctl status "$deploy_service" --no-pager --lines=12
REMOTE_DEPLOY

echo "Деплой ${DEPLOY_COMMIT:0:12} успешно завершён."
