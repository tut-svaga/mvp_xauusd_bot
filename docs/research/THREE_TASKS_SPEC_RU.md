# Спецификация трёх расчётных задач XAUUSD

Документ фиксирует поведение исходных реализаций на момент архитектурного
рефакторинга. Это описание фактического кода, а не обещание статистической
значимости или торговой доходности.

## Общая схема

```text
data/economic_data/**/*.csv ──┬── multi_event_reaction_analyzer.py
                              └── stage_c_xauusd_analyzer_corrected.py
XAUUSD M1 BID + ASK ──────────┬── multi-event
                              └── Stage C
Stage C event CSV + manifest ─── stage_d_weighted_forecast.py
                                      ├── walk-forward evaluation
                                      ├── report weights / joint model
                                      └── prediction from Actual + Forecast
```

Discovery инвентаризирует доступные источники, но три расчётные задачи читают
данные самостоятельно. Multi-event и Stage C независимы. Stage D зависит от
артефактов Stage C и не загружает котировки.

## 1. Multi-event reaction analyzer

**Исходник:** `multi_event_reaction_analyzer.py`

**Назначение:** описательная историческая статистика по каждой валюте и каждому
экономическому отчёту, найденному в `data/economic_data`.

### Входные данные

- Рекурсивно читает CSV из `--economic-data`.
- Исключает служебные файлы: event_catalog, skipped_events, event_reactions,
  group_stats, run_summary и forex_factory_cache.
- Forex Factory live cache намеренно не используется.
- BID и ASK читаются из отдельных корней `--bid-root` и `--ask-root`;
  внутри каждого корня ожидается `YYYY/MM/XAUUSD_M1_<SIDE>_YYYY-MM-DD.csv`.
- Наивные event timestamps по умолчанию трактуются как UTC
  (`--naive-timezone UTC`); можно задать IANA timezone или `skip`.
  Midnight timestamp пропускается, если явно не задан `--allow-midnight`.

### Подготовка событий

- Поддерживаются datetime_utc, datetime_local, DateTime и date+time;
  приоритет именно в таком порядке.
- Значения Actual, Forecast, Previous разбираются как числа, в том числе
  проценты, K/M/B/T и отрицательные значения в скобках.
- Surprise = Actual − Forecast. Положительный surprise = beat, отрицательный
  = miss, равный нулю = inline.
- Дедупликация выполняется внутри report ID по timestamp, currency, Actual и
  Forecast. Report ID — относительный путь файла без расширения.
- Previous revision = текущий Previous − Actual предыдущего релиза этого отчёта.
- События с отсутствующим/неразбираемым временем попадают в skipped_events.csv.

### Котировки и реакция

- BID/ASK M1 загружаются отдельными LRU-кешами по дням.
- Наивный timestamp котировки считается UTC; секунды отбрасываются.
- Базовая цена — midpoint закрытия M1 свечи за минуту до времени публикации:
  (BID close + ASK close) / 2.
- M1, M5 и M15 свечи рассчитываются из полных M1 BID/ASK баров; отсутствующие
  минуты не интерполируются.
- Для M1 горизонты: 1, 5, 15, 30, 60, 120 минут. Для M5: 5, 15, 30, 60,
  120 минут. Для M15: 15, 30, 60, 120 минут.
- Публикация должна быть выровнена по границе соответствующего таймфрейма.
- Метрики: cumulative return от pre-release midpoint, candle body return,
  range, upper/lower wick. Также сохраняются BID/ASK OHLC и объём.
- Одновременные релизы на одной минуте помечаются co_release_count и
  is_isolated_release. Это маркировка смешанного воздействия, не причинное
  разделение влияния отчётов.

### Сценарии и статистика

- Для каждого отчёта beat и miss независимо делятся на small/medium/large по
  q33 и q67 абсолютного surprise; inline образует отдельный класс.
- Критически важно: текущие q33/q67 вычисляются по всей доступной истории
  данного отчёта, включая поздние наблюдения. Это допустимо для описательной
  ретроспективной сегментации, но не для честной OOS-модели без пересчёта
  порогов только на train-fold.
- Периоды: 2007–2012, 2013–2019, 2020–2022, 2023–2026; основной акцент
  отчётов — 2023–2026, при этом сохраняется all-history и старые периоды.
- Flat threshold — 0.5 стандартного отклонения реакции, pooled по уникальным
  timestamp для timeframe/horizon/metric/period.
