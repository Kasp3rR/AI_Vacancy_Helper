# AI Vacancy Helper

Backend ИИ-помощника по подбору вакансий на hh.ru.
Реализованы этапы **1–3**: каркас FastAPI, PostgreSQL/Redis в Docker Compose,
SQLAlchemy-модели и начальная миграция Alembic.

JWT, загрузка PDF, Ollama, Celery, hh.ru, кэширование и ранжирование будут
добавляться следующими этапами. Таблицы для них подготовлены, бизнес-API пока нет.

## Стек текущего этапа

Python 3.12, FastAPI, Uvicorn, Pydantic 2, pydantic-settings, PostgreSQL 16,
SQLAlchemy 2, Alembic, psycopg 3, Redis 7, uv, Ruff, mypy, pytest, pre-commit.
Версии Python-зависимостей зафиксированы в `uv.lock`.

## Быстрый запуск в Docker

Требуется Docker с Compose v2.

```bash
cp .env.example .env
# При необходимости отредактируйте порты и локальные параметры в .env.
docker compose up -d --build --wait
curl http://localhost:8000/health
```

Ответ: `{"status":"ok"}`. Swagger UI: <http://localhost:8000/docs>.

Compose запускает PostgreSQL и Redis, ждёт готовности PostgreSQL,
применяет миграции отдельным одноразовым сервисом `migrate`, затем запускает API.
`migrate` со статусом `Exited (0)` — нормальный результат успешной миграции.
API работает от непривилегированного пользователя контейнера.

`/health` — проверка работоспособности процесса API (liveness). Она не проверяет
доступность PostgreSQL и Redis; у них отдельные healthcheck в Compose.

```bash
docker compose ps -a
docker compose logs api migrate
docker compose down
```

Данные сохраняются в именованных томах `postgres_data` и `redis_data`.
`docker compose down -v` удаляет эти данные, поэтому не используйте его для обычной остановки.

## Локальная разработка

Установите [uv](https://docs.astral.sh/uv/getting-started/installation/) и Docker.

```bash
cp .env.example .env
uv sync --frozen
uv run pre-commit install
docker compose up -d --wait postgres redis
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

`uv` автоматически использует Python 3.12 из `.python-version`.
Не запускайте локальный Uvicorn и контейнер API одновременно на одном порту.

Настройки читаются из `.env` и переменных окружения; переменные окружения имеют приоритет.
`.env` не попадает в Git или Docker-образ. В примере заданы только локальные
учётные данные; Compose предназначен для разработки и публикует порты на `127.0.0.1`.

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `APP_NAME` | `AI Vacancy Helper` | Название API |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `jobhelper` | Инициализация PostgreSQL |
| `POSTGRES_PORT` | `5432` | Порт PostgreSQL на хосте |
| `REDIS_PORT` | `6379` | Порт Redis на хосте |
| `API_PORT` | `8000` | Порт API на хосте |
| `DATABASE_URL` | см. `.env.example` | Подключение локального Python-процесса к БД |
| `REDIS_URL` | `redis://localhost:6379/0` | Подключение локального процесса к Redis |

Если порт PostgreSQL занят, например, установите `POSTGRES_PORT=55433` и замените
порт в `DATABASE_URL` на `55433`. Аналогично согласуйте `REDIS_PORT` и `REDIS_URL`.
Внутри Compose адреса автоматически используют имена сервисов `postgres` и `redis`.
В паролях, включаемых в URL подключения, специальные символы нужно URL-кодировать.
Переменные `POSTGRES_*` инициализируют только новый том; их изменение не меняет
учётные данные уже существующей БД.

## Структура и схема данных

```text
app/
  main.py       — создание FastAPI-приложения и /health
  config.py     — настройки окружения
  db.py         — ленивое создание engine, фабрика сессий, dependency get_db
  models.py     — SQLAlchemy-модели
migrations/     — Alembic и начальная миграция
tests/          — smoke-тест API и PostgreSQL-интеграционные тесты
compose.yaml    — PostgreSQL, Redis, миграции и API
```

| Таблица | Данные и ограничения |
|---|---|
| `users` | UUID, уникальный email, хеш пароля, время создания |
| `refresh_tokens` | JTI, пользователь, срок действия, признак отзыва |
| `resumes` | Пользователь, имя/путь PDF, профиль JSONB, время создания |
| `jobs` | Резюме, тип, статус, параметры JSONB, ошибка, временные метки |
| `recommendations` | Задача, ID вакансии, score 0–100, данные JSONB |

Владелец задачи определяется через её резюме; владелец рекомендации — через задачу.
Внешние ключи защищают связи, удаление связанных записей автоматически не каскадируется.
Пара `(job_id, vacancy_id)` уникальна. Допустимые типы задач: `resume`, `search`;
статусы: `queued`, `running`, `succeeded`, `failed`.
UUID, начальные значения и временные метки задаёт ORM. `updated_at` обновляется
при SQLAlchemy UPDATE; серверных триггеров для прямых SQL-запросов нет.
Профили и результаты пока хранятся в JSONB, их API-схемы появятся на следующих этапах.
Email будет нормализоваться в слое аутентификации на этапе 4.

## Миграции

```bash
uv run alembic upgrade head
uv run alembic current
uv run alembic check
# После изменения моделей:
uv run alembic revision --autogenerate -m "describe schema change"
```

Всегда проверяйте автосгенерированную миграцию перед применением.
Начальная миграция поддерживает `downgrade base`, который удаляет таблицы проекта.
Проверяйте откат на тестовой БД, не на БД с нужными данными.
Таблицы не создаются через `create_all()` при старте приложения.

## Проверки

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

Без `TEST_DATABASE_URL` выполняется smoke-тест API, 4 PostgreSQL-теста пропускаются.
Для всех **5 текущих тестов**, когда PostgreSQL запущен (укажите фактический порт):

```bash
TEST_DATABASE_URL=postgresql+psycopg://jobhelper:jobhelper@localhost:55433/jobhelper uv run pytest
```

Интеграционные тесты создают отдельную случайную схему внутри транзакции и откатывают
её после каждого теста. Требуется право `CREATE` на БД. Проверяются upgrade/downgrade,
работа ORM с JSONB и временными метками, внешние ключи, уникальность, типы/статусы задач
и диапазон score. Заявленные 25 тестов будут набраны к этапу 13 по мере реализации функций.
