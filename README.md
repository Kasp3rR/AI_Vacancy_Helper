# AI Vacancy Helper

Backend ИИ-помощника по подбору вакансий на hh.ru.
Реализованы этапы **1–6**: каркас FastAPI, PostgreSQL/Redis, SQLAlchemy/Alembic,
JWT-аутентификация, загрузка и извлечение текста PDF, анализ профиля через Ollama/Qwen3-4B.

Обработка PDF и вызов модели пока синхронные. Celery, статусы фоновых задач,
hh.ru, кэширование и ранжирование появятся в следующих этапах.

## Стек текущего этапа

Python 3.12, FastAPI, Uvicorn, Pydantic 2, pydantic-settings, PostgreSQL 16,
SQLAlchemy 2, Alembic, psycopg 3, Redis 7, PyJWT, pwdlib/Argon2, PyMuPDF,
HTTPX, Tenacity, Ollama, uv, Ruff, mypy, pytest, pre-commit.
Версии Python-зависимостей зафиксированы в `uv.lock`.

## Быстрый запуск в Docker

Требуется Docker с Compose v2.

```bash
cp .env.example .env
# Сгенерируйте секрет: openssl rand -hex 32
# Запишите результат в JWT_SECRET в .env. При необходимости измените порты.
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

Данные сохраняются в именованных томах `postgres_data`, `redis_data`, `uploads`
и, при использовании профиля `llm`, `ollama_data`.
`docker compose down -v` удаляет эти данные, поэтому не используйте его для обычной остановки.

## Локальная разработка

Установите [uv](https://docs.astral.sh/uv/getting-started/installation/) и Docker.

```bash
cp .env.example .env
# Заполните JWT_SECRET результатом openssl rand -hex 32
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
  api/          — auth, resumes и зависимости авторизации
  security.py   — Argon2 и JWT
  schemas.py    — схемы запросов, ответов и профиля
  services/     — извлечение текста PDF и клиент Ollama
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
Профили и результаты хранятся в JSONB; профиль проверяется схемой Pydantic.
Email нормализуется до нижнего регистра перед регистрацией и входом.

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

Без `TEST_DATABASE_URL` выполняются 12 тестов API health, PDF и Ollama,
13 PostgreSQL-тестов пропускаются. Для всех **25 текущих тестов**, когда PostgreSQL
запущен (укажите фактический порт):

```bash
TEST_DATABASE_URL=postgresql+psycopg://jobhelper:jobhelper@localhost:55433/jobhelper uv run pytest
```

Интеграционные тесты используют отдельные случайные схемы, которые удаляются либо
откатываются после проверки; API-тесты используют реальные коммиты внутри своей схемы.
Требуется право `CREATE` на БД. Проверяются миграции и ограничения,
JWT, конкурентная ротация refresh-токенов, отзыв доступа, изоляция пользователей,
загрузка PDF, очистка файлов при ошибке БД и обработка ошибок модели.
В автотестах ответы Ollama подменяются через respx — модель скачивать не требуется.
PyMuPDF может выдавать DeprecationWarning от SWIG на Python 3.12; это предупреждения
зависимости, не ошибки тестов. При реализации дальнейших этапов набор тестов расширяется.


## JWT-аутентификация

`JWT_SECRET` обязателен, минимум 32 символа. Используйте случайное значение;
не передавайте `.env` в Git. JWT подписываются HS256, проверяются issuer, audience,
тип токена, срок действия и идентификаторы. Пароли (10–128 символов) хешируются Argon2id.

| Метод и путь | Тело / результат |
|---|---|
| `POST /auth/register` | JSON `email`, `password`; 201 и пара токенов |
| `POST /auth/login` | JSON `email`, `password`; 200 и пара токенов |
| `POST /auth/refresh` | JSON `refresh_token`; новая пара токенов |
| `POST /auth/logout` | JSON `refresh_token`; 204 |
| `GET /auth/me` | Bearer access-токен; ID, email, дата регистрации |