- Вероятности — описательные частоты; Wilson interval используется для
  интервалов. Порог надёжности в отчёте: N>=15 reliable, 10–14 preliminary,
  1–9 reference, 0 empty.

### Артефакты

- `event_reactions.csv` — одна строка на событие, метрики по таймфреймам.
- `group_stats.csv` — агрегаты по группам и горизонтам.
- `derived_event_candles.csv` — сформированные свечи событий.
- `scenario_templates.json` — шаблоны по отчётам.
- `skipped_events.csv` — пропущенные строки и причины.
- `reports/**/*.txt` — человекочитаемые отчёты.
- `run_summary.json` — объёмы входа, покрытие котировками и перечень outputs.

### CLI

`--economic-data`, `--bid-root`, `--ask-root`, `--output` обязательны.
Дополнительно: `--naive-timezone`, `--allow-midnight`,
`--price-cache-days` (минимум 2).

## 2. Stage C corrected

**Исходник:** `stage_c_xauusd_analyzer_corrected.py`

**Назначение:** независимый анализ восьми выбранных американских макроотчётов
для фиксированного времени публикации.

### Набор отчётов

1. Core PCE Price Index m/m
2. Prelim GDP q/q
3. Prelim GDP Price Index q/q
4. Core Durable Goods Orders m/m
5. Durable Goods Orders m/m
6. Personal Income m/m
7. Personal Spending m/m
8. Unemployment Claims

Пути CSV заданы явно в списке REPORTS в исходнике.

### Время

- Для каждого календарного дня время исходной строки DateTime игнорируется.
- Дата берётся из календарного источника; время публикации реконструируется
  как 08:30 в `America/New_York` (или параметры CLI).
- ZoneInfo применяет DST по правилам IANA timezone database.
- Результат нормализуется в UTC.
- Это специальное правило только для восьми отчётов Stage C; его нельзя
  автоматически переносить на остальные события.

### Числовая классификация и цены

- Actual и Forecast разбираются численно; result_group:
  below_forecast, inline, above_forecast.
- Inline определяется абсолютной разницей не больше
  `--equal-tolerance` (по умолчанию 0).
- Котировки Dukascopy имеют timestamp начала M1 свечи; close time =
  timestamp + 1 минута.
- BID и ASK объединяются только по точному совпадению close timestamp.
  Midpoint = (BID close + ASK close) / 2; spread = ASK − BID.
- Базовая цена — последняя завершённая M1 close не позже release timestamp.
- Горизонты 5 и 15 минут — последняя завершённая M1 close не позже
  release timestamp + N минут.
- Quote принимается только если его возраст относительно целевого времени
  не превышает `--max-gap-minutes` (по умолчанию 2 минуты).
- Изменение = target midpoint − baseline midpoint; направление up/down/flat.

### Статистика и outputs

Для каждой группы и горизонта считаются N, число up/down/flat, вероятности,
mean, median, mean absolute change, q25/q75. Отдельно сохраняются события без
полного quote результата, чтобы можно было диагностировать покрытие.

- `reports/<slug>_events.csv`
- `statistics/<slug>_group_stats.csv`
- `templates/<slug>.json`
- `stage_c_all_group_stats.csv`
- `stage_c_templates.json`
- `stage_c_run_manifest.json` — пути, настройки, временное правило, quote
  semantics, параметры tolerance/gap и ссылки на outputs.

Manifest — контракт для Stage D. Не редактировать event CSV вручную без
понимания, что Stage D проверяет manifest и повторно классифицирует
Actual/Forecast при наличии numeric полей.

### CLI

`--calendar-root`, `--quotes-root`, `--output-dir`,
`--naive-event-timezone`, `--release-timezone`, `--release-time`,
`--max-gap-minutes`, `--equal-tolerance`, `--debug`.

## 3. Stage D weighted forecast

**Исходник:** `stage_d_weighted_forecast.py`

**Назначение:** построить модель на исторических результатах Stage C, оценить
её хронологически out-of-sample и применить к новым Actual/Forecast.

### Входной контракт

- Каталог Stage C должен содержать `stage_c_run_manifest.json` и
  `reports/<slug>_events.csv`.
- По умолчанию manifest должен указывать правило
  `calendar date + scheduled release time`; иначе запуск запрещён.
- Ожидаемое локальное время — 08:30 America/New_York. Если более 2% событий
  конкретного отчёта имеют иное время, валидация останавливает сборку модели.
