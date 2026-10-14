# AI Vacancy Helper

Backend ИИ-помощника по подбору вакансий на hh.ru.
Реализованы этапы **1–9**: каркас FastAPI, PostgreSQL/Redis, SQLAlchemy/Alembic,
JWT-аутентификация, фоновая обработка PDF через Celery и Ollama/Qwen3-4B,
API статусов задач и клиент hh.ru.

Кэширование hh.ru, фоновый подбор вакансий и ранжирование появятся в этапах 10–12.

## Стек текущего этапа

Python 3.12, FastAPI, Uvicorn, Pydantic 2, pydantic-settings, PostgreSQL 16,
SQLAlchemy 2, Alembic, psycopg 3, Redis 7, PyJWT, pwdlib/Argon2, PyMuPDF,
HTTPX, Tenacity, Ollama, Celery 5, uv, Ruff, mypy, pytest, pre-commit.
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
применяет миграции отдельным одноразовым сервисом `migrate`, затем запускает API, worker и beat.
`migrate` со статусом `Exited (0)` — нормальный результат успешной миграции.
API работает от непривилегированного пользователя контейнера.

`/health` — проверка работоспособности процесса API (liveness). Она не проверяет
доступность PostgreSQL и Redis; у них отдельные healthcheck в Compose.

```bash
docker compose ps -a
docker compose logs api worker beat migrate
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
  celery_app.py — broker, result backend и расписание восстановления
  tasks.py      — задачи Celery
  services/     — PDF, Ollama, hh.ru, обработка и восстановление задач
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

Без `TEST_DATABASE_URL` выполняются 18 тестов API health, PDF, Ollama и hh.ru,
19 PostgreSQL-тестов пропускаются. Для всех **37 текущих тестов**, когда PostgreSQL
запущен (укажите фактический порт):

```bash
TEST_DATABASE_URL=postgresql+psycopg://jobhelper:jobhelper@localhost:55433/jobhelper uv run pytest
```

Интеграционные тесты используют отдельные случайные схемы, которые удаляются либо
откатываются после проверки; API-тесты используют реальные коммиты внутри своей схемы.
Требуется право `CREATE` на БД. Проверяются миграции и ограничения,
JWT, конкурентная ротация refresh-токенов, отзыв доступа, изоляция пользователей,
загрузка PDF, очистка файлов при ошибке БД и обработка ошибок модели.
Проверяются также конкурентный запуск и повторная доставка задач, восстановление
публикации после ошибки брокера, защита от записи результата устаревшим worker
и пагинация/retry hh.ru. В автотестах ответы Ollama и hh.ru подменяются через respx,
публикация задач — через mock; Redis и модель для pytest не требуются.
Работа настоящего Celery/Redis проверяется отдельно в Docker.
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
| `POST /resumes` | Multipart `file`: сохранение PDF и постановка анализа; 202, объект `{resume, job}` |
| `GET /resumes/{id}` | Данные собственного резюме и профиль, если анализ выполнен |
| `POST /resumes/{id}/analyze` | Повторный запуск анализа; 202, объект задачи |
| `GET /jobs/{id}` | Статус собственной задачи и безопасное описание ошибки |

Для всех запросов нужен заголовок `Authorization: Bearer <access_token>`.
Чужие и отсутствующие резюме возвращают 404. Путь к файлу и исходный текст в API не выдаются.
Имя файла хранения генерируется из UUID, пользовательское имя не влияет на путь.
PDF сохраняются в `UPLOAD_DIR`, в Compose — в отдельном томе `/app/uploads`.

По умолчанию: 10 МиБ (`MAX_PDF_BYTES`), 50 страниц (`MAX_PDF_PAGES`),
40 000 символов извлечённого текста (`MAX_TEXT_CHARS`). API проверяет размер файла
(превышение — 413), MIME (415) и сигнатуру PDF (422). Проверка структуры, числа
страниц, извлечение текста и вызов модели выполняются только в worker.
Повреждённый/зашифрованный PDF, превышение лимитов страниц/текста или пустой текст
переводят задачу в `failed`; причина доступна через `GET /jobs/{id}`.
Сканы без текстового слоя требуют OCR, который в этот этап не входит.
Проверяется содержимое PDF, а не только расширение. Лимит файла проверяется после
multipart-разбора; при внешнем развёртывании задайте также лимит тела запроса в reverse proxy.

Пример после регистрации и получения access-токена:

```bash
curl -X POST http://localhost:8000/resumes \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -F "file=@resume.pdf;type=application/pdf"
curl "http://localhost:8000/jobs/$JOB_ID" \
  -H "Authorization: Bearer $ACCESS_TOKEN"
# Когда status = succeeded:
curl "http://localhost:8000/resumes/$RESUME_ID" \
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
Worker в Compose по умолчанию обращается к `http://ollama:11434`.
Для нативного Ollama на macOS задайте в `.env`
`OLLAMA_DOCKER_URL=http://host.docker.internal:11434` и пересоздайте сервисы:
`docker compose up -d api worker beat`.

