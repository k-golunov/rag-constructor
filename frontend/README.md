# Frontend RAG Constructor

Angular 21.2, TypeScript 5.9, standalone-компоненты и ленивые маршруты. Команды из `frontend`:

```powershell
npm.cmd install --legacy-peer-deps
npm.cmd start
npm.cmd test -- --run
npm.cmd run build
```

Dev server: `http://localhost:4200`. Сборка: `dist/frontend/browser`. `npm test` запускает Vitest в режиме наблюдения; `--run` выполняет один прогон. E2E-набор не настроен. Production-сборка загружает Google Fonts, поэтому требует доступа к сети.

| Маршрут | Назначение |
| --- | --- |
| `/` | Главная страница |
| `/projects` | Список проектов |
| `/projects/new` | Создание проекта |
| `/projects/:id` | Настройки, документы, загрузка и чат |

Сервисы находятся в `src/app/services`, DTO — в `src/app/models`. На странице проекта доступны выбор/перетаскивание файла и polling статуса ZIP каждые две секунды. В выборе файла разрешён `.md`, однако backend его не поддерживает. Для одиночной загрузки текущий интерфейс показывает успешную карточку при HTTP 200, не проверяя поле `status`; действительный результат смотрите в списке источников.

## Адрес API

Сервисы импортируют `src/environments/environment.ts`, где указан `http://127.0.0.1:8000/api/v1/public/provider`. В `environment.prod.ts` задан относительный `/api/v1/public/provider`, но `angular.json` не содержит `fileReplacements`: production-сборка автоматически его не выбирает.

Для размещения за nginx настройте выбор production-окружения или адрес в используемом файле и пересоберите приложение. Это настройка времени сборки, переменная уже запущенного контейнера её не изменяет. Открывайте локальный frontend через `localhost:4200`: CORS backend разрешает именно этот origin.

В Dockerfile остаётся `npm run build --prod`; команду контейнерной сборки следует согласовать с явным `npm run build -- --configuration production` и проверить весь образ. В рамках анализа Dockerfile не изменялся.

Два теста `app.spec.ts` проверяют локальный упрощённый класс, а не настоящий Angular-компонент. Конфигурации `vitest.config.ts` и `.js` расходятся: первая содержит только комментарий. Подробности и результаты: [проверки](../docs/testing.md), [анализ](../docs/project-status.md).
