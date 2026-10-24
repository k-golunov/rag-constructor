# API

Локальная база: `http://127.0.0.1:8000/api/v1/public/provider`. Пути ниже относительно неё. Полные схемы: `/docs` и `/openapi.json` в корне backend. `/health` находится в корне и возвращает `{"status":"ok"}` без проверки зависимостей. Авторизации нет.

| Метод | Путь | Результат |
| --- | --- | --- |
| POST | `/projects/` | 201, проект |
| GET | `/projects/?skip=0&limit=50` | 200, `{total, items}` |
| GET | `/projects/{project_id}` | 200, проект |
| PATCH | `/projects/{project_id}` | 200, обновлённый проект |
| DELETE | `/projects/{project_id}` | 204, удаление проекта и связанных SQL-записей |
| GET | `/projects/{project_id}/data-sources` | 200, `{total, items}`, без пагинации |
| DELETE | `/data-sources/{data_source_id}` | 204, удаление SQL-источника |
| POST | `/upload/single?project_id={uuid}` | 200, результат обработки |
| POST | `/upload/archive?project_id={uuid}` | 202, `{operation_id, message}` |
| GET | `/upload/status/{operation_id}` | 200, статус операции ZIP |
| POST | `/projects/{project_id}/chat` | 200, `{answer, session_id, sources}` |
| GET | `/projects/{project_id}/chat/{session_id}` | 200, `{session_id, project_id, messages}` |
| POST | `/projects/{project_id}/chat/generate` | 200, `{answer}` |

Неизвестный проект в CRUD/загрузке/генерации, неизвестный источник при удалении и неизвестная операция возвращают 404. Список источников и история не проверяют существование проекта отдельно и могут вернуть пустой список. Ошибки схем обычно дают 422 с `detail`.

## Создание проекта

Обязательны `name`, `embedding_model`, `llm_model`:

```json
{
  "name": "База знаний",
  "chunk_size": 800,
  "chunk_overlap": 100,
  "embedding_model": "text-embedding-3-small",
  "embedding_dimension": 1536,
  "llm_model": "gpt-4o-mini",
  "system_prompt": "Отвечай по предоставленным документам."
}
```

Имена моделей — примеры из исходников, их доступность определяется провайдером. Настройте действительный общий ключ либо `embedding_api_key`/`llm_api_key` проекта и при необходимости `embedding_api_url`/`llm_api_url`.

`chunk_size`: 100–8000 (по умолчанию 800), `chunk_overlap`: 0–2000 (100), `embedding_dimension`: >= 1 (1536). Соблюдайте `chunk_overlap < chunk_size`: схема не проверяет соотношение, ошибка возникнет при обработке файла.

`split_by`: `paragraphs`, `sentences`, `tokens`; сейчас только сохраняется. `chunking_strategy`: только `recursive`. `extract_tables`: boolean, не управляет PDF-парсером. PATCH меняет переданные поля, переиндексацию не запускает. Ответ проекта включает API-ключи.

## Пример PowerShell

При запущенном backend:

```powershell
$base = 'http://127.0.0.1:8000/api/v1/public/provider'
$body = @{
    name = 'База знаний'
    embedding_model = 'text-embedding-3-small'
    embedding_dimension = 1536
    llm_model = 'gpt-4o-mini'
} | ConvertTo-Json
$project = Invoke-RestMethod -Method Post -Uri "$base/projects/" -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
$projectId = $project.id

# Замените путь существующим PDF, TXT или DOCX. Используйте curl.exe, а не alias curl.
curl.exe -X POST "$base/upload/single?project_id=$projectId" -F 'file=@C:/documents/guide.txt'

$question = @{ question = 'О чём этот документ?' } | ConvertTo-Json
$reply = Invoke-RestMethod -Method Post -Uri "$base/projects/$projectId/chat" -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($question))
Invoke-RestMethod "$base/projects/$projectId/chat/$($reply.session_id)"
```

Перед вопросом убедитесь, что загрузка вернула `status: completed`. Multipart-поле называется `file`, `project_id` передаётся в query. Ответ: `filename`, `project_id`, `data_source_id`, `status`, `chunk_size`, `chunk_overlap`, `chunks_count`. Сбой векторизации может дать HTTP 200 и `status: failed`; подробность — в `error_message` SQL-источника. Неизвестное расширение даёт 400, PDF без текста — 422.

Для продолжения чата передавайте `session_id` из ответа вместе с `question`; для нового не передавайте или задайте `null`. Некорректный UUID сессии при POST возвращает 400. `sources` содержит имена файлов; ответ может прийти без контекста после ошибки поиска.

## ZIP и готовый контекст

Архив передаётся полем `file` на `/upload/archive`. Некорректный ZIP даёт 400. Операция имеет статусы `processing`, `completed`, `failed`:

```json
{
  "operation_id": "uuid",
  "status": "completed",
  "result": {"chunks_count": 12, "skipped_files": ["image.png"]},
  "error": null
}
```

После перезапуска статус пропадает. Файлы ZIP не появляются в SQL-списке источников. Нулевой результат также может иметь статус `completed`.

`/chat/generate` принимает `query` (непустая строка), `context` (по умолчанию пустой), необязательные `history` вида `[{"role":"user","content":"..."}]` и `session_id`. Поиск и сохранение истории не выполняются, `session_id` не используется. Этот путь требует заполненного `llm_api_key` проекта, иначе возвращает 422.