Модель задаётся через `OLLAMA_MODEL=qwen3:4b-instruct`.
Таймаут ответа — `OLLAMA_TIMEOUT_SECONDS=180` секунд. Таймаут, недоступность модели
или невалидный/незавершённый ответ переводят задачу в `failed` с описанием в `error`.
Ошибки соединения и HTTP 429/502/503/504 повторяются до трёх попыток с задержкой;
таймауты и невалидные ответы не повторяются. Системные HTTP-прокси
для передачи текста резюме не используются. При отсутствии Ollama загрузка PDF
и аутентификация продолжают работать, ошибка появляется только при анализе.

Интеграция следует [документации structured outputs](https://docs.ollama.com/capabilities/structured-outputs)
и использует [qwen3:4b-instruct](https://ollama.com/library/qwen3:4b-instruct).


## Celery, статусы и восстановление

Redis разделён по базам: `/0` зарезервирована для кэша, `/1` — broker,
`/2` — result backend. Настройки: `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`.
API и worker должны использовать одну БД и один каталог PDF; в Compose общий том `uploads`.
Если запускаете API и worker локально, используйте один абсолютный `UPLOAD_DIR`.

Для локального запуска дополнительно нужны два терминала:

```bash
uv run celery -A app.celery_app:celery_app worker --loglevel=INFO --concurrency=1
uv run celery -A app.celery_app:celery_app beat --loglevel=INFO --schedule=/tmp/jobhelper-celerybeat
```

На macOS для проверки worker можно использовать `--pool=solo`; полноценные hard/soft
лимиты проверяются с prefork worker в Linux-контейнере. Запускайте один экземпляр beat.

Загрузка атомарно создаёт в PostgreSQL резюме и задачу `queued`, затем публикует
сообщение с её UUID в Celery. Идентификатор Celery совпадает с ID записи в `jobs`.
Сообщение содержит только ID, без текста резюме. Переходы: `queued → running → succeeded/failed`.
Результат профиля и `succeeded` сохраняются одной транзакцией.
Источник статусов API — PostgreSQL; result backend Celery не используется для авторизации
или выдачи профиля и очищает результаты через сутки.

Для одного резюме допускается одна активная задача анализа: повторный или параллельный
`POST /resumes/{id}/analyze` возвращает её ID. После завершения или ошибки этот endpoint
создаёт новую задачу. Повторная доставка сообщения не запускает повторный анализ:
worker атомарно захватывает только `queued`. Старый профиль сохраняется до успешного нового анализа.

Если Redis временно недоступен или API завершился между коммитом БД и отправкой сообщения,
задача остаётся в `queued`; API после сохранения возвращает 202. Beat каждые 30 секунд
публикует служебную задачу восстановления. Она повторно отправляет до 100 старых `queued`
задач за проход. Успешная публикация обновляет время попытки. Для восстановления должны
работать Redis, beat и хотя бы один worker. Восстановление может задерживаться, пока
единственный worker занят анализом. Без worker задача остаётся `queued`, а не считается успешной.

Soft limit обработки — `3 × OLLAMA_TIMEOUT_SECONDS + 60`, hard limit — ещё 30 секунд.
Задачи `running`, не завершённые к hard limit плюс 60 секунд, восстановление переводит
в `failed`. При стандартных настройках это 690 секунд с начала выполнения.
Завершившийся с опозданием worker не может перезаписать профиль такой задачи.
Для повторного анализа вызовите `/resumes/{id}/analyze`.
Неустранимые ошибки PDF и ошибки модели сохраняются в статусе; автоматический перезапуск
всего анализа не выполняется, повторные HTTP-попытки ограничены клиентом Ollama.

Миграция `80c15d7e8933` добавляет частичный уникальный индекс для активных задач.
Она не меняет имеющиеся профили. При ручном заполнении БД до миграции убедитесь,
что нет нескольких `queued/running` задач одного типа для одного резюме.

## Клиент hh.ru

`app/services/hh.py` содержит синхронный HTTPX-клиент для следующего этапа подбора:

```python
from app.config import get_settings
from app.services.hh import HHClient, HHSearch

client = HHClient(get_settings())
query = HHSearch(text="Python developer", area="1", per_page=20)
page = client.search(query)
vacancies = client.search_all(query, max_pages=3)
if vacancies:
    details = client.vacancy(vacancies[0].id)
```

Поддерживаются текст, регион, зарплата, валюта и опыт. Максимум 100 элементов на страницу,
глубина поиска ограничена 2000 результатами; `search_all` останавливается на последней
или пустой странице и удаляет дубликаты ID. Полная карточка загружается отдельно:
поисковая выдача может не содержать `key_skills` и описания. Отсутствующая зарплата и
другие необязательные поля сохраняются как `None`.

Задайте `HH_USER_AGENT` с названием приложения и контактной почтой разработчика
(пример в `.env.example`); клиент отправляет `HH-User-Agent` и `User-Agent`.
`HH_TIMEOUT_SECONDS=10` задаёт таймаут. Для транспортных ошибок, HTTP 429 и 5xx выполняется
до трёх попыток. `Retry-After` поддерживается в секундах и в формате HTTP-date,
задержка ограничена 10 секундами. HTTP 400/403/404 не повторяются; ошибки и невалидный JSON
преобразуются в `HHRequestRejected`, `HHNotFound`, `HHUnavailable`, `HHInvalidResponse`.

Кэш, API запуска подбора и сохранение рекомендаций пока не реализованы — это этапы 10–12.
Контракт клиента сверялся с [официальной документацией hh.ru](https://api.hh.ru/openapi/redoc).