- Для каждого отчёта обязательны event_time_utc и result_group.
- При наличии actual_numeric/forecast_numeric result_group пересчитывается
  по inline tolerance из Stage C manifest.
- Направление берётся из m5/m15_direction; если его нет, восстанавливается из
  change_usd.
- Дубликаты одного отчёта на одном UTC timestamp сводятся к одной записи
  (последняя после сортировки по source_row, если поле есть).

### Отчётные модели

- Классы направления: up, down, flat.
- Базовые вероятности сглаживаются Dirichlet/Laplace prior (по умолчанию 1).
- Для каждой группы surprise вероятности сглаживаются к unconditional
  базовому распределению с alpha=8.
- Walk-forward для каждого отчёта — expanding window в хронологическом
  порядке; train содержит только строки до test события.
- Минимальная история для веса: 30 событий; минимум OOS: 8.
- Качество оценивается multiclass Brier score и Brier Skill Score:
  BSS = 1 − Brier(model) / Brier(baseline).
- Отрицательный/нулевой skill не даёт положительный вес; если ни один
  доступный отчёт не имеет положительного веса, используется equal-weight
  fallback с явным предупреждением.

### Корреляция и совместная модель

- Сигналы отчётов сопоставляются по совпадающим OOS timestamps; для пары
  требуется не менее 10 общих точек для корреляции.
- Joint dataset объединяет отчёты одного timestamp. Итоговое направление
  при нескольких строках выбирается по median price change (или majority
  direction, если нет числовых изменений).
- Joint softmax использует три бинарных признака на отчёт: below, above,
  missing; inline — reference (все нули для отчёта).
- Ridge regularization JOINT_L2=3.0; 3-class softmax обучается градиентным
  спуском.
- Совместная модель используется только при достаточной истории, OOS,
  покрытии совпадающих релизов и положительном BSS; иначе применяется
  смесь отчётных вероятностей.
- Важно: текущий код обучает joint-модель на expanding chronological blocks.
  Пороговые значения и минимумы зафиксированы константами в Stage D.

### Прогноз

Вход JSON содержит доступные Actual/Forecast значения по slug отчёта.
Прогнозы формируются отдельно для 5 и 15 минут: P(up/down/flat), ожидаемое
изменение USD, средний абсолютный ход, взвешенная медиана, эффективная
историческая поддержка, использованный метод и вклад каждого отчёта.
Если доступны не все 8 пар Actual/Forecast, модель использует полученные
отчёты и добавляет warning. Equal-weight fallback также явно помечается.
Модель не предсказывает финальный release-specific результат без Actual и
Forecast.

### Артефакты

- `stage_d_model.json` — сериализованная модель и диагностика.
- `stage_d_report_weights.csv`
- `stage_d_signal_correlations_5m.csv`, `stage_d_signal_correlations_15m.csv`
- `stage_d_walk_forward_predictions.csv`
- `stage_d_summary.txt`
- при `--input-json`: `stage_d_prediction.json` и
  `stage_d_telegram_message.html`.

### CLI

Обязательны `--stage-c-dir` и `--output-dir`. Опциональны `--input-json`
и диагностический `--allow-time-mismatch`. Последний отключает защиту времени
и не должен применяться для production-модели.

## Архитектурные границы и следующие рефакторинги

- `discovery/` — инвентаризация и структурные проверки источников.
- `tasks/` — стабильные точки запуска и импорта трёх существующих задач.
- `validation/` — следующий слой: event-level eligibility, timestamp/DST,
  duplicates, quote coverage, overlap/confounding. Не смешивать с Discovery.
- `processing/` — единая нормализация событий/котировок и контракт UTC.
- `event_analysis/` — расчёт реакций; разделить multi-event и Stage C
  профили, не сводя их разные baseline/time semantics к одной формуле.
- `statistics/` — общие функции статистики после тестирования эквивалентности.
- `model_evaluation/` — Stage D walk-forward, Brier, calibration, leakage
  checks.
- `reporting/` — общий индекс/manifest запусков и форматы отчётов.

На текущем шаге вычислительные движки намеренно сохранены без переноса
функций между файлами: сначала введены единый интерфейс и точная спецификация.
Физическое разбиение монолитов выполняется отдельными небольшими изменениями
с golden tests на реальных сохранённых артефактах, чтобы не изменить расчёты
непреднамеренно.