Access-токен действует 15 минут, refresh — 7 дней (`ACCESS_MINUTES`, `REFRESH_DAYS`).
Refresh используется однократно: конкурентные запросы обновления дают один успех.
При ротации старые access и refresh отзываются; клиент должен сохранить оба новых токена.
Выход отзывает предъявленный refresh и связанный access, другие входы пользователя
сохраняют доступ. Отзыв проверяется в БД при каждом защищённом запросе.
Повторный выход с уже отозванным, но ещё не истёкшим refresh возвращает 204.
Сами refresh-токены в БД не хранятся — только JTI и сведения об отзыве.
Ответы с токенами запрещают кэширование; ошибки валидации не возвращают введённые пароли.

## PDF и извлечение профиля

| Метод и путь | Назначение |
|---|---|
| `POST /resumes` | Multipart поле `file`: проверка PDF, сохранение; 201 |
| `GET /resumes/{id}` | Данные собственного резюме и профиль, если анализ выполнен |
| `POST /resumes/{id}/analyze` | Синхронный анализ PDF через Qwen3-4B и сохранение профиля |

Для всех запросов нужен заголовок `Authorization: Bearer <access_token>`.
Чужие и отсутствующие резюме возвращают 404. Путь к файлу и исходный текст в API не выдаются.
Имя файла хранения генерируется из UUID, пользовательское имя не влияет на путь.
PDF сохраняются в `UPLOAD_DIR`, в Compose — в отдельном томе `/app/uploads`.

По умолчанию: 10 МиБ (`MAX_PDF_BYTES`), 50 страниц (`MAX_PDF_PAGES`),
40 000 символов извлечённого текста (`MAX_TEXT_CHARS`). Превышение — 413,
неподдерживаемый MIME — 415, повреждённый PDF, шифрование или отсутствие текста — 422.
Сканы без текстового слоя требуют OCR, который в этот этап не входит.
Проверяется содержимое PDF, а не только расширение. Лимит файла проверяется после
multipart-разбора; при внешнем развёртывании задайте также лимит тела запроса в reverse proxy.

Пример после регистрации и получения access-токена:

```bash
curl -X POST http://localhost:8000/resumes \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -F "file=@resume.pdf;type=application/pdf"
curl -X POST "http://localhost:8000/resumes/$RESUME_ID/analyze" \
  -H "Authorization: Bearer $ACCESS_TOKEN"
```

Профиль: `title`, `skills`, `keywords`, `experience_years`, `salary`, `currency`, `region`.
Неизвестные скалярные значения — `null`, списки — `[]`. Навыки и ключевые слова
нормализуются по регистру, дубликаты удаляются. Модель получает JSON Schema,
а её ответ повторно проверяется Pydantic; лишние поля и неверные типы отклоняются.
Схема валидирует структуру, но не гарантирует фактическую точность извлечения моделью.
При неуспешном повторном анализе существующий профиль сохраняется.

## Ollama и Qwen3-4B

Ollama не запускается при обычном `docker compose up` — он выделен в профиль `llm`:

```bash
docker compose --profile llm up -d --wait ollama
docker compose exec ollama ollama pull qwen3:4b-instruct
```

Скачивание модели требует около 2.5 ГБ диска. Первый анализ может быть медленнее
из-за загрузки модели. В контейнере без GPU используется CPU.

Для нативного Ollama (например, на macOS):

```bash
ollama serve
# В другом терминале:
ollama pull qwen3:4b-instruct
```

Локальный Python использует `OLLAMA_URL=http://localhost:11434`.
API в Compose по умолчанию обращается к `http://ollama:11434`.
Для нативного Ollama на macOS задайте в `.env`
`OLLAMA_DOCKER_URL=http://host.docker.internal:11434` и пересоздайте API:
`docker compose up -d api`.

Модель задаётся через `OLLAMA_MODEL=qwen3:4b-instruct`.
Таймаут ответа — `OLLAMA_TIMEOUT_SECONDS=180` секунд. Таймаут возвращается как 504,
недоступный сервис/модель — 503, невалидный или незавершённый ответ — 502.
Ошибки соединения и HTTP 429/502/503/504 повторяются до трёх попыток с задержкой;
таймауты и невалидные ответы не повторяются. Системные HTTP-прокси
для передачи текста резюме не используются. При отсутствии Ollama загрузка PDF
и аутентификация продолжают работать, ошибка появляется только при анализе.

Интеграция следует [документации structured outputs](https://docs.ollama.com/capabilities/structured-outputs)
и использует [qwen3:4b-instruct](https://ollama.com/library/qwen3:4b-instruct).
